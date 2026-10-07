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

import base64
import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
import time
import venv
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

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


class BenchmarkRunError(SandboxError):
    """The sandbox worked, but the *benchmark command itself* failed — a
    non-zero exit, a timeout, or output that isn't a number. Distinct from
    infrastructure failures (checkout, dependency install, API errors), which
    stay plain SandboxError.

    Why the split matters: on a committed revision, either is fatal (we can't
    honestly score it). But on a *model-patched* revision, a crash is a normal,
    expected outcome — LLM patches break code — and the Fixer loop exists to
    retry on it, using `detail` (the traceback) as feedback. It must not kill
    the job. Subclassing SandboxError keeps every existing `except SandboxError`
    and `pytest.raises(SandboxError)` valid.
    """

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail


@dataclass
class BenchmarkResult:
    commit_sha: str
    raw_scores: list[float]
    stdout_tail: str = ""
    patched: bool = False  # True when this result came from apply_patch_and_benchmark()


# A sandbox listener receives (kind, data) for every real thing a backend does:
# "checkout", "patch", "deps", "run", "instance". It is how the live terminal
# shows each benchmark run landing *as it happens* instead of after the fact.
# Listeners must never raise into the sandbox — _notify() swallows errors.
SandboxListener = Callable[[str, dict[str, Any]], None]


