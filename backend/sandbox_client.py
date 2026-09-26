# backend/sandbox_client.py
"""
sandbox_client.py — Sandbox execution wrapper for Performance Regression Detective.

Per AGENT_SPECS.md / TRD.md §4, every candidate commit gets a fresh, isolated
sandbox: checkout -> install deps (cached by lockfile hash) -> run the
benchmark N times -> return every raw score (median/threshold comparison is
the Bisector's job, not this module's). apply_patch_and_benchmark() extends
this for the Fixer/Verifier loop (AGENT_SPECS.md §3): same guarantees, plus
applying a unified-diff patch on top of the commit before benchmarking.

Two backends, one contract:
  - LocalGitSandbox: git worktree checkout + a venv cached by lockfile hash,
    running benchmark_command as a subprocess. No Token Factory credentials
    needed — lets bisector.py be built and tested end-to-end today.
  - TokenFactorySandbox: the real backend for the hackathon submission.
    Stubbed pending Token Factory Sandbox API access — fill in
    run_benchmark() once NEBIUS_API_KEY + the actual SDK surface are
    available. Must satisfy the exact same Sandbox contract.

Callers (bisector.py, fixer.py, ...) depend only on the Sandbox ABC below —
swapping Local for TokenFactory is a one-line change via get_sandbox().
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
import venv
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import structlog

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
)
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = structlog.get_logger("sandbox_client")


class SandboxError(Exception):
    """Raised when a sandbox run fails outright (checkout, install, or
    benchmark execution). Callers MUST surface this as a failed job — never
    swallow it into a fabricated score. See AGENTS.md non-negotiable #1."""


@dataclass
class BenchmarkResult:
    commit_sha: str
    raw_scores: list[float]
    stdout_tail: str = ""
    patched: bool = False  # True when this result came from apply_patch_and_benchmark()


class Sandbox(ABC):
    """Common contract every sandbox backend must satisfy."""

    @abstractmethod
    def run_benchmark(
        self, commit_sha: str, benchmark_command: str, n_runs: int = 5
    ) -> BenchmarkResult:
        """Checkout commit_sha in a fresh, isolated environment, install deps,
        run benchmark_command n_runs times, and return every raw score.

        benchmark_command's I/O contract: the LAST non-empty line printed to
        stdout must be a bare number (e.g. elapsed milliseconds) — nothing
        else on that line. Anything above that is free-form and ignored.

        Must raise SandboxError rather than return a partial/fabricated
        result if checkout, install, or any single run fails.
        """

    @abstractmethod
    def apply_patch_and_benchmark(
        self, commit_sha: str, patch: str, benchmark_command: str, n_runs: int = 5
    ) -> BenchmarkResult:
        """Same guarantees as run_benchmark (fresh isolated checkout, cached
        deps, every raw score returned), but applies `patch` (a unified
        diff, as Fixer produces per AGENT_SPECS.md §3) on top of commit_sha
        before installing/running. A patch that doesn't apply cleanly must
        raise SandboxError — never be silently skipped or scored as if
        nothing changed (AGENTS.md #1 applies to Fixer verification exactly
        as it does to Bisector runs).
        """


