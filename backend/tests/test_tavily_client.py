# FILE: backend/tests/test_tavily_client.py — place at this path in the
# Culprit repo (new file, sits alongside test_models.py etc.).
"""
No live TAVILY_API_KEY in this environment, so search_grounding()'s HTTP
call is faked via httpx.MockTransport — same convention test_models.py uses
for Nemotron. This proves the query-construction heuristic and the
"never raise, always degrade to []" contract; it does NOT prove Tavily's
real /search response shape matches what tavily_client.py assumes from
public docs. See tavily_client.py's module docstring.
"""

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tavily_client import TavilyRef, build_query, search_grounding  # noqa: E402


def _client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# build_query — the identifier-extraction heuristic
# ---------------------------------------------------------------------------

def test_build_query_extracts_call_shaped_identifier():
    cited = ["for order in orders:", "    order.line_items = LineItem.objects.filter(order_id=order.id)"]
    query = build_query("n_plus_one", cited)
    assert query == "n plus one performance issue LineItem.objects.filter"


def test_build_query_falls_back_to_anti_pattern_pattern_without_identifier():
    # No call-shaped ("...(") token in this line, so no identifier to extract.
    cited = ["GLOBAL_CACHE_ENABLED = False"]
    query = build_query("lost_cache", cited)
    assert query == "is GLOBAL_CACHE_ENABLED = False a known anti-pattern"


def test_build_query_falls_back_to_bare_category_with_no_cited_lines():
    query = build_query("algorithmic_complexity", [])
    assert query == "algorithmic complexity performance issue"


# ---------------------------------------------------------------------------
# search_grounding — HTTP behavior
# ---------------------------------------------------------------------------

def test_returns_refs_on_successful_response():
    def handler(request):
        return httpx.Response(200, json={
            "query": "n plus one performance issue filter",
            "results": [
                {"title": "Avoiding N+1 queries in Django", "url": "https://example.com/a", "score": 0.9},
                {"title": "ORM performance anti-patterns", "url": "https://example.com/b", "score": 0.8},
            ],
        })

    refs = search_grounding("n_plus_one", ["LineItem.objects.filter(order_id=order.id)"],
                             api_key="fake", client=_client_for(handler))
    assert refs == [
        TavilyRef(title="Avoiding N+1 queries in Django", url="https://example.com/a"),
        TavilyRef(title="ORM performance anti-patterns", url="https://example.com/b"),
    ]


def test_no_api_key_returns_empty_without_calling_http():
    called = False

    def handler(request):
        nonlocal called
        called = True
        return httpx.Response(200, json={"results": []})

    refs = search_grounding("n_plus_one", ["x("], api_key=None, client=_client_for(handler))
    assert refs == []
    assert not called


def test_http_error_returns_empty_not_raises():
    def handler(request):
        return httpx.Response(500, text="internal error")

    refs = search_grounding("n_plus_one", ["x("], api_key="fake", client=_client_for(handler))
    assert refs == []


def test_malformed_response_body_returns_empty_not_raises():
    def handler(request):
        return httpx.Response(200, content=b"not json")

    refs = search_grounding("n_plus_one", ["x("], api_key="fake", client=_client_for(handler))
    assert refs == []


def test_unexpected_response_shape_returns_empty_not_raises():
    def handler(request):
        return httpx.Response(200, json={"no_results_key_at_all": True})

    refs = search_grounding("n_plus_one", ["x("], api_key="fake", client=_client_for(handler))
    assert refs == []


def test_malformed_individual_results_are_dropped_not_the_whole_batch():
    def handler(request):
        return httpx.Response(200, json={
            "results": [
                {"title": "Good result", "url": "https://example.com/good"},
                {"title": "", "url": "https://example.com/empty-title"},
                {"url": "https://example.com/no-title"},
                {"title": "No URL"},
                "not even a dict",
            ],
        })

    refs = search_grounding("n_plus_one", ["x("], api_key="fake", client=_client_for(handler))
    assert refs == [TavilyRef(title="Good result", url="https://example.com/good")]


def test_request_uses_bearer_auth_and_query_max_results():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        import json as _json
        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"results": []})

    search_grounding("lost_cache", [], api_key="tvly-secret", max_results=7, client=_client_for(handler))
    assert seen["auth"] == "Bearer tvly-secret"
    assert seen["body"]["max_results"] == 7
    assert seen["body"]["query"] == "lost cache performance issue"
