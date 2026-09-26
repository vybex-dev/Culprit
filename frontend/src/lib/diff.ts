// FILE: frontend/src/lib/diff.ts — place at this path in the Culprit repo
//
// Small unified-diff parser. Backend agents (diagnoser.py, fixer.py) hand
// back plain unified-diff strings (see docs/AGENT_SPECS.md §2-3) — this
// turns one into renderable hunks/lines so DiffViewer can show an actual
// diff instead of a <pre> dump, and so it can line up the Diagnoser's
// `cited_lines` against the real diff content (BUILD_03: "render as an
// actual diff... this is the 'wow' moment").

export type DiffLineKind = "add" | "del" | "context" | "hunk" | "meta";

export interface DiffLine {
  kind: DiffLineKind;
  content: string; // without the leading +/-/space marker
  oldLineNo: number | null;
  newLineNo: number | null;
}

export interface DiffHunk {
  header: string;
  lines: DiffLine[];
}

const HUNK_RE = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/;

export function parseUnifiedDiff(diff: string): DiffHunk[] {
  const hunks: DiffHunk[] = [];
  let current: DiffHunk | null = null;
  let oldLine = 0;
  let newLine = 0;

  for (const raw of diff.split("\n")) {
    const hunkMatch = HUNK_RE.exec(raw);
    if (hunkMatch) {
      oldLine = parseInt(hunkMatch[1], 10);
      newLine = parseInt(hunkMatch[3], 10);
      current = { header: raw, lines: [] };
      hunks.push(current);
      continue;
    }
    if (raw.startsWith("--- ") || raw.startsWith("+++ ") || raw.startsWith("diff ") || raw.startsWith("index ")) {
      // File-level metadata outside any hunk — keep it out of the hunk
      // list entirely so it doesn't get treated as a headerless hunk.
      continue;
    }
    if (!current) {
      // Content before the first "@@" line (or a diff with no hunk
      // markers at all) — still worth showing, just without line numbers.
      current = { header: "", lines: [] };
      hunks.push(current);
    }
    if (raw.startsWith("+")) {
      current.lines.push({ kind: "add", content: raw.slice(1), oldLineNo: null, newLineNo: newLine });
      newLine++;
    } else if (raw.startsWith("-")) {
      current.lines.push({ kind: "del", content: raw.slice(1), oldLineNo: oldLine, newLineNo: null });
      oldLine++;
    } else if (raw.startsWith(" ")) {
      current.lines.push({ kind: "context", content: raw.slice(1), oldLineNo: oldLine, newLineNo: newLine });
      oldLine++;
      newLine++;
    } else if (raw.length > 0) {
      current.lines.push({ kind: "meta", content: raw, oldLineNo: null, newLineNo: null });
    }
  }

  return hunks;
}

function normalize(s: string): string {
  return s.trim();
}

/** A DiffLine is "cited" if any of the Diagnoser's cited_lines strings
 * matches it closely enough. Citations are documented as "exact line(s)
 * or short snippet from the diff" (AGENT_SPECS.md §2) — in practice that
 * means exact equality after trimming, or one containing the other
 * (a citation can be a fragment of a longer line, or vice versa). */
export function isCitedLine(line: DiffLine, citedLines: string[]): boolean {
  if (line.kind === "hunk" || line.kind === "meta") return false;
  const content = normalize(line.content);
  if (!content) return false;
  return citedLines.some((raw) => {
    // Citations sometimes retain the +/- prefix from the diff verbatim.
    const cited = normalize(raw.replace(/^[+\- ]/, ""));
    if (!cited) return false;
    return content === cited || content.includes(cited) || cited.includes(content);
  });
}