class LocalGitSandbox(Sandbox):
    """Local dev/testing backend. Isolation model: a fresh `git worktree` per
    call (fresh working tree, no state carried between commits) plus a venv
    cached by lockfile hash (fresh install per unique dependency set, not per
    commit — this is the caching behavior AGENTS.md/TRD.md ask for so a
    5-week build with limited credits doesn't reinstall on every commit).
    """

    def __init__(self, repo_path: str, cache_dir: str | None = None, timeout_s: int = 60):
        self.repo_path = Path(repo_path).resolve()
        if not (self.repo_path / ".git").exists():
            raise SandboxError(f"{self.repo_path} is not a git repo root")
        self.cache_dir = Path(
            cache_dir or os.environ.get("SANDBOX_CACHE_DIR", tempfile.gettempdir() + "/prd_cache")
        )
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_s = timeout_s

    # Python-only, per AGENTS.md's "one ecosystem, go deep" rule. Both of these
    # are formats `pip install -r` can actually consume. poetry.lock/uv.lock
    # were dropped: _build_venv() had no way to install from them, so
    # detecting one just produced a hash for an install step that silently
    # installed nothing and then cached that empty venv as "good."
    _LOCKFILE_CANDIDATES = ("requirements.txt", "requirements.lock")

    def _find_lockfile(self, worktree: Path) -> Path | None:
        for name in self._LOCKFILE_CANDIDATES:
            p = worktree / name
            if p.exists():
                return p
        return None

    def _lockfile_hash(self, worktree: Path) -> str | None:
        lockfile = self._find_lockfile(worktree)
        return hashlib.sha256(lockfile.read_bytes()).hexdigest()[:16] if lockfile else None

    def _venv_for_hash(self, lock_hash: str | None, worktree: Path) -> tuple[Path, bool]:
        """Returns (venv_dir, is_ephemeral). Ephemeral venvs (no lockfile
        found) are the caller's responsibility to clean up."""
        if lock_hash is None:
            venv_dir = Path(tempfile.mkdtemp(prefix="prd_venv_"))
            self._build_venv(venv_dir, worktree)
            return venv_dir, True

        venv_dir = self.cache_dir / f"venv-{lock_hash}"
        marker = venv_dir / ".prd_build_ok"
        if not marker.exists():
            log.info("sandbox.venv.build", lock_hash=lock_hash)
            self._build_venv(venv_dir, worktree)
            marker.touch()
        else:
            log.info("sandbox.venv.cache_hit", lock_hash=lock_hash)
        return venv_dir, False

    def _build_venv(self, venv_dir: Path, worktree: Path) -> None:
        venv.create(venv_dir, with_pip=True, clear=True)
        pip = venv_dir / "bin" / "pip"
        lockfile = self._find_lockfile(worktree)  # same file _lockfile_hash used
        if lockfile is not None:
            try:
                proc = subprocess.run(
                    [str(pip), "install", "-q", "-r", str(lockfile)],
                    capture_output=True, text=True, timeout=300,
                )
            except subprocess.TimeoutExpired as e:
                shutil.rmtree(venv_dir, ignore_errors=True)
                raise SandboxError(f"dependency install timed out (300s) for {lockfile.name}") from e
            if proc.returncode != 0:
                shutil.rmtree(venv_dir, ignore_errors=True)
                raise SandboxError(f"dependency install failed: {proc.stderr[-2000:]}")

    def _apply_patch(self, worktree: Path, patch: str) -> None:
        try:
            proc = subprocess.run(
                ["git", "apply", "--whitespace=fix", "-"],
                cwd=worktree, input=patch, capture_output=True, text=True, timeout=30,
            )
        except subprocess.TimeoutExpired as e:
            raise SandboxError("patch apply timed out (30s)") from e
        if proc.returncode != 0:
            raise SandboxError(f"patch did not apply cleanly to {worktree.name}: {proc.stderr[-2000:]}")

    def run_benchmark(
        self, commit_sha: str, benchmark_command: str, n_runs: int = 5
    ) -> BenchmarkResult:
        return self._run_benchmark_in_worktree(commit_sha, benchmark_command, n_runs, patch=None)

    def apply_patch_and_benchmark(
        self, commit_sha: str, patch: str, benchmark_command: str, n_runs: int = 5
    ) -> BenchmarkResult:
        return self._run_benchmark_in_worktree(commit_sha, benchmark_command, n_runs, patch=patch)

    def _run_benchmark_in_worktree(
        self, commit_sha: str, benchmark_command: str, n_runs: int, *, patch: str | None
    ) -> BenchmarkResult:
        worktree = Path(tempfile.mkdtemp(prefix=f"prd_wt_{commit_sha[:8]}_"))
        worktree.rmdir()  # `git worktree add` requires the target not exist yet
        venv_dir: Path | None = None
        venv_is_ephemeral = False
        try:
            try:
                proc = subprocess.run(
                    ["git", "worktree", "add", "--detach", str(worktree), commit_sha],
                    cwd=self.repo_path, capture_output=True, text=True, timeout=60,
                )
            except subprocess.TimeoutExpired as e:
                raise SandboxError(f"checkout timed out (60s) for {commit_sha}") from e
            if proc.returncode != 0:
                raise SandboxError(f"checkout failed for {commit_sha}: {proc.stderr}")

            if patch is not None:
                self._apply_patch(worktree, patch)  # raises SandboxError on failure

            lock_hash = self._lockfile_hash(worktree)
            venv_dir, venv_is_ephemeral = self._venv_for_hash(lock_hash, worktree)

            scores: list[float] = []
            last_stdout = ""
            for i in range(n_runs):
                try:
                    run = subprocess.run(
                        benchmark_command, shell=True, cwd=worktree,
                        capture_output=True, text=True, timeout=self.timeout_s,
                        env={**os.environ, "PATH": f"{venv_dir}/bin:{os.environ['PATH']}"},
                    )
                except subprocess.TimeoutExpired as e:
                    raise SandboxError(
                        f"benchmark run {i + 1}/{n_runs} timed out after {self.timeout_s}s for {commit_sha}"
                    ) from e
                last_stdout = run.stdout.strip()
                if run.returncode != 0:
                    raise SandboxError(
                        f"benchmark run {i + 1}/{n_runs} failed for {commit_sha}: {run.stderr[-2000:]}"
                    )
                try:
                    score = float(last_stdout.splitlines()[-1])
                except (ValueError, IndexError) as e:
                    raise SandboxError(
                        "benchmark_command must print a single numeric score as its "
                        f"last stdout line; got: {last_stdout!r}"
                    ) from e
                scores.append(score)
                log.info("sandbox.run", commit=commit_sha, run=i + 1, score=score, patched=patch is not None)

            return BenchmarkResult(
                commit_sha=commit_sha, raw_scores=scores, stdout_tail=last_stdout, patched=patch is not None,
            )
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=self.repo_path, capture_output=True, text=True,
            )
            if venv_dir is not None and venv_is_ephemeral:
                shutil.rmtree(venv_dir, ignore_errors=True)


