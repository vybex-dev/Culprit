# backend/preflight.py
"""
preflight.py — "will a job actually work?" — answered *before* a job is started.

models.py's own docstring is candid that the Nemotron model IDs and API shapes
were "NOT verified against a live account", and Nebius's own docs list several
ID spellings for the same family. Discovering that three minutes into a run —
after the sandbox has already burned credits — is a bad experience, so this
module checks, against the live Token Factory catalog:

  * is an API key present?
  * do the configured Nano / Ultra model IDs exist in the catalog?  (and if
    not, which Nemotron IDs *do*, so the fix is a copy-paste)
  * is the sandbox project configured?
  * is a Tavily key present?
  * is git available?

It never returns a secret, only booleans and public catalog IDs. Failures of
the check itself (network down) are reported as "unknown", never as "ok".
"""

from __future__ import annotations

import os
import shutil
import time
from typing import Any

import httpx

_CACHE: dict[str, Any] = {"at": 0.0, "value": None}
_CACHE_TTL_S = 30.0


def _check(name: str, status: str, detail: str, **extra: Any) -> dict[str, Any]:
    """status: ok | warn | fail | unknown"""
    return {"name": name, "status": status, "detail": detail, **extra}


def list_catalog_models(base_url: str, api_key: str, client: httpx.Client, timeout_s: float = 6.0) -> list[str]:
    resp = client.get(f"{base_url.rstrip('/')}/models", headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout_s)
    resp.raise_for_status()
    body = resp.json()
    items = body.get("data") if isinstance(body, dict) else None
    if not isinstance(items, list):
        raise ValueError("unexpected /models response shape")
    return [m["id"] for m in items if isinstance(m, dict) and isinstance(m.get("id"), str)]


def run_preflight(*, client: httpx.Client | None = None, use_cache: bool = True) -> dict[str, Any]:
    now = time.monotonic()
    if use_cache and client is None and _CACHE["value"] and now - _CACHE["at"] < _CACHE_TTL_S:
        return _CACHE["value"]

    import models  # late import so env changes in tests are respected

    offline = os.environ.get("CULPRIT_OFFLINE") == "1"
    use_tf = os.environ.get("USE_TOKEN_FACTORY") == "1"
    checks: list[dict[str, Any]] = []

    checks.append(
        _check("git", "ok", "git is available") if shutil.which("git")
        else _check("git", "fail", "git was not found on PATH")
    )

    if offline:
        checks.append(_check(
            "models", "warn",
            "Offline mode: Nemotron roles are served by a labelled deterministic stand-in (offline.py). "
            "Benchmarks are still real.",
        ))
        checks.append(_check("sandbox", "ok", "Local git-worktree sandbox (offline mode)"))
        checks.append(_check("tavily", "warn", "Skipped in offline mode"))
        result = _finish(checks, offline=True, use_tf=False)
    else:
        key = os.environ.get("NEBIUS_API_KEY")
        if not key:
            checks.append(_check("api_key", "fail", "NEBIUS_API_KEY is not set"))
        else:
            checks.append(_check("api_key", "ok", "NEBIUS_API_KEY is set"))
            checks.append(_check_models(models, key, client))

        if use_tf:
            project = os.environ.get("CONTREE_PROJECT") or os.environ.get("NEBIUS_AI_PROJECT")
            checks.append(
                _check("sandbox", "ok", "Token Factory Sandboxes enabled; project header configured (not probed)")
                if project else
                _check("sandbox", "fail", "USE_TOKEN_FACTORY=1 but CONTREE_PROJECT (or NEBIUS_AI_PROJECT) is not set")
            )
            if not (os.environ.get("CONTREE_IMAGE")):
                checks.append(_check("sandbox_image", "warn",
                                     "CONTREE_IMAGE is unset — an image will be imported on first use (slower, unverified body shape)"))
        else:
            checks.append(_check(
                "sandbox", "warn",
                "Using the LOCAL sandbox (USE_TOKEN_FACTORY is not 1). Benchmark commands execute on this machine — "
                "fine for development, not for untrusted input, and not the Token Factory story.",
            ))
        checks.append(
            _check("tavily", "ok", "TAVILY_API_KEY is set") if os.environ.get("TAVILY_API_KEY")
            else _check("tavily", "warn", "TAVILY_API_KEY not set — diagnoses will have no grounding citations")
        )
        result = _finish(checks, offline=False, use_tf=use_tf)

    if client is None:
        _CACHE.update(at=now, value=result)
    return result


def _check_models(models_mod: Any, key: str, client: httpx.Client | None) -> dict[str, Any]:
    ids = {"nano": models_mod._MODEL_IDS["nano"], "ultra": models_mod._MODEL_IDS["ultra"]}
    owns = client is None
    c = client or httpx.Client()
    try:
        catalog = list_catalog_models(models_mod._DEFAULT_BASE_URL, key, c)
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        hint = "the API key was rejected" if code in (401, 403) else f"HTTP {code}"
        return _check("models", "fail" if code in (401, 403) else "unknown",
                      f"Could not read the Token Factory model catalog: {hint}", configured=ids)
    except Exception as e:  # noqa: BLE001
        return _check("models", "unknown",
                      f"Could not reach the Token Factory catalog ({type(e).__name__}); model IDs are unverified",
                      configured=ids)
    finally:
        if owns:
            c.close()

    lower = {m.lower(): m for m in catalog}
    missing = {role: mid for role, mid in ids.items() if mid.lower() not in lower}
    nemotron = sorted(m for m in catalog if "nemotron" in m.lower())
    if not missing:
        return _check("models", "ok", "Configured Nano and Ultra model IDs exist in the Token Factory catalog",
                      configured=ids, catalog_matches=nemotron)
    return _check(
        "models", "fail",
        "Configured model ID(s) not found in the catalog: "
        + ", ".join(f"{r}={m}" for r, m in missing.items())
        + ". Set NEMOTRON_NANO_MODEL_ID / NEMOTRON_ULTRA_MODEL_ID to one of the Nemotron IDs listed.",
        configured=ids, missing=missing, catalog_matches=nemotron,
    )


def _finish(checks: list[dict[str, Any]], *, offline: bool, use_tf: bool) -> dict[str, Any]:
    ready = not any(c["status"] == "fail" for c in checks)
    return {
        "ready": ready,
        "mode": "offline" if offline else "live",
        "sandbox_backend": "local" if (offline or not use_tf) else "token_factory",
        "checks": checks,
    }
