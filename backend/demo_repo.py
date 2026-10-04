# backend/demo_repo.py
"""
demo_repo.py — builds a small, real git repository ("orders-service") whose
history contains one genuine performance regression.

This is a *bundled sample*, not a substitute for the real-OSS demo cases in
demo_cases/ (AGENTS.md prefers those for the submission video). What makes it
honest rather than a mock:
  * It is a real git repo with real commits, authors and dates.
  * The regression is real code: a "simplify loading" refactor that turns one
    batched query into one query per order (a classic N+1). Every benchmark
    number Culprit reports for it comes from actually timing that code.
  * The simulated part is only the database round-trip (a fixed sleep per
    query), which is how an in-process fake stands in for network latency.

It exists so a judge or contributor can click "Run the bundled demo" and watch
the whole pipeline — with or without API keys — in about a minute.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

GUILTY_SUBJECT = "refactor: simplify order summary loading"
BASELINE_SUBJECT = "bench: add order-summary benchmark"  # first commit that can be benchmarked
BENCHMARK_COMMAND = "python bench.py"

_DB = '''"""Tiny in-memory stand-in for a database. Every query costs one simulated
network round-trip, which is what makes call *counts* matter."""
import time

QUERY_LATENCY_S = 0.0015

_ORDERS = [{"id": i, "customer_id": 1, "status": "paid"} for i in range(1, 61)]
_ORDERS += [{"id": 100 + i, "customer_id": 2, "status": "paid"} for i in range(1, 9)]
_LINE_ITEMS = [
    {"id": o["id"] * 10 + n, "order_id": o["id"], "sku": f"SKU-{n}", "qty": n + 1, "unit_cents": 1250 * (n + 1)}
    for o in _ORDERS for n in range(3)
]
_TABLES = {"orders": _ORDERS, "line_items": _LINE_ITEMS}


class DB:
    def query(self, table, **filters):
        time.sleep(QUERY_LATENCY_S)
        return [dict(r) for r in _TABLES[table] if all(r[k] == v for k, v in filters.items())]

    def query_in(self, table, column, values):
        time.sleep(QUERY_LATENCY_S)
        wanted = set(values)
        return [dict(r) for r in _TABLES[table] if r[column] in wanted]
'''

_SUMMARY_BATCHED = '''from collections import defaultdict

from .serialize import serialize_order


def get_order_summary(db, customer_id):
    orders = db.query("orders", customer_id=customer_id)
    order_ids = [o["id"] for o in orders]
    items = db.query_in("line_items", "order_id", order_ids)
    items_by_order = defaultdict(list)
    for item in items:
        items_by_order[item["order_id"]].append(item)
    for order in orders:
        order["line_items"] = items_by_order.get(order["id"], [])

    return [serialize_order(o) for o in orders]
'''

_SUMMARY_GUILTY = '''from .serialize import serialize_order


def get_order_summary(db, customer_id):
    orders = db.query("orders", customer_id=customer_id)
    for order in orders:
        order["line_items"] = db.query("line_items", order_id=order["id"])

    return [serialize_order(o) for o in orders]
'''

_SERIALIZE = '''def serialize_order(order):
    total = sum(i["qty"] * i["unit_cents"] for i in order["line_items"])
    return {"id": order["id"], "status": order["status"], "total_cents": total, "items": len(order["line_items"])}
'''

_BENCH = '''"""Benchmark: time three order-summary calls. Prints milliseconds (last line)."""
import time

from app.db import DB
from app.summary import get_order_summary

db = DB()
get_order_summary(db, 1)  # warm-up (imports, first-call costs)
start = time.perf_counter()
for _ in range(3):
    get_order_summary(db, 1)
print(round((time.perf_counter() - start) * 1000, 3))
'''

_AUTHORS = [
    ("Ana Ruiz", "ana@orders.example"),
    ("Dev Patel", "dev@orders.example"),
    ("Mei Lin", "mei@orders.example"),
    ("Sam Okafor", "sam@orders.example"),
]


@dataclass
class DemoRepo:
    path: str
    start_sha: str
    end_sha: str
    guilty_sha: str
    n_commits: int
    benchmark_command: str = BENCHMARK_COMMAND


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, env={**os.environ, **(env or {})},
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout.strip()


def _write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _describe(repo: Path) -> DemoRepo:
    shas = _git(repo, "rev-list", "--reverse", "HEAD").splitlines()
    guilty = _git(repo, "log", "--format=%H", f"--grep={GUILTY_SUBJECT}", "-1")
    # The known-good baseline is the commit that *introduced* the benchmark —
    # earlier commits have nothing to run.
    start = _git(repo, "log", "--format=%H", f"--grep={BASELINE_SUBJECT}", "-1")
    return DemoRepo(
        path=str(repo), start_sha=start, end_sha=shas[-1], guilty_sha=guilty,
        n_commits=len(shas) - shas.index(start),
    )


def build_demo_repo(dest: str | Path) -> DemoRepo:
    """Create (or reuse, if already built) the sample repo at `dest`."""
    repo = Path(dest)
    if (repo / ".git").exists():
        try:
            return _describe(repo)
        except Exception:  # noqa: BLE001 — half-built repo: rebuild from scratch
            import shutil

            shutil.rmtree(repo, ignore_errors=True)
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "demo@orders.example")
    _git(repo, "config", "user.name", "Culprit Demo")

    day = 24 * 3600
    base = 1788800000  # 2026-09-07 — fixed so the sample is reproducible

    # (subject, {path: content}) — the guilty change is commit #11 of 22.
    story: list[tuple[str, dict[str, str]]] = [
        ("chore: project skeleton", {"README.md": "# orders-service\n", "requirements.txt": "# no runtime deps\n",
                                     "app/__init__.py": "", "app/db.py": _DB}),
        ("feat: order summary with batched line-item lookup", {"app/summary.py": _SUMMARY_BATCHED,
                                                              "app/serialize.py": _SERIALIZE}),
        ("bench: add order-summary benchmark", {"bench.py": _BENCH}),
        ("docs: describe the summary endpoint", {"README.md": "# orders-service\n\nSummarises a customer's orders.\n"}),
        ("chore: add .gitignore", {".gitignore": "__pycache__/\n"}),
        ("style: tidy serialize_order", {"app/serialize.py": _SERIALIZE.replace("def serialize_order(order):", 'def serialize_order(order):\n    """Flatten an order for the API."""')}),
        ("docs: document benchmark usage", {"README.md": "# orders-service\n\nSummarises a customer's orders.\n\nRun `python bench.py`.\n"}),
        ("chore: pin python version note", {"requirements.txt": "# no runtime deps\n# python>=3.10\n"}),
        ("feat: include item count in summary", {"app/serialize.py": _SERIALIZE}),
        ("test: add smoke test placeholder", {"tests/test_smoke.py": "def test_smoke():\n    assert True\n"}),
        (GUILTY_SUBJECT, {"app/summary.py": _SUMMARY_GUILTY}),
        ("docs: changelog for 0.2", {"CHANGELOG.md": "## 0.2\n- summary loading simplified\n"}),
        ("chore: bump version metadata", {"VERSION": "0.2.0\n"}),
        ("feat: expose status in summary", {"app/serialize.py": _SERIALIZE + "\n# status is included\n"}),
        ("docs: clarify readme", {"README.md": "# orders-service\n\nSummarises a customer's orders.\n\nRun `python bench.py`.\nSee CHANGELOG.md.\n"}),
        ("test: second smoke test", {"tests/test_smoke.py": "def test_smoke():\n    assert True\n\n\ndef test_again():\n    assert 1 + 1 == 2\n"}),
        ("chore: editorconfig", {".editorconfig": "root = true\n"}),
        ("style: trailing newline in db", {"app/db.py": _DB.replace("QUERY_LATENCY_S = 0.0015", "QUERY_LATENCY_S = 0.0015  # simulated round-trip")}),
        ("docs: contributing note", {"CONTRIBUTING.md": "Open a PR.\n"}),
        ("chore: license header note", {"NOTICE": "Sample project for Culprit.\n"}),
        ("docs: update changelog", {"CHANGELOG.md": "## 0.2\n- summary loading simplified\n## 0.3\n- docs\n"}),
        ("chore: release 0.3", {"VERSION": "0.3.0\n"}),
    ]

    for i, (subject, files) in enumerate(story):
        for rel, text in files.items():
            _write(repo, rel, text)
        name, email = _AUTHORS[i % len(_AUTHORS)]
        stamp = f"{base + i * day // 2} +0000"
        _git(repo, "add", "-A")
        _git(
            repo, "commit", "-q", "-m", subject,
            env={
                "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_AUTHOR_DATE": stamp,
                "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email, "GIT_COMMITTER_DATE": stamp,
            },
        )
    return _describe(repo)
