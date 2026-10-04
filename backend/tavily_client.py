# FILE: backend/tavily_client.py — place at this path in the Culprit repo
# (replaces the Stack-2 stub of the same name; no new dependency needed —
# uses httpx directly, exactly like models.py does for Nebius, so
# requirements.txt does not need a `tavily-python` line added).
"""
tavily_client.py — Tavily grounding step for the Diagnoser.

Spec: docs/build-prompts/BUILD_02_BACKEND_AGENTS.md §1 ("Tavily grounding
step") / docs/AGENT_SPECS.md §2 ("Tavily grounding step (runs after the
above)") / docs/TRD.md §2.2 ("Tavily integration").

Confirmed against Tavily's current public docs (docs.tavily.com) before
writing this, the same way models.py flags its Nebius assumptions:
  - Base URL: https://api.tavily.com : POST /search
  - Auth: `Authorization: Bearer <api_key>` header (current documented
    style — some older SDKs/snippets instead put `api_key` in the JSON
    body; this file uses the header form since that's what Tavily's docs
    show as of this writing).
  - Response shape: {"results": [{"title": ..., "url": ..., "content": ...,
    "score": ...}, ...], "query": ..., "response_time": ...}.
  - Not verified against a live account (no TAVILY_API_KEY in this
    environment) — the request/response shape is read from public docs,
    not exercised against the real API. Make one live call before trusting
    this in the Diagnoser path.

Query-construction judgment call (flagged, not silently picked): both docs
give TWO alternative query patterns —
  "{category} performance issue {relevant library/function name}"  OR
  "is {cited pattern} a known anti-pattern"
— but neither doc says how to programmatically pull "the relevant
library/function name" out of a raw diff line. build_query() below uses a
simple heuristic (the first identifier-looks-like-a-call token found in
cited_lines) and falls back to the second pattern, then to a bare
category-only query, rather than guessing at NLP it can't actually do.
This is good enough to produce a real query for the Tavily bonus-prize
story, but isn't a "confirmed interpretation" of either doc.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass

import httpx
import structlog

from events import Reporter, or_null

log = structlog.get_logger("tavily_client")

_TAVILY_API_URL = "https://api.tavily.com/search"

# Same transient-vs-permanent retry convention as models.py's
# call_nemotron (CODE_REVIEW_FINDINGS.md #20): a dropped connection or
# transient 5xx/429 shouldn't throw away this grounding call's one search
# result on the first blip. Kept shorter than models.py's, since this
# whole step is optional enrichment that already degrades to [] — it's
# not worth making a diagnosis wait long for it.
_DEFAULT_TRANSIENT_RETRY_DELAYS_S: tuple[float, ...] = (0.5, 1.0)
_DEFAULT_TIMEOUT_S = 15.0

# Matches something call-shaped, e.g. "LineItem.objects.filter(" -> captures
# "LineItem.objects.filter". Deliberately simple — see module docstring.
_IDENTIFIER_RE = re.compile(r"([A-Za-z_][A-Za-zA-Z0-9_.]*)\s*\(")


class TavilyError(Exception):
    """Raised only for a caller configuration error (missing API key) —
    reserved for cases the caller should actually notice, unlike a bad
    network call. NOT raised for a failed/empty search: per AGENT_SPECS.md
    §2 / BUILD_02 §1, Tavily is optional grounding, and "if Tavily returns
    nothing relevant, leave tavily_refs empty rather than fabricate
    relevance." diagnoser.py never needs to catch this — search_grounding()
    itself swallows network/HTTP/shape failures and returns [] instead."""


# Tavily scores every result 0..1. AGENT_SPECS.md §2: "If Tavily returns nothing
# relevant, don't force a citation" — so results below this floor are dropped
# rather than shown as if they supported the diagnosis. Results with no score
# (older API shapes, test doubles) are kept: absence of a score isn't evidence
# of irrelevance. Override with TAVILY_MIN_SCORE.
_DEFAULT_MIN_SCORE = float(os.environ.get("TAVILY_MIN_SCORE", "0.2"))


@dataclass
class TavilyRef:
    title: str
    url: str
    # Additive (defaults keep every existing construction site valid): what the
    # source actually says and how relevant Tavily judged it, so the dashboard
    # can show *why* a citation is there rather than a bare link.
    snippet: str = ""
    score: float | None = None


def build_query(category: str, cited_lines: list[str]) -> str:
    """See module docstring for the two documented patterns and the
    identifier-extraction heuristic. Never returns an empty string — the
    category-only fallback always has something to search for."""
    identifier: str | None = None
    for line in cited_lines:
        match = _IDENTIFIER_RE.search(line)
        if match:
            identifier = match.group(1)
            break

    category_readable = category.replace("_", " ")
    if identifier:
        return f"{category_readable} performance issue {identifier}"
    if cited_lines:
        snippet = cited_lines[0].strip()
        if snippet:
            return f"is {snippet} a known anti-pattern"
    return f"{category_readable} performance issue"


def _is_retryable_transient_error(e: httpx.HTTPError) -> bool:
    """Same rule as models.py's call_nemotron: retry connection-level
    failures and 5xx/429; a 4xx (bad key, bad request) will never succeed
    on retry."""
    if isinstance(e, httpx.HTTPStatusError):
        return e.response.status_code >= 500 or e.response.status_code == 429
    return isinstance(e, httpx.RequestError)


def search_grounding(
    category: str,
    cited_lines: list[str],
    *,
    max_results: int = 3,
    api_key: str | None = None,
    client: httpx.Client | None = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    retry_delays_s: tuple[float, ...] = _DEFAULT_TRANSIENT_RETRY_DELAYS_S,
    reporter: Reporter | None = None,
    min_score: float | None = None,
) -> list[TavilyRef]:
    """Runs the grounding search for one Diagnoser result. Returns [] on
    ANY failure — missing key, network error, HTTP error, unexpected
    response shape, or zero results — rather than raising, since this step
    is optional enrichment (AGENTS.md's "don't fabricate" spirit applies
    here too: no key/no results means an honestly empty tavily_refs, never
    a made-up one).

    client: inject an httpx.Client (e.g. with a MockTransport) for testing;
    a real one is created and closed automatically otherwise — same
    convention as models.call_nemotron.
    retry_delays_s: backoff before retrying a TRANSIENT failure (default
    (0.5, 1.0)) — override with () in tests that need to force/observe one
    without actually sleeping. See CODE_REVIEW_FINDINGS.md #20.
    """
    rep = or_null(reporter)
    query = build_query(category, cited_lines)
    floor = _DEFAULT_MIN_SCORE if min_score is None else min_score

    api_key = api_key or os.environ.get("TAVILY_API_KEY")
    if not api_key:
        log.warning("tavily.no_api_key_configured", query=query)
        rep.emit("tavily.skipped", "tavily", "Tavily grounding skipped — TAVILY_API_KEY not set", query=query)
        return []

    if os.environ.get("CULPRIT_OFFLINE") == "1" and client is None:
        # Offline mode never touches the network for grounding either; an honest
        # empty result, not an invented citation.
        rep.emit("tavily.skipped", "tavily", "Tavily grounding skipped — offline mode", query=query)
        return []

    span = rep.start("tavily", "tavily", f"Tavily search: {query}", query=query, max_results=max_results)

    owns_client = client is None
    if owns_client:
        client = httpx.Client()
    try:
        resp = None
        last_error: httpx.HTTPError | None = None
        for attempt_idx, delay_s in enumerate((0.0, *retry_delays_s)):
            if delay_s:
                log.warning(
                    "tavily.transient_error_retry",
                    query=query, error=str(last_error), retry_in_s=delay_s, attempt=attempt_idx + 1,
                )
                time.sleep(delay_s)
            try:
                resp = client.post(
                    _TAVILY_API_URL,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"query": query, "max_results": max_results, "search_depth": "basic"},
                    timeout=timeout_s,
                )
                resp.raise_for_status()
                break
            except httpx.HTTPError as e:
                last_error = e
                resp = None
                if not _is_retryable_transient_error(e):
                    break
        if resp is None:
            log.warning("tavily.call_failed", query=query, error=str(last_error))
            rep.end(span, "Tavily search failed — continuing without grounding", query=query, ok=False,
                    error=str(last_error), n_refs=0)
            return []

        try:
            body = resp.json()
        except ValueError as e:
            log.warning("tavily.bad_response_body", query=query, error=str(e))
            rep.end(span, "Tavily returned an unreadable response", query=query, ok=False, n_refs=0)
            return []

        results = body.get("results")
        if not isinstance(results, list):
            log.warning("tavily.unexpected_response_shape", query=query, body=body)
            rep.end(span, "Tavily response had an unexpected shape", query=query, ok=False, n_refs=0)
            return []

        refs: list[TavilyRef] = []
        dropped = 0
        for r in results:
            if not (
                isinstance(r, dict)
                and isinstance(r.get("title"), str) and r.get("title")
                and isinstance(r.get("url"), str) and r.get("url")
            ):
                continue
            score = r.get("score") if isinstance(r.get("score"), (int, float)) else None
            if score is not None and score < floor:
                dropped += 1
                continue
            content = r.get("content") if isinstance(r.get("content"), str) else ""
            refs.append(TavilyRef(
                title=r["title"], url=r["url"],
                snippet=content.strip()[:400], score=round(float(score), 3) if score is not None else None,
            ))
        log.info("tavily.grounding", query=query, refs_found=len(refs), results_seen=len(results))
        rep.end(
            span,
            (f"{len(refs)} relevant source(s)" + (f" ({dropped} below relevance floor dropped)" if dropped else ""))
            if refs else "no sufficiently relevant sources — leaving citations empty (not forcing one)",
            query=query, ok=True, n_refs=len(refs), n_results=len(results), dropped=dropped, floor=floor,
            refs=[{"title": x.title, "url": x.url, "score": x.score, "snippet": x.snippet} for x in refs],
        )
        return refs
    finally:
        if owns_client:
            client.close()
