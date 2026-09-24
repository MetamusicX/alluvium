#!/usr/bin/env python3
"""
Alluvium — Frontmatter Audit
Lists notes whose `tags` or `related` frontmatter looks damaged by the
pre-fix pipeline (see normalize_tags / normalize_related in llm.py).

Read-only by default. With --fix, recoverable `related` damage is repaired
in place (see fix_note). Exits 0 when nothing is left to fix by hand, 1 otherwise.

What older versions could leave behind:
  - tags split into letters      `tags: solo` + a new tag  ->  [l, o, run, s]
  - related split into characters `related: "[[Note]]"`     ->  ['[', '[', 'N', ...]
  - related as a Python list repr `related: [[Note]]`        ->  ["['Note']"]
  - related double-bracketed      LLM sent "[[Note]]"         ->  "[[[[Note]]]]"

Split tags were stored as a sorted set, so the original word cannot be
recovered and must be fixed by hand. The related damage is recoverable: the
suggested value is shown, and --fix writes it.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).parent

NOTE_FOLDERS = [
    "01 Inbox",
    "1 Projects",
    "2 Areas",
    "3 Resources",
    "4 Archive",
    "People",
    "Authors",
]


def read_frontmatter(path: Path) -> dict | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        fm = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return None
    return fm if isinstance(fm, dict) else None


def check_tags(tags) -> list[str]:
    if not isinstance(tags, list):
        return []
    letters = [t for t in tags if isinstance(t, str) and len(t) == 1]
    # One single-character tag can be legitimate; two or more is the split signature.
    if len(letters) >= 2:
        return [f"tags look split into letters: {letters} — original word lost, fix by hand"]
    return []


def _repr_title(entry: str) -> str | None:
    """"['Note']" -> "Note" (what str() of YAML's [["Note"]] produced)."""
    if not (entry.startswith("[") and not entry.startswith("[[")):
        return None
    try:
        value = ast.literal_eval(entry)
    except (ValueError, SyntaxError):
        return None
    while isinstance(value, list) and len(value) == 1:
        value = value[0]
    return value if isinstance(value, str) else None


def repair_related(related) -> tuple[list[str], list | None]:
    """Return (problems, repaired list), or ([], None) when `related` is not damaged."""
    if not isinstance(related, list):
        return [], None
    problems = []
    suggested = []
    damaged = False
    chars = []

    def flush_chars():
        nonlocal damaged
        if len(chars) >= 2:
            damaged = True
            joined = "".join(chars)
            problems.append(f"related split into characters: {chars}")
            suggested.append(joined if joined.startswith("[[") else f"[[{joined}]]")
        else:
            suggested.extend(chars)
        chars.clear()

    for entry in related:
        if isinstance(entry, str) and len(entry) == 1:
            chars.append(entry)
            continue
        flush_chars()
        if not isinstance(entry, str):
            suggested.append(entry)
            continue
        title = _repr_title(entry)
        if title is not None:
            damaged = True
            problems.append(f"related entry is a list repr: {entry!r}")
            suggested.append(f"[[{title}]]")
            continue
        if entry.startswith("[[[["):
            damaged = True
            fixed = entry
            while fixed.startswith("[[[[") and fixed.endswith("]]]]"):
                fixed = fixed[2:-2]
            problems.append(f"related entry is double-bracketed: {entry!r}")
            suggested.append(fixed)
            continue
        suggested.append(entry)
    flush_chars()

    if not damaged:
        return [], None
    deduped = []
    for entry in suggested:
        if entry not in deduped:
            deduped.append(entry)
    return problems, deduped


def check_related(related) -> list[str]:
    problems, suggested = repair_related(related)
    if suggested is not None:
        problems.append(f"suggested related: {suggested}")
    return problems


def _replace_related_block(fm_text: str, related: list) -> str | None:
    """Swap only the `related:` key (and its continuation lines) in raw frontmatter text."""
    lines = fm_text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if re.match(r"related\s*:", line)]
    if len(starts) != 1:
        return None
    start = end = starts[0]
    # Block-style items ("- x", at column 0 as yaml.dump writes them, or indented)
    # and wrapped flow lists continue the key until the next top-level line.
    while end + 1 < len(lines) and lines[end + 1].startswith((" ", "\t", "-")):
        end += 1
    block = yaml.dump({"related": related}, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return "".join(lines[:start]) + block + "".join(lines[end + 1:])


def fix_note(path: Path) -> str:
    """Repair recoverable `related` damage in place.

    Returns "fixed", "clean" (nothing recoverable to fix) or "skipped". Only the
    `related` lines change: the body, other keys, comments and date_modified are
    left byte-for-byte as they were, and the file is written only if re-parsing
    shows `related` is the sole difference.
    """
    text = path.read_text(encoding="utf-8")
    fm = read_frontmatter(path)
    if fm is None:
        return "clean"
    _, repaired = repair_related(fm.get("related"))
    if repaired is None:
        return "clean"
    parts = text.split("---", 2)
    new_fm_text = _replace_related_block(parts[1], repaired)
    if new_fm_text is None:
        return "skipped"
    try:
        new_fm = yaml.safe_load(new_fm_text)
    except yaml.YAMLError:
        return "skipped"
    if new_fm != {**fm, "related": repaired}:
        return "skipped"
    path.write_text("---" + new_fm_text + "---" + parts[2], encoding="utf-8")
    return "fixed"


def audit(root: Path) -> dict[Path, list[str]]:
    findings = {}
    for folder in NOTE_FOLDERS:
        base = root / folder
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.md")):
            fm = read_frontmatter(path)
            if fm is None:
                continue
            problems = check_tags(fm.get("tags")) + check_related(fm.get("related"))
            if problems:
                findings[path.relative_to(root)] = problems
    return findings


def main():
    parser = argparse.ArgumentParser(description="List notes with damaged tags/related frontmatter.")
    parser.add_argument("root", nargs="?", type=Path, default=BASE_DIR,
                        help="Vault root (defaults to this Alluvium folder).")
    parser.add_argument("--fix", action="store_true",
                        help="Repair recoverable `related` damage in place. Split tags still need fixing by hand.")
    args = parser.parse_args()

    findings = audit(args.root)
    if not findings:
        print("No damaged tags or related links found.")
        sys.exit(0)

    print(f"Found {len(findings)} note(s) with damaged frontmatter:\n")
    for path, problems in findings.items():
        print(path)
        for msg in problems:
            print(f"  - {msg}")
        print()

    if not args.fix:
        print("Run again with --fix to repair the related links above (tags must be fixed by hand).")
        sys.exit(1)

    fixed, skipped = [], []
    for path in findings:
        status = fix_note(args.root / path)
        if status == "fixed":
            fixed.append(path)
        elif status == "skipped":
            skipped.append(path)

    print(f"Fixed related links in {len(fixed)} note(s).")
    for path in fixed:
        print(f"  ✓ {path}")
    if skipped:
        print(f"\nSkipped {len(skipped)} note(s) — frontmatter layout too unusual to edit safely; fix by hand:")
        for path in skipped:
            print(f"  ✗ {path}")

    remaining = audit(args.root)
    if remaining:
        print(f"\n{len(remaining)} note(s) still need fixing by hand (re-run without --fix to see them).")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
