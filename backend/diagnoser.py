"""Root-cause classification. Calls Nemotron 3 Ultra + Tavily.

Spec: docs/build-prompts/BUILD_02_BACKEND_AGENTS.md, section 1
Schema: docs/AGENT_SPECS.md §2

Owns: the cited_lines-verification check described in the spec
(don't just trust the model's claimed citations — verify them
against the raw diff before accepting the result).

TODO: implement per BUILD_02_BACKEND_AGENTS.md.
"""
