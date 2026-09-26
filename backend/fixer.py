"""Patch generation + retry loop (max 3 attempts). Calls Nemotron 3 Ultra.

Spec: docs/build-prompts/BUILD_02_BACKEND_AGENTS.md, section 2
Schema: docs/AGENT_SPECS.md §3

This file proposes patches only — verification (apply in sandbox, re-run
benchmark) lives in sandbox_client.py / api.py, not here. Never set
resolved/verified True from inside this module.

TODO: implement per BUILD_02_BACKEND_AGENTS.md.
"""
