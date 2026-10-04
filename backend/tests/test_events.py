# backend/tests/test_events.py
"""The event stream is what the dashboard terminal renders — it has to be
ordered, complete, bounded, and unable to break the pipeline."""

import threading

import pytest

from events import NULL_REPORTER, Event, JobCancelled, Reporter, or_null, sandbox_listener, summarize


def _collect():
    seen: list[Event] = []
    return seen, Reporter("job-1", sink=seen.append)


def test_seq_is_monotonic_and_starts_after_start_seq():
    seen, rep = _collect()
    rep.emit("a", "system", "one")
    rep.emit("b", "git", "two")
    assert [e.seq for e in seen] == [1, 2]
    resumed = Reporter("job-1", sink=seen.append, start_seq=41)
    assert resumed.emit("c", "system", "three").seq == 42


def test_span_pairs_start_and_end_with_duration():
    seen, rep = _collect()
    sid = rep.start("probe", "bisect", "probing", step=3)
    rep.end(sid, "done", verdict="clean")
    start, end = seen
    assert start.kind == "probe.start" and end.kind == "probe.end"
    assert start.data["span"] == end.data["span"] == sid
    assert end.data["dur_s"] >= 0 and end.data["verdict"] == "clean" and end.source == "bisect"


def test_oversized_payloads_are_truncated_not_dropped():
    seen, rep = _collect()
    rep.emit("model.end", "ultra", "x", response="y" * 50_000, nested={"deep": ["z" * 50_000]})
    d = seen[0].data
    assert len(d["response"]) < 13_000 and "truncated" in d["response"]
    assert "truncated" in d["nested"]["deep"][0]


def test_a_broken_sink_never_breaks_the_pipeline():
    def boom(_e):
        raise RuntimeError("db is down")

    rep = Reporter("j", sink=boom)
    assert rep.emit("k", "system", "still fine").seq == 1


def test_cancel_flag_raises_at_checkpoint():
    flag = threading.Event()
    rep = Reporter("j", cancel=flag)
    rep.check_cancelled()  # not set → no-op
    flag.set()
    with pytest.raises(JobCancelled):
        rep.check_cancelled()


def test_null_reporter_is_inert():
    rep = or_null(None)
    assert rep is NULL_REPORTER
    rep.emit("x", "y", "z")
    rep.end(rep.start("a", "b", "c"), "d")
    rep.check_cancelled()


def test_summarize_aggregates_only_what_happened():
    seen, rep = _collect()
    rep.emit("model.end", "nano", "r", role="nano", model_id="nano-1", latency_s=0.5, attempts=2,
             usage={"prompt_tokens": 100, "completion_tokens": 20})
    rep.emit("model.end", "nano", "r", role="nano", model_id="nano-1", latency_s=0.3, attempts=1,
             usage={"prompt_tokens": 50, "completion_tokens": 10})
    rep.emit("model.end", "ultra", "r", role="ultra", model_id="ultra-1", latency_s=4.0, attempts=1, usage={})
    rep.emit("sandbox.run", "sandbox", "r", wall_s=1.5)
    rep.emit("sandbox.run", "sandbox", "r", wall_s=1.0, patched=True)
    rep.emit("probe.end", "bisect", "p")
    rep.emit("tavily.end", "tavily", "t", n_refs=2)
    m = summarize(seen)
    nano = m["models"]["nano"]
    assert nano["calls"] == 2 and nano["total_tokens"] == 180 and nano["retries"] == 1
    assert nano["avg_latency_s"] == 0.4
    assert m["models"]["ultra"]["total_tokens"] == 0  # no usage reported → 0, never estimated
    assert m["sandbox"]["runs"] == 2 and m["sandbox"]["patched_runs"] == 1 and m["sandbox"]["probes"] == 1
    assert m["tavily"] == {"searches": 1, "sources": 2}


def test_sandbox_listener_translates_backend_callbacks():
    seen, rep = _collect()
    listener = sandbox_listener(rep, lambda: {"step": 4})
    listener("run", {"run": 2, "n_runs": 5, "score": 9.5, "commit": "abc", "patched": False})
    listener("deps", {"cache_hit": True, "commit": "abc"})
    listener("instance", {"phase": "start", "operation": "op-1234567890", "commit": "abc"})
    listener("instance", {"phase": "end", "operation": "op-1234567890", "status": "SUCCESS", "commit": "abc"})
    assert [e.kind for e in seen] == ["sandbox.run", "sandbox.deps", "sandbox.instance.start", "sandbox.instance.end"]
    assert all(e.data["step"] == 4 for e in seen)  # context is stamped on every event
    assert seen[2].data["span"] == seen[3].data["span"] == "op-1234567890"
