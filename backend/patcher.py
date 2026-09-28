# backend/patcher.py
"""
patcher.py — turn the Fixer's exact-text EDITS into a real unified diff.

Why this exists: LLMs can't reliably hand-write unified diffs (hunk line
counts, context lines, whitespace, and +/- direction all have to be exactly
right, and `git apply` rejects the whole patch on any slip). Copying a short
snippet verbatim out of a file and saying what to replace it with is a much
easier job for a model, so the Fixer returns edits and THIS module builds the
diff deterministically with difflib. The rest of the pipeline is unchanged:
it still receives one unified-diff string, applied with `git apply`.
"""

from __future__ import annotations

import difflib

_FILE_MARKER = "# FILE: "


class PatchBuildError(Exception):
    """An edit couldn't be applied to the file text it was written against
    (unknown file, `old` text missing, or `old` text not unique). The message
    is written to be fed back to the model on a retry."""


def parse_file_blocks(full_file_contents: str) -> dict[str, str]:
    """Inverse of api.py's _concat_file_contents_at_commit(): blocks are
    '# FILE: <path>\\n<contents>' joined by a blank line. Splitting on the
    full '\\n\\n# FILE: ' separator (not any line starting with '# FILE:')
    keeps a file that itself contains such a comment intact."""
    text = full_file_contents
    if not text.startswith(_FILE_MARKER):
        return {}
    files: dict[str, str] = {}
    for block in text[len(_FILE_MARKER):].split("\n\n" + _FILE_MARKER):
        path, _, contents = block.partition("\n")
        files[path.strip()] = contents
    return files


def edits_to_patch(full_file_contents: str, edits: list[dict]) -> str:
    """Apply `edits` ({"file","old","new"}) to the files in
    `full_file_contents` and return a unified diff (git-apply compatible,
    a/ b/ prefixes). Raises PatchBuildError with a model-readable message."""
    if not isinstance(edits, list) or not edits:
        raise PatchBuildError("'edits' must be a non-empty list of {file, old, new} objects")
    files = parse_file_blocks(full_file_contents)
    original = dict(files)
    updated = dict(files)

    for i, edit in enumerate(edits, start=1):
        if not isinstance(edit, dict) or not {"file", "old", "new"} <= edit.keys():
            raise PatchBuildError(f"edit {i} must have 'file', 'old' and 'new'")
        path, old, new = edit["file"], edit["old"], edit["new"]
        if path not in updated:
            raise PatchBuildError(f"edit {i}: unknown file {path!r}; known files: {sorted(updated)}")
        if not isinstance(old, str) or not old:
            raise PatchBuildError(f"edit {i}: 'old' must be a non-empty string copied exactly from {path}")
        if not isinstance(new, str):
            raise PatchBuildError(f"edit {i}: 'new' must be a string (use \"\" to delete)")
        count = updated[path].count(old)
        if count == 0:
            raise PatchBuildError(
                f"edit {i}: the 'old' text was not found in {path}. Copy it exactly, "
                f"character for character including indentation. It started with: {old[:80]!r}"
            )
        if count > 1:
            raise PatchBuildError(
                f"edit {i}: the 'old' text appears {count} times in {path}; include more "
                f"surrounding lines so it matches exactly once. It started with: {old[:80]!r}"
            )
        updated[path] = updated[path].replace(old, new, 1)

    chunks: list[str] = []
    for path in updated:
        if updated[path] == original[path]:
            continue
        before = original[path]
        after = updated[path]
        if before and not before.endswith("\n"):
            before += "\n"
        if after and not after.endswith("\n"):
            after += "\n"
        diff = difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True),
            fromfile=f"a/{path}", tofile=f"b/{path}", n=3,
        )
        chunks.append("".join(diff))
    patch = "".join(chunks)
    if not patch.strip():
        raise PatchBuildError("the edits made no change to any file")
    return patch
