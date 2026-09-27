# backend/tests/conftest.py
"""
Puts backend/ on sys.path once, for the whole test session, so every
test_*.py file can `import api`, `import bisector`, etc. directly without
repeating its own `sys.path.insert(0, ...)` boilerplate.

CODE_REVIEW_FINDINGS.md #16: this exact three-line block (import sys; from
pathlib import Path; sys.path.insert(...)) was copy-pasted verbatim at the
top of all seven test_*.py files — a `pip install -e .` / a proper
pyproject.toml + src-layout would be the more standard fix, but that's a
bigger structural change than this review's scope; conftest.py is
pytest's own designed mechanism for exactly this "make the package under
test importable" problem, and de-duplicating into one file here is a
purely mechanical, zero-behavior-change cleanup.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
