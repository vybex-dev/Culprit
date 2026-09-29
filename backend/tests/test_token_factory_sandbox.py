"""TokenFactorySandbox tests: mocked ConTree HTTP + the real in-VM shell
script executed locally, so the protocol AND the script are both exercised.
Does NOT prove the live Nebius API matches — do one real smoke run."""
import base64, json, os, subprocess, sys
import httpx, pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from sandbox_client import TokenFactorySandbox, SandboxError


def _repo(tmp_path):
    r = tmp_path / "repo"; r.mkdir()
    run = lambda *a: subprocess.run(a, cwd=r, check=True, capture_output=True)
    run("git", "init", "-q"); run("git", "config", "user.email", "a@b"); run("git", "config", "user.name", "a")
    (r / "bench.py").write_text("print('noise')\nprint(12.5)\n")
    run("git", "add", "."); run("git", "commit", "-qm", "c1")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=r, capture_output=True, text=True).stdout.strip()
    return r, sha


def _fake_api(captured, *, fail=False):
    """Executes the submitted script locally to emulate the VM."""
    def handler(req: httpx.Request):
        assert req.headers["Authorization"] == "Bearer k" and req.headers["Project"] == "p"
        if req.method == "POST" and req.url.path.endswith("/instances"):
            body = json.loads(req.content); captured.update(body)
            return httpx.Response(201, headers={"Location": "/v1/operations/op1"}, json={"uuid": "op1"})
        if req.method == "GET" and req.url.path.endswith("/operations/op1"):
            body = captured
            stdin = body["stdin"]["value"].encode()
            work = "/tmp/work_test"
            script = body["command"].replace("/work", work).replace("pip install", "true")
            proc = subprocess.run(["sh", "-c", script], input=stdin, capture_output=True,
                                  env={**os.environ, **body["env"]})
            state = {"exit_code": 1 if fail else proc.returncode}
            enc = lambda b: {"value": base64.b64encode(b).decode(), "encoding": "base64"}
            return httpx.Response(200, json={
                "status": "FAILED" if fail or proc.returncode else "SUCCESS", "error": None,
                "metadata": {"result": {"state": state, "stdout": enc(proc.stdout), "stderr": enc(proc.stderr)}}})
        return httpx.Response(404)
    return handler


def _sb(repo, handler):
    return TokenFactorySandbox(str(repo), api_key="k", project="p", image="tag:python:3.12",
                               poll_interval_s=0, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_run_benchmark_returns_all_scores(tmp_path):
    repo, sha = _repo(tmp_path); cap = {}
    res = _sb(repo, _fake_api(cap)).run_benchmark(sha, "python bench.py".replace("python", sys.executable), n_runs=3)
    assert res.raw_scores == [12.5, 12.5, 12.5]
    assert cap["disposable"] is True and cap["shell"] is True and cap["image"] == "tag:python:3.12"


def test_patch_applied(tmp_path):
    repo, sha = _repo(tmp_path); cap = {}
    patch = subprocess.run(["sh", "-c", "sed -i 's/12.5/7.5/' bench.py && git diff && git checkout -q bench.py"],
                           cwd=repo, capture_output=True, text=True).stdout
    res = _sb(repo, _fake_api(cap)).apply_patch_and_benchmark(sha, patch, f"{sys.executable} bench.py", n_runs=2)
    assert res.raw_scores == [7.5, 7.5] and res.patched


def test_failure_raises_not_fabricates(tmp_path):
    repo, sha = _repo(tmp_path)
    with pytest.raises(SandboxError):
        _sb(repo, _fake_api({}, fail=True)).run_benchmark(sha, f"{sys.executable} bench.py", n_runs=2)


def test_missing_project_raises(tmp_path, monkeypatch):
    repo, _ = _repo(tmp_path); monkeypatch.delenv("CONTREE_PROJECT", raising=False)
    with pytest.raises(SandboxError):
        TokenFactorySandbox(str(repo), api_key="k")
