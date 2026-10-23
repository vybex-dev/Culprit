# backend/tests/test_fixer_profile.py
"""
The Fixer is shown the MEASURED profile (the same evidence the Diagnoser gets).

It is purely additive: with no hotspots, the Fixer's call is byte-for-byte what it
was before, and the output schema is unchanged either way. The model is only told
where the time went; the sandbox still decides whether the fix worked.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import api
import fixer
from fixer import FIXER_APPROACH_ADDENDUM, FIXER_SYSTEM_PROMPT, PROFILE_EVIDENCE_ADDENDUM, VerifyOutcome
from schema import InMemoryEventStore, InMemoryJobStore

HOT = [
    {"func": "app/summary.py:get_order_summary", "file": "app/summary.py", "line": 6, "project": True,
     "calls_before": 3, "calls_after": 186, "cum_before_s": 0.01, "cum_after_s": 0.4,
     "self_before_s": 0.0, "self_after_s": 0.1},
] + [
    {"func": f"lib:f{i}", "file": "lib.py", "line": i, "project": False, "calls_before": 1, "calls_after": 2,
     "cum_before_s": 0.0, "cum_after_s": 0.1, "self_before_s": 0.0, "self_after_s": 0.1}
    for i in range(8)
]
DIAG = {"category": "n_plus_one", "explanation": "x", "cited_lines": ["y"]}
FILES = "# FILE: f.py\nx\n"


class _Res:
    def __init__(self, parsed):
        self.parsed = parsed


def _fake(calls):
    def call(*, job_id, step, model, system_prompt, payload, temperature, **kw):
        calls.append({"step": step, "prompt": system_prompt, "payload": payload})
        return _Res({"patch": "--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-x\n+y\n", "rationale": "r", "confidence_will_resolve": "high"})
    return call


def test_compact_profile_keeps_top_rows_and_key_fields_only():
    assert fixer.compact_profile(None) is None and fixer.compact_profile([]) is None
    p = fixer.compact_profile(HOT)
    assert len(p["hotspots"]) == fixer.MAX_PROFILE_HOTSPOTS
    assert p["hotspots"][0]["calls_after"] == 186
    assert set(p["hotspots"][0]) == set(fixer._PROFILE_FIELDS)  # self_* timings are not sent
    assert "profile_evidence" not in HOT[0]  # input is not mutated


def test_without_hotspots_the_call_is_exactly_what_it_always_was(monkeypatch):
    calls = []
    monkeypatch.setattr(fixer, "call_nemotron", _fake(calls))
    for hs in (None, []):
        fixer.propose_patch(job_id="j", diagnosis=DIAG, full_file_contents=FILES, attempt_number=1, profile_hotspots=hs)
    for c in calls:
        assert c["prompt"] == FIXER_SYSTEM_PROMPT
        assert set(c["payload"]) == {"diagnosis", "full_file_contents", "attempt_number", "previous_attempt_result"}


def test_with_hotspots_the_payload_and_prompt_gain_the_evidence(monkeypatch):
    calls = []
    monkeypatch.setattr(fixer, "call_nemotron", _fake(calls))
    fixer.propose_patch(job_id="j", diagnosis=DIAG, full_file_contents=FILES, attempt_number=1, profile_hotspots=HOT)
    c = calls[0]
    assert c["prompt"] == FIXER_SYSTEM_PROMPT + PROFILE_EVIDENCE_ADDENDUM
    assert c["payload"]["profile_evidence"]["hotspots"][0]["calls_before"] == 3
    # the output contract is untouched: still edits copied from the files shown
    assert '"edits"' in c["prompt"] and "never cite" not in PROFILE_EVIDENCE_ADDENDUM


def test_profile_and_approach_addenda_stack(monkeypatch):
    calls = []
    monkeypatch.setattr(fixer, "call_nemotron", _fake(calls))
    fixer.propose_patch(job_id="j", diagnosis=DIAG, full_file_contents=FILES, attempt_number=1,
                        approach="do x", candidate=1, profile_hotspots=HOT)
    assert calls[0]["prompt"] == FIXER_SYSTEM_PROMPT + FIXER_APPROACH_ADDENDUM + PROFILE_EVIDENCE_ADDENDUM
    assert calls[0]["payload"]["approach"] == "do x" and "profile_evidence" in calls[0]["payload"]


@pytest.mark.parametrize("candidates", [1, 3])
def test_the_loop_hands_the_profile_to_every_proposal(monkeypatch, candidates):
    calls = []
    monkeypatch.setattr(fixer, "call_nemotron", _fake(calls))
    fixer.run_fix_loop_live(job_id="j", diagnosis=DIAG, full_file_contents=FILES,
                            verify=lambda p: VerifyOutcome(score_after=100.0, resolved=False),
                            before_score=300.0, max_attempts=2, candidates=candidates, profile_hotspots=HOT)
    assert len(calls) == 2 * candidates
    assert all("profile_evidence" in c["payload"] for c in calls)


# ---------------------------------------------------------------------------
# End to end — the real profile from the real demo repo reaches the Fixer
# ---------------------------------------------------------------------------

def _wait(client, job_id, timeout=180.0):
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/jobs/{job_id}").json()
        if j["status"] in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.2)
    raise TimeoutError(job_id)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("CULPRIT_OFFLINE", "1")
    monkeypatch.setattr(api, "_FIX_CANDIDATES", "1")
    return TestClient(api.create_app(store=InMemoryJobStore(), event_store=InMemoryEventStore(), data_dir=tmp_path))


def _fix_requests(client, jid):
    evs = client.get(f"/jobs/{jid}/events?limit=2000").json()["events"]
    return [e["data"]["request"] for e in evs
            if e["kind"] == "model.start" and "full_file_contents" in (e["data"].get("request") or {}).get("payload", {})
            and "test_file_name" not in e["data"]["request"]["payload"]]


def test_the_demos_measured_hotspots_are_what_the_fixer_is_shown(client):
    jid = client.post("/demo").json()["job_id"]
    job = _wait(client, jid)
    assert job["final_result"] == "resolved" and job["profile"]["hotspots"]
    reqs = _fix_requests(client, jid)
    assert len(reqs) == 1
    shown = reqs[0]["payload"]["profile_evidence"]["hotspots"]
    stored = job["profile"]["hotspots"]
    assert shown[0]["func"] == stored[0]["func"] and shown[0]["calls_after"] == stored[0]["calls_after"]
    assert reqs[0]["system"].endswith(PROFILE_EVIDENCE_ADDENDUM)


def test_no_profile_means_the_fixer_prompt_is_untouched(monkeypatch, client):
    monkeypatch.setattr(api, "_PROFILE_ENABLED", False)
    jid = client.post("/demo").json()["job_id"]
    job = _wait(client, jid)
    assert job["final_result"] == "resolved" and job["profile"] is None
    reqs = _fix_requests(client, jid)
    assert len(reqs) == 1 and "profile_evidence" not in reqs[0]["payload"]
    assert PROFILE_EVIDENCE_ADDENDUM not in reqs[0]["system"]
