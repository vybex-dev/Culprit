# backend/tests/test_store.py
"""Persistence: jobs and their event streams must survive a restart, and jobs
orphaned by a dead process must be marked failed rather than spin forever."""

import pytest

from events import Event
from schema import (
    CommitModel, InMemoryEventStore, InMemoryJobStore, JobNotFoundError, JobState, ProbeModel,
    SqliteEventStore, SqliteJobStore, summarize_job,
)


def _job(job_id="j1", status="queued", created="2026-09-01T00:00:00Z", **kw):
    return JobState(job_id=job_id, status=status, repo_url="/r", benchmark_command="python b.py",
                    created_at=created, updated_at=created, **kw)


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    return InMemoryJobStore() if request.param == "memory" else SqliteJobStore(tmp_path / "t.db")


def test_create_get_save_roundtrip_including_new_fields(store):
    job = _job(
        mode="offline", baseline_score=9.7, threshold_pct=15, n_runs=5, window=[3, 9],
        commits=[CommitModel(index=0, sha="a" * 40, subject="init", author="A", date="2026-09-01")],
        probes=[ProbeModel(step=0, commit="a" * 40, index=0, role="baseline", raw_scores=[9.7, 9.8],
                           median_score=9.75, pct_change=0.0, verdict="baseline", window=[0, 19])],
    )
    store.create(job)
    got = store.get("j1")
    assert got == job
    got.status = "bisecting"
    store.save(got)
    assert store.get("j1").status == "bisecting"


def test_duplicate_create_and_missing_get(store):
    store.create(_job())
    with pytest.raises(ValueError):
        store.create(_job())
    with pytest.raises(JobNotFoundError):
        store.get("nope")


def test_list_is_newest_first(store):
    store.create(_job("old", created="2026-09-01T00:00:00Z"))
    store.create(_job("new", created="2026-09-02T00:00:00Z"))
    assert [j.job_id for j in store.list()] == ["new", "old"]
    assert [j.job_id for j in store.list(limit=1)] == ["new"]


def test_sqlite_survives_reopen_and_recovers_orphans(tmp_path):
    path = tmp_path / "culprit.db"
    s1 = SqliteJobStore(path)
    s1.create(_job("running", status="bisecting"))
    s1.create(_job("finished", status="done", created="2026-09-02T00:00:00Z"))
    ev1 = SqliteEventStore(s1)
    ev1.append("running", Event(seq=1, ts="t", kind="job.start", source="system", message="hi", data={"a": 1}))

    s2 = SqliteJobStore(path)  # "restart"
    ev2 = SqliteEventStore(s2)
    assert s2.get("running").status == "bisecting"
    assert s2.mark_orphans_failed("server restarted") == 1
    orphan = s2.get("running")
    assert orphan.status == "failed" and orphan.error == "server restarted" and "failed" in orphan.stage_times
    assert s2.get("finished").status == "done"  # terminal jobs are left alone
    assert ev2.since("running")[0].data == {"a": 1}  # events persisted too


@pytest.fixture(params=["memory", "sqlite"])
def events(request, tmp_path):
    return InMemoryEventStore() if request.param == "memory" else SqliteEventStore(SqliteJobStore(tmp_path / "e.db"))


def _ev(seq, kind="k"):
    return Event(seq=seq, ts="t", kind=kind, source="system", message=f"m{seq}", data={"n": seq})


def test_event_cursor_paging_never_skips_or_repeats(events):
    for i in range(1, 8):
        events.append("j", _ev(i, "model.end" if i % 3 == 0 else "k"))
    first = events.since("j", 0, limit=3)
    second = events.since("j", first[-1].seq, limit=3)
    third = events.since("j", second[-1].seq, limit=3)
    assert [e.seq for e in first + second + third] == list(range(1, 8))
    assert events.since("j", 7) == []
    assert events.last_seq("j") == 7 and events.last_seq("other") == 0
    assert [e.seq for e in events.of_kinds("j", ["model.end"])] == [3, 6]
    assert events.since("other") == []


def test_summarize_job_row():
    job = _job(
        status="done", regression_commit="b" * 40, baseline_score=10.0, final_result="resolved",
        commits=[CommitModel(index=1, sha="b" * 40, subject="the culprit")],
        probes=[ProbeModel(step=i, commit="c", index=i, role="bisect", raw_scores=[1], median_score=1,
                           pct_change=0, verdict="clean") for i in range(4)],
    )
    s = summarize_job(job)
    assert s.subject == "the culprit" and s.n_probes == 3 and s.final_result == "resolved"
