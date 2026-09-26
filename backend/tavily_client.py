"""Tavily grounding step for the Diagnoser.

Spec: docs/build-prompts/BUILD_02_BACKEND_AGENTS.md, section 1 ("Tavily grounding step")

Query pattern: "{category} performance issue {relevant library/function name}"
or "is {cited pattern} a known anti-pattern". Leave tavily_refs empty if
nothing relevant comes back — never fabricate a citation.

TODO: implement per BUILD_02_BACKEND_AGENTS.md.
"""
