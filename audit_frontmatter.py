#!/usr/bin/env python3
"""
Alluvium — Frontmatter Audit
Lists notes whose `tags` or `related` frontmatter looks damaged by the
pre-fix pipeline (see normalize_tags / normalize_related in llm.py).

Read-only: nothing is written. Exits 0 if no damage is found, 1 otherwise.

What older versions could leave behind:
  - tags split into letters      `tags: solo` + a new tag  ->  [l, o, run, s]
  - related split into characters `related: "[[Note]]"`     ->  ['[', '[', 'N', ...]
  - related as a Python list repr `related: [[Note]]`        ->  ["['Note']"]
  - related double-bracketed      LLM sent "[[Note]]"         ->  "[[[[Note]]]]"

Split tags were stored as a sorted set, so the original word cannot be
recovered and must be fixed by hand. The related damage is recoverable, and
the suggested value is shown.
"""

from __future__ import annotations

import argparse
import ast
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


def check_related(related) -> list[str]:
    if not isinstance(related, list):
        return []
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

    if damaged:
        deduped = list(dict.fromkeys(suggested))
        problems.append(f"suggested related: {deduped}")
    return problems


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
    parser = argparse.ArgumentParser(description="List notes with damaged tags/related frontmatter (read-only).")
    parser.add_argument("root", nargs="?", type=Path, default=BASE_DIR,
                        help="Vault root (defaults to this Alluvium folder).")
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
    sys.exit(1)


if __name__ == "__main__":
    main()
