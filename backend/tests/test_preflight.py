# backend/tests/test_preflight.py
"""Preflight must tell the truth about config *before* credits are spent — and
never report "ok" for something it couldn't verify."""

import httpx
import pytest

import models
import preflight


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for v in ("CULPRIT_OFFLINE", "USE_TOKEN_FACTORY", "CONTREE_PROJECT", "NEBIUS_AI_PROJECT", "CONTREE_IMAGE", "TAVILY_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    preflight._CACHE.update(at=0.0, value=None)


def _by_name(result):
    return {c["name"]: c for c in result["checks"]}


def test_no_api_key_is_a_failure(monkeypatch):
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    r = preflight.run_preflight(client=_client(lambda req: httpx.Response(200, json={"data": []})))
    assert not r["ready"] and _by_name(r)["api_key"]["status"] == "fail"


def test_configured_models_present_in_catalog_is_ok(monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "k")
    ids = [models._MODEL_IDS["nano"], models._MODEL_IDS["ultra"], "other/model"]
    r = preflight.run_preflight(client=_client(lambda req: httpx.Response(200, json={"data": [{"id": i} for i in ids]})))
    assert _by_name(r)["models"]["status"] == "ok"


def test_wrong_model_id_fails_and_suggests_real_nemotron_ids(monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "k")
    catalog = ["nvidia/nvidia-nemotron-3-nano-30b-a3b", models._MODEL_IDS["ultra"], "meta/llama"]
    r = preflight.run_preflight(client=_client(lambda req: httpx.Response(200, json={"data": [{"id": i} for i in catalog]})))
    m = _by_name(r)["models"]
    assert m["status"] == "fail" and not r["ready"]
    assert "nano" in m["missing"] and "nvidia/nvidia-nemotron-3-nano-30b-a3b" in m["catalog_matches"]
    assert "meta/llama" not in m["catalog_matches"]


def test_unreachable_catalog_is_unknown_not_ok(monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "k")

    def boom(req):
        raise httpx.ConnectError("no network")

    r = preflight.run_preflight(client=_client(boom))
    assert _by_name(r)["models"]["status"] == "unknown"


def test_rejected_key_is_a_failure(monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "bad")
    r = preflight.run_preflight(client=_client(lambda req: httpx.Response(401, json={})))
    assert _by_name(r)["models"]["status"] == "fail" and "rejected" in _by_name(r)["models"]["detail"]


def test_token_factory_requires_project_and_local_sandbox_is_flagged(monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "k")
    ok_catalog = lambda req: httpx.Response(200, json={"data": [{"id": models._MODEL_IDS["nano"]}, {"id": models._MODEL_IDS["ultra"]}]})
    monkeypatch.setenv("USE_TOKEN_FACTORY", "1")
    r = preflight.run_preflight(client=_client(ok_catalog))
    assert _by_name(r)["sandbox"]["status"] == "fail"
    monkeypatch.setenv("NEBIUS_AI_PROJECT", "proj-1")  # the name Nebius's own docs use
    assert _by_name(preflight.run_preflight(client=_client(ok_catalog)))["sandbox"]["status"] == "ok"
    monkeypatch.delenv("USE_TOKEN_FACTORY")
    r = preflight.run_preflight(client=_client(ok_catalog))
    assert _by_name(r)["sandbox"]["status"] == "warn" and r["sandbox_backend"] == "local"


def test_offline_mode_is_ready_but_says_so(monkeypatch):
    monkeypatch.setenv("CULPRIT_OFFLINE", "1")
    r = preflight.run_preflight()
    assert r["ready"] and r["mode"] == "offline" and _by_name(r)["models"]["status"] == "warn"
    assert "secret" not in str(r).lower()
