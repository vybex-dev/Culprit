# backend/tests/test_sandbox_client.py
"""
Proves the Week 1 exit criteria: checkout -> run -> number back, reliably,
against a real (tiny, throwaway) git repo — no mocks, no Token Factory
credentials needed. Also proves the venv cache is actually reused when the
lockfile is unchanged across commits.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sandbox_client import LocalGitSandbox, SandboxError  # noqa: E402

FAST_BENCH = textwrap.dedent(
    """
    import time
    import colorama  # proves the venv/lockfile install actually ran
    colorama.init()

    start = time.perf_counter()
    total = 0
    for i in range(2_000_000):
        total += i
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"# baseline workload, total={total}")
    print(elapsed_ms)
    """
)

# Regression: same workload, but now called inside a nested loop (O(n) -> O(n*k))
SLOW_BENCH = textwrap.dedent(
    """
    import time
    import colorama
    colorama.init()

    start = time.perf_counter()
    total = 0
    for _ in range(8):
        for i in range(2_000_000):
            total += i
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"# regressed workload, total={total}")
    print(elapsed_ms)
    """
)


@pytest.fixture
def demo_repo(tmp_path):
    repo = tmp_path / "demo_repo"
    repo.mkdir()
    run = lambda *args: subprocess.run(args, cwd=repo, check=True, capture_output=True, text=True)  # noqa: E731

    run("git", "init", "-q")
    run("git", "config", "user.email", "test@example.com")
    run("git", "config", "user.name", "Test")

    (repo / "requirements.txt").write_text("colorama==0.4.6\n")
    (repo / "bench.py").write_text(FAST_BENCH)
    run("git", "add", ".")
    run("git", "commit", "-q", "-m", "baseline: O(n) workload")
    baseline_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()

    (repo / "bench.py").write_text(SLOW_BENCH)
    run("git", "add", ".")
    run("git", "commit", "-q", "-m", "regression: nested the workload in an extra loop")
    regressed_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()

    return repo, baseline_sha, regressed_sha


def test_checkout_run_and_score_come_back(demo_repo, tmp_path):
    repo, baseline_sha, regressed_sha = demo_repo
    cache_dir = tmp_path / "sandbox_cache"
    sandbox = LocalGitSandbox(str(repo), cache_dir=str(cache_dir))

    baseline = sandbox.run_benchmark(baseline_sha, "python bench.py", n_runs=3)
    regressed = sandbox.run_benchmark(regressed_sha, "python bench.py", n_runs=3)

    assert len(baseline.raw_scores) == 3
    assert len(regressed.raw_scores) == 3
    assert all(isinstance(s, float) for s in baseline.raw_scores + regressed.raw_scores)

    # The injected regression (8x the workload) should be unmistakably slower —
    # loose bound (>2x) so this isn't flaky on a noisy CI box.
    assert min(regressed.raw_scores) > 2 * max(baseline.raw_scores)

    # Same lockfile both commits -> exactly one venv build directory, reused.
    venv_dirs = list(cache_dir.glob("venv-*"))
    assert len(venv_dirs) == 1


def test_bad_benchmark_output_raises_not_fabricates(demo_repo):
    repo, baseline_sha, _ = demo_repo
    (repo / "bench.py").write_text("print('not a number')")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "broken bench"], cwd=repo, check=True)
    broken_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()

    sandbox = LocalGitSandbox(str(repo))
    with pytest.raises(SandboxError):
        sandbox.run_benchmark(broken_sha, "python bench.py", n_runs=1)


def test_bad_commit_sha_raises(demo_repo):
    repo, _, _ = demo_repo
    sandbox = LocalGitSandbox(str(repo))
    with pytest.raises(SandboxError):
        sandbox.run_benchmark("0" * 40, "python bench.py", n_runs=1)


def test_unsupported_lockfile_does_not_silently_cache_a_broken_venv(demo_repo, tmp_path):
    """Regression test. Previously poetry.lock/uv.lock would produce a
    lock_hash (used for caching) even though _build_venv() only ever
    installed from requirements.txt — so a repo shipping only poetry.lock
    got an empty venv silently marked 'cached and good' under that hash.
    Now unsupported lockfiles simply aren't detected: no hash, no marker,
    ephemeral venv every time — an honest failure instead of a cached bad
    build.
    """
    repo, _, _ = demo_repo
    subprocess.run(["git", "rm", "-q", "requirements.txt"], cwd=repo, check=True)
    (repo / "poetry.lock").write_text("# not a real lockfile, just present\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "swap to poetry.lock"], cwd=repo, check=True)
    poetry_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()

    cache_dir = tmp_path / "cache"
    sandbox = LocalGitSandbox(str(repo), cache_dir=str(cache_dir))
    # bench.py still does `import colorama` — with nothing to install it
    # from, this must fail loudly rather than "succeed" on a broken venv.
    with pytest.raises(SandboxError):
        sandbox.run_benchmark(poetry_sha, "python bench.py", n_runs=1)
    assert list(cache_dir.glob("venv-*")) == []  # nothing got cached as "ok"


def test_slow_benchmark_raises_sandboxerror_not_raw_timeout(demo_repo):
    """Regression test. Previously subprocess.TimeoutExpired propagated
    unconverted, breaking the module's own contract that a failure — timeouts
    included — must surface as SandboxError, never escape as something else.
    """
    repo, _, _ = demo_repo
    (repo / "bench.py").write_text("import time\ntime.sleep(5)\nprint(1.0)\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "slow bench"], cwd=repo, check=True)
    slow_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()

    sandbox = LocalGitSandbox(str(repo), timeout_s=1)
    with pytest.raises(SandboxError):
        sandbox.run_benchmark(slow_sha, "python bench.py", n_runs=1)
