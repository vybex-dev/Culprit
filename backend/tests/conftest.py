# backend/tests/conftest.py
"""
Puts backend/ on sys.path once, for the whole test session, so every
test_*.py file can `import api`, `import bisector`, etc. directly.

Also pins the test environment: an in-memory database (importing `api` builds
a module-level app, which must never create files), and no ambient offline
mode / Token Factory settings leaking in from a developer's shell or .env.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["CULPRIT_DB"] = "memory"
for _var in ("CULPRIT_OFFLINE", "USE_TOKEN_FACTORY", "CULPRIT_ALLOW_LOCAL_REPOS"):
    os.environ.pop(_var, None)
