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
from dataclasses import dataclass

import httpx
import structlog

log = structlog.get_logger("tavily_client")

_TAVILY_API_URL = "https://api.tavily.com/search"
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


@dataclass
class TavilyRef:
    title: str
    url: str


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


def search_grounding(
    category: str,
    cited_lines: list[str],
    *,
    max_results: int = 3,
    api_key: str | None = None,
    client: httpx.Client | None = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
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
    """
    query = build_query(category, cited_lines)

    api_key = api_key or os.environ.get("TAVILY_API_KEY")
    if not api_key:
        log.warning("tavily.no_api_key_configured", query=query)
        return []

    owns_client = client is None
    if owns_client:
        client = httpx.Client()
    try:
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
        except httpx.HTTPError as e:
            log.warning("tavily.call_failed", query=query, error=str(e))
            return []

        try:
            body = resp.json()
        except ValueError as e:
            log.warning("tavily.bad_response_body", query=query, error=str(e))
            return []

        results = body.get("results")
        if not isinstance(results, list):
            log.warning("tavily.unexpected_response_shape", query=query, body=body)
            return []

        refs = [
            TavilyRef(title=r["title"], url=r["url"])
            for r in results
            if isinstance(r, dict)
            and isinstance(r.get("title"), str) and r.get("title")
            and isinstance(r.get("url"), str) and r.get("url")
        ]
        log.info("tavily.grounding", query=query, refs_found=len(refs), results_seen=len(results))
        return refs
    finally:
        if owns_client:
            client.close()
