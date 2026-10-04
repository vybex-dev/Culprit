# backend/tests/test_offline_and_demo.py
"""The bundled demo + offline stand-in. Two things must hold: the regression in
the sample repo is *real* (measured, not asserted), and the stand-in's output
goes through the same production validation as real model output."""

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

import diagnoser
from demo_repo import GUILTY_SUBJECT, build_demo_repo
from offline import offline_call
from patcher import edits_to_patch


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    return build_demo_repo(tmp_path_factory.mktemp("demo") / "orders")


def _bench(repo, sha, runs=2):
    wt = Path(tempfile.mkdtemp())
    shutil.rmtree(wt)
    subprocess.run(["git", "worktree", "add", "--detach", str(wt), sha], cwd=repo.path, capture_output=True, check=True)
    try:
        return [
            float(subprocess.run(["python3", "bench.py"], cwd=wt, capture_output=True, text=True, check=True)
                  .stdout.strip().splitlines()[-1])
            for _ in range(runs)
        ]
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=repo.path, capture_output=True)


def test_demo_repo_is_a_real_history_with_the_guilty_commit_inside_the_range(demo):
    shas = subprocess.run(["git", "rev-list", "--reverse", f"{demo.start_sha}..{demo.end_sha}"], cwd=demo.path,
                          capture_output=True, text=True).stdout.split()
    assert demo.guilty_sha in shas and shas[-1] == demo.end_sha and demo.n_commits == len(shas) + 1
    subject = subprocess.run(["git", "log", "-1", "--format=%s", demo.guilty_sha], cwd=demo.path,
                             capture_output=True, text=True).stdout.strip()
    assert subject == GUILTY_SUBJECT


def test_demo_regression_is_real_and_pinned_to_the_guilty_commit(demo):
    parent = subprocess.run(["git", "rev-parse", demo.guilty_sha + "^"], cwd=demo.path, capture_output=True, text=True).stdout.strip()
    before, after = min(_bench(demo, parent)), min(_bench(demo, demo.guilty_sha))
    assert after > before * 5  # measured, not asserted: the N+1 makes it an order of magnitude slower
    assert min(_bench(demo, demo.end_sha)) > before * 5  # and it stays slow through HEAD


def test_build_demo_repo_is_idempotent(demo):
    again = build_demo_repo(demo.path)
    assert (again.start_sha, again.end_sha, again.guilty_sha) == (demo.start_sha, demo.end_sha, demo.guilty_sha)


def _guilty_diff(demo):
    return subprocess.run(["git", "diff", demo.guilty_sha + "^", demo.guilty_sha], cwd=demo.path,
                          capture_output=True, text=True).stdout


def test_offline_diagnoser_finds_n_plus_one_and_its_citation_survives_the_production_check(demo):
    diff = _guilty_diff(demo)
    out = offline_call("ultra", {"diff": diff})
    assert out["category"] == "n_plus_one"
    # The production verifier (not a mock) must accept the stand-in's citations:
    assert diagnoser._verify_cited_lines(out["cited_lines"], diff)
    # …and it must NOT cite unchanged context lines (the loop header is context here):
    assert all("for order in orders" not in c for c in out["cited_lines"])


def test_offline_diagnoser_declines_honestly_on_an_unknown_pattern():
    diff = "--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,1 @@\n-x = 1\n+x = 2\n"
    out = offline_call("ultra", {"diff": diff})
    assert out["category"] == "other" and out["cited_lines"] == []


def test_offline_fixer_patch_applies_with_real_git_and_removes_the_regression(demo):
    diff = _guilty_diff(demo)
    wt = Path(tempfile.mkdtemp())
    shutil.rmtree(wt)
    subprocess.run(["git", "worktree", "add", "--detach", str(wt), demo.guilty_sha], cwd=demo.path, capture_output=True, check=True)
    try:
        files = "\n\n".join(f"# FILE: {p}\n{(wt / p).read_text()}" for p in ["app/summary.py"])
        out = offline_call("ultra", {"diagnosis": {"diff": diff}, "full_file_contents": files})
        patch = edits_to_patch(files, out["edits"])
        applied = subprocess.run(["git", "apply", "--whitespace=fix", "-"], cwd=wt, input=patch, text=True, capture_output=True)
        assert applied.returncode == 0, applied.stderr
        assert "db.query_in" in (wt / "app/summary.py").read_text()  # the batched lookup is back
        fixed = min(float(subprocess.run(["python3", "bench.py"], cwd=wt, capture_output=True, text=True).stdout.strip().splitlines()[-1])
                    for _ in range(2))
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=demo.path, capture_output=True)
    assert fixed < 50  # back near the ~10ms baseline, far below the ~300ms regression


def test_offline_nano_follows_the_documented_policy():
    clean = offline_call("nano", {"candidate_scores": [10, 10.1, 9.9, 10, 10.05], "baseline_score": 10, "regression_threshold_pct": 15})
    bad = offline_call("nano", {"candidate_scores": [30, 30.2, 29.8, 30, 30.1], "baseline_score": 10, "regression_threshold_pct": 15})
    assert clean["verdict"] == "clean" and bad["verdict"] == "regressed"
    one = offline_call("nano", {"candidate_scores": [30], "baseline_score": 10, "regression_threshold_pct": 15})
    assert one["verdict"] == "inconclusive" and one["additional_runs_needed"] > 0


def test_offline_call_rejects_unknown_payloads():
    with pytest.raises(ValueError):
        offline_call("ultra", {"something": "else"})