class Sandbox(ABC):
    """Common contract every sandbox backend must satisfy."""

    #: Optional live-event hook (see SandboxListener). Set by the pipeline;
    #: backends that don't know about it (or test fakes) simply ignore it.
    listener: SandboxListener | None = None

    def _notify(self, kind: str, **data: Any) -> None:
        cb = self.listener
        if cb is None:
            return
        try:
            cb(kind, data)
        except Exception:  # noqa: BLE001 — a UI hook must not break a benchmark
            log.warning("sandbox.listener_failed", kind=kind)

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

    def __init__(self, repo_path: str, cache_dir: str | None = None, timeout_s: int | None = None):
        if timeout_s is None:
            # Per-run cap. 60s is fine on a laptop, but a throttled free-tier
            # host can stall a run well past that — override via the env.
            timeout_s = int(os.environ.get("BENCHMARK_RUN_TIMEOUT_S", "60"))
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
            self._last_deps = {"cache_hit": False, "lock_hash": None, "lockfile": None}
            return venv_dir, True

        venv_dir = self.cache_dir / f"venv-{lock_hash}"
        marker = venv_dir / ".prd_build_ok"
        lockfile = self._find_lockfile(worktree)
        if not marker.exists():
            log.info("sandbox.venv.build", lock_hash=lock_hash)
            self._build_venv(venv_dir, worktree)
            marker.touch()
            self._last_deps = {"cache_hit": False, "lock_hash": lock_hash, "lockfile": lockfile.name if lockfile else None}
        else:
            log.info("sandbox.venv.cache_hit", lock_hash=lock_hash)
            self._last_deps = {"cache_hit": True, "lock_hash": lock_hash, "lockfile": lockfile.name if lockfile else None}
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
            t_checkout = time.perf_counter()
            try:
                proc = subprocess.run(
                    ["git", "worktree", "add", "--detach", str(worktree), commit_sha],
                    cwd=self.repo_path, capture_output=True, text=True, timeout=60,
                )
            except subprocess.TimeoutExpired as e:
                raise SandboxError(f"checkout timed out (60s) for {commit_sha}") from e
            if proc.returncode != 0:
                raise SandboxError(f"checkout failed for {commit_sha}: {proc.stderr}")
            self._notify("checkout", commit=commit_sha, dur_s=round(time.perf_counter() - t_checkout, 3),
                         backend="local-worktree")

            if patch is not None:
                self._apply_patch(worktree, patch)  # raises SandboxError on failure
                self._notify("patch", commit=commit_sha, applied=True)

            t_deps = time.perf_counter()
            lock_hash = self._lockfile_hash(worktree)
            venv_dir, venv_is_ephemeral = self._venv_for_hash(lock_hash, worktree)
            self._notify("deps", commit=commit_sha, dur_s=round(time.perf_counter() - t_deps, 3),
                         **getattr(self, "_last_deps", {}))

            scores: list[float] = []
            last_stdout = ""
            for i in range(n_runs):
                t_run = time.perf_counter()
                try:
                    run = subprocess.run(
                        benchmark_command, shell=True, cwd=worktree,
                        capture_output=True, text=True, timeout=self.timeout_s,
                        env={**os.environ, "PATH": f"{venv_dir}/bin:{os.environ['PATH']}"},
                    )
                except subprocess.TimeoutExpired as e:
                    raise BenchmarkRunError(
                        f"benchmark run {i + 1}/{n_runs} timed out after {self.timeout_s}s for {commit_sha}",
                        detail=f"timed out after {self.timeout_s}s",
                    ) from e
                last_stdout = run.stdout.strip()
                if run.returncode != 0:
                    raise BenchmarkRunError(
                        f"benchmark run {i + 1}/{n_runs} failed for {commit_sha}: {run.stderr[-2000:]}",
                        detail=run.stderr[-1500:],
                    )
                try:
                    score = float(last_stdout.splitlines()[-1])
                except (ValueError, IndexError) as e:
                    raise BenchmarkRunError(
                        "benchmark_command must print a single numeric score as its "
                        f"last stdout line; got: {last_stdout!r}",
                        detail=f"last stdout line was not a number: {last_stdout[-300:]!r}",
                    ) from e
                scores.append(score)
                log.info("sandbox.run", commit=commit_sha, run=i + 1, score=score, patched=patch is not None)
                self._notify("run", commit=commit_sha, run=i + 1, n_runs=n_runs, score=score,
                             wall_s=round(time.perf_counter() - t_run, 3), patched=patch is not None)

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
    """Real backend: Nebius Token Factory Sandboxes (ConTree) over plain HTTPS.

    One VM-isolated, disposable instance per call (AGENTS.md #2): the commit's
    tree is `git archive`d locally, piped in over stdin, unpacked in the VM,
    optionally patched, deps installed, then benchmark_command runs n_runs
    times inside the SAME VM. Each run prints `CULPRIT_SCORE=<x>`, parsed back
    out of stdout. Any non-zero exit / missing score raises SandboxError —
    never a fabricated score (AGENTS.md #1).

    API surface used (docs.tokenfactory.nebius.com, sandboxes):
      POST /v1/instances            -> 201 + Location: /v1/operations/{id}
      GET  /v1/operations/{id}      -> status PENDING|ASSIGNED|EXECUTING|
                                       SUCCESS|FAILED|CANCELLED, metadata.result
      POST /v1/images/import        -> only if CONTREE_IMAGE is unset (see
                                       _resolve_image; body shape inferred from
                                       the operation metadata schema — verify).
    Auth: `Authorization: Bearer <token>` + `Project: <project id>` header.

    Env: NEBIUS_API_KEY (or CONTREE_TOKEN), CONTREE_PROJECT, optional
    CONTREE_URL (default https://api.tokenfactory.nebius.com/sandboxes),
    CONTREE_IMAGE (image UUID or `tag:...` containing python3 + git + pip;
    strongly recommended — pick one from `contree images`).

    Requests that CREATE operations are never auto-retried (an ambiguous
    response could duplicate a run); status GETs are retried on transient errors.
    """

    _DEFAULT_URL = "https://api.tokenfactory.nebius.com/sandboxes"
    _DEFAULT_IMPORT_REF = "docker://docker.io/library/python:3.12"
    _IMPORT_TAG = "culprit/python-3.12"
    _TERMINAL = {"SUCCESS", "FAILED", "CANCELLED"}

    def __init__(
        self, repo_path: str, api_key: str | None = None, *, project: str | None = None,
        base_url: str | None = None, image: str | None = None,
        timeout_s: int = 600, poll_interval_s: float = 1.0,
        client: "httpx.Client | None" = None,
    ):
        import httpx  # local: LocalGitSandbox users don't need it

        self.repo_path = Path(repo_path).resolve()
        if not (self.repo_path / ".git").exists():
            raise SandboxError(f"{self.repo_path} is not a git repo root")
        self.api_key = api_key or os.environ.get("CONTREE_TOKEN") or os.environ.get("NEBIUS_API_KEY")
        if not self.api_key:
            raise SandboxError("NEBIUS_API_KEY (or CONTREE_TOKEN) not set — cannot create a Token Factory sandbox")
        self.project = project or os.environ.get("CONTREE_PROJECT") or os.environ.get("NEBIUS_AI_PROJECT")
        if not self.project:
            raise SandboxError("CONTREE_PROJECT (or NEBIUS_AI_PROJECT) not set — the sandbox API requires a `Project` header")
        self.base = (base_url or os.environ.get("CONTREE_URL") or self._DEFAULT_URL).rstrip("/") + "/v1"
        self._image = image or os.environ.get("CONTREE_IMAGE")
        self.timeout_s = timeout_s
        self.poll_interval_s = poll_interval_s
        self._http = client or httpx.Client(timeout=60.0)

    # -- HTTP helpers -------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Project": self.project}

    def _post(self, path: str, body: dict):
        import httpx
        try:
            r = self._http.post(self.base + path, json=body, headers=self._headers())
        except httpx.HTTPError as e:  # NOT retried on purpose — see class docstring
            raise SandboxError(f"sandbox API request failed: {e}") from e
        if r.status_code not in (200, 201, 202):
            raise SandboxError(f"sandbox API {path} returned {r.status_code}: {r.text[:500]}")
        return r

    def _get_operation(self, op_id: str) -> dict:
        import httpx
        last: Exception | None = None
        for delay in (0.0, 0.5, 1.5):
            if delay:
                time.sleep(delay)
            try:
                r = self._http.get(f"{self.base}/operations/{op_id}", headers=self._headers())
                if r.status_code in (429, 500, 502, 503, 504):
                    last = SandboxError(f"operation status {r.status_code}")
                    continue
                if r.status_code != 200:
                    raise SandboxError(f"operation {op_id} status {r.status_code}: {r.text[:500]}")
                return r.json()
            except httpx.HTTPError as e:
                last = e
        raise SandboxError(f"could not read operation {op_id}: {last}")

    def _wait(self, op_id: str) -> dict:
        deadline = time.monotonic() + self.timeout_s
        while True:
            op = self._get_operation(op_id)
            if op.get("status") in self._TERMINAL:
                return op
            if time.monotonic() > deadline:
                try:
                    self._http.delete(f"{self.base}/operations/{op_id}", headers=self._headers())
                except Exception:  # best-effort cancel
                    pass
                raise SandboxError(f"sandbox operation {op_id} did not finish within {self.timeout_s}s")
            time.sleep(self.poll_interval_s)

    @staticmethod
    def _decode(stream: dict | None) -> str:
        if not stream:
            return ""
        val = stream.get("value", "")
        if stream.get("encoding") == "base64":
            return base64.b64decode(val).decode("utf-8", errors="replace")
        return val

    # -- image --------------------------------------------------------------
    def _resolve_image(self) -> str:
        if self._image:
            return self._image
        # UNVERIFIED body shape (inferred from ImageImportMetadata) — prefer
        # setting CONTREE_IMAGE. Cached on the instance so we import once.
        r = self._post("/images/import", {
            "registry": {"url": self._DEFAULT_IMPORT_REF}, "tag": self._IMPORT_TAG, "timeout": 300,
        })
        op_id = r.headers.get("Location", "").rsplit("/", 1)[-1] or r.json().get("uuid")
        op = self._wait(op_id)
        image = op.get("result_image_uuid") or (op.get("result") or {}).get("image")
        if op.get("status") != "SUCCESS" or not image:
            raise SandboxError(f"image import failed: {op.get('error')}")
        self._image = image
        log.info("sandbox.tf.image_imported", image=image)
        return image

    # -- core ---------------------------------------------------------------
    def _git_archive_b64(self, commit_sha: str) -> str:
        try:
            proc = subprocess.run(
                ["git", "archive", "--format=tar.gz", commit_sha],
                cwd=self.repo_path, capture_output=True, timeout=120,
            )
        except subprocess.TimeoutExpired as e:
            raise SandboxError(f"git archive timed out (120s) for {commit_sha}") from e
        if proc.returncode != 0:
            raise SandboxError(f"git archive failed for {commit_sha}: {proc.stderr.decode(errors='replace')}")
        return base64.b64encode(proc.stdout).decode("ascii")

    _SCRIPT = r"""set -e
mkdir -p /work && cd /work
base64 -d | tar xz -C /work
if [ -n "$CULPRIT_PATCH_B64" ]; then
  echo "$CULPRIT_PATCH_B64" | base64 -d > /tmp/culprit.patch
  git apply --whitespace=fix /tmp/culprit.patch || patch -p1 < /tmp/culprit.patch
fi
for f in requirements.txt requirements.lock; do
  if [ -f "$f" ]; then pip install -q -r "$f" 1>&2; break; fi
done
i=0
while [ "$i" -lt "$CULPRIT_N_RUNS" ]; do
  out=$(sh -c "$CULPRIT_BENCH")
  echo "CULPRIT_SCORE=$(printf '%s\n' "$out" | tail -n 1)"
  i=$((i+1))
done
"""

    def _run(self, commit_sha: str, benchmark_command: str, n_runs: int, patch: str | None) -> BenchmarkResult:
        image = self._resolve_image()
        env = {"CULPRIT_N_RUNS": str(n_runs), "CULPRIT_BENCH": benchmark_command}
        if patch is not None:
            env["CULPRIT_PATCH_B64"] = base64.b64encode(patch.encode()).decode("ascii")
        body = {
            "command": self._SCRIPT, "shell": True, "image": image, "disposable": True,
            "env": env, "timeout": self.timeout_s,
            "stdin": {"value": self._git_archive_b64(commit_sha), "encoding": "ascii", "close": True},
            "networking": {"enabled": True},
        }
        r = self._post("/instances", body)
        op_id = r.headers.get("Location", "").rsplit("/", 1)[-1] or r.json().get("uuid")
        if not op_id:
            raise SandboxError("sandbox API did not return an operation id")
        self._notify("instance", phase="start", commit=commit_sha, operation=op_id,
                     patched=patch is not None, disposable=True, image=image)
        t_vm = time.perf_counter()
        op = self._wait(op_id)
        self._notify("instance", phase="end", commit=commit_sha, operation=op_id,
                     status=op.get("status"), dur_s=round(time.perf_counter() - t_vm, 3))

        result = (op.get("metadata") or {}).get("result") or {}
        stdout = self._decode(result.get("stdout"))
        stderr = self._decode(result.get("stderr"))
        state = result.get("state") or {}
        if op.get("status") != "SUCCESS" or state.get("timed_out") or state.get("exit_code") not in (0, None):
            msg = (
                f"sandbox run failed for {commit_sha} (status={op.get('status')}, "
                f"exit={state.get('exit_code')}, error={op.get('error')}): {stderr[-2000:]}"
            )
            # The VM ran the script and it failed → the benchmark (or the
            # patched code) is at fault, not the infrastructure.
            if op.get("status") == "SUCCESS" and (state.get("timed_out") or state.get("exit_code") not in (0, None)):
                raise BenchmarkRunError(msg, detail=stderr[-1500:])
            raise SandboxError(msg)
        scores: list[float] = []
        for line in stdout.splitlines():
            if line.startswith("CULPRIT_SCORE="):
                try:
                    scores.append(float(line.split("=", 1)[1]))
                except ValueError as e:
                    raise SandboxError(f"non-numeric benchmark score line: {line!r}") from e
        if len(scores) != n_runs:
            raise BenchmarkRunError(
                f"expected {n_runs} benchmark scores for {commit_sha}, got {len(scores)}: {stdout[-1000:]!r}",
                detail=f"expected {n_runs} scores, got {len(scores)}",
            )
        for i, sc in enumerate(scores, 1):
            log.info("sandbox.run", backend="token_factory", commit=commit_sha, run=i, score=sc,
                     patched=patch is not None, operation=op_id)
            # All N runs execute inside ONE VM and come back together, so these
            # are reported as a batch — the UI labels them as such rather than
            # implying each streamed in live.
            self._notify("run", commit=commit_sha, run=i, n_runs=n_runs, score=sc,
                         patched=patch is not None, batched=True, operation=op_id)
        return BenchmarkResult(
            commit_sha=commit_sha, raw_scores=scores, stdout_tail=stdout[-500:], patched=patch is not None,
        )

    def run_benchmark(
        self, commit_sha: str, benchmark_command: str, n_runs: int = 5
    ) -> BenchmarkResult:
        return self._run(commit_sha, benchmark_command, n_runs, None)

    def apply_patch_and_benchmark(
        self, commit_sha: str, patch: str, benchmark_command: str, n_runs: int = 5
    ) -> BenchmarkResult:
        return self._run(commit_sha, benchmark_command, n_runs, patch)


def get_sandbox(repo_url_or_path: str, use_token_factory: bool = False) -> Sandbox:
    """Factory — the only place that should decide which backend is in play."""
    if use_token_factory:
        return TokenFactorySandbox(repo_url_or_path)
    return LocalGitSandbox(repo_url_or_path)