class TokenFactorySandbox(Sandbox):
    """Real backend for the hackathon submission — wraps Nebius Token Factory
    Sandboxes. STUBBED pending actual API/SDK access. Fill in run_benchmark()
    to match LocalGitSandbox's contract exactly (same isolation guarantee:
    fresh sandbox per candidate commit, per AGENTS.md non-negotiable #2).
    """

    def __init__(self, repo_url: str, api_key: str | None = None):
        self.repo_url = repo_url
        self.api_key = api_key or os.environ.get("NEBIUS_API_KEY")
        if not self.api_key:
            raise SandboxError("NEBIUS_API_KEY not set — cannot create a real Token Factory sandbox")

    def run_benchmark(
        self, commit_sha: str, benchmark_command: str, n_runs: int = 5
    ) -> BenchmarkResult:
        raise NotImplementedError(
            "TokenFactorySandbox.run_benchmark: wire up against the real Token "
            "Factory Sandbox API once access is confirmed. Must return the "
            "same BenchmarkResult contract as LocalGitSandbox — every raw "
            "score, never a fabricated one (AGENTS.md #1)."
        )

    def apply_patch_and_benchmark(
        self, commit_sha: str, patch: str, benchmark_command: str, n_runs: int = 5
    ) -> BenchmarkResult:
        raise NotImplementedError(
            "TokenFactorySandbox.apply_patch_and_benchmark: wire up alongside "
            "run_benchmark once Token Factory Sandbox API access is confirmed."
        )


def get_sandbox(repo_url_or_path: str, use_token_factory: bool = False) -> Sandbox:
    """Factory — the only place that should decide which backend is in play."""
    if use_token_factory:
        return TokenFactorySandbox(repo_url_or_path)
    return LocalGitSandbox(repo_url_or_path)
