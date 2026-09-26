"""Nemotron API wrapper — shared by every agent (Nano + Ultra calls).

Spec: docs/build-prompts/BUILD_01_BACKEND_CORE.md, section 2

Owns: malformed-JSON retry policy, prompt+response logging keyed by
job_id + step, and the model-routing contract (nano vs ultra).

TODO: implement per BUILD_01_BACKEND_CORE.md.
"""
