#!/usr/bin/env python3
"""
Alluvium — Flow becomes knowledge
Reads a daily journal entry and extracts atomic notes with Obsidian-compatible YAML frontmatter.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

import anthropic
import yaml

# --- Paths ---
BASE_DIR = Path(__file__).parent
JOURNAL_DIR = BASE_DIR / "Journal"
NOTES_DIR = BASE_DIR / "Notes"
PEOPLE_DIR = BASE_DIR / "People"
PROJECTS_DIR = BASE_DIR / "Projects"
CONFIG_PATH = BASE_DIR / "config.yaml"

ALL_FOLDERS = [NOTES_DIR, PEOPLE_DIR, PROJECTS_DIR]


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def get_journal_path(target_date: date) -> Path:
    return JOURNAL_DIR / f"{target_date.isoformat()}.md"


def get_existing_notes() -> dict[str, Path]:
    """Return a map of slug -> filepath for all existing notes."""
    notes = {}
    for folder in ALL_FOLDERS:
        if folder.exists():
            for f in folder.glob("*.md"):
                notes[f.stem] = f
    return notes


def get_existing_titles(existing_notes: dict[str, Path]) -> list[str]:
    """Extract titles from YAML frontmatter of existing notes."""
    titles = []
    for slug, path in existing_notes.items():
        text = path.read_text(encoding="utf-8")
        if text.startswith("---"):
            try:
                fm_text = text.split("---", 2)[1]
                fm = yaml.safe_load(fm_text)
                if fm and "title" in fm:
                    titles.append(fm["title"])
                    continue
            except Exception:
                pass
        titles.append(slug.replace("-", " ").title())
    return titles


FEW_SHOT_EXAMPLE = """
## Example

**Journal entry (2026-03-10):**
Ran 12km this morning, easy pace around 5:30/km. Legs felt heavy from yesterday's bike.
Later had a call with Henrik about the panel review — we agreed to split the applications by discipline. Need to send him my half by Friday.
Reading Deleuze's "Difference and Repetition" again. The concept of the virtual is exactly what I need for my chapter on layered temporality.

**Extracted notes:**
[
  {
    "title": "12km Easy Run",
    "type": "practice-log",
    "domain": "sport",
    "tags": ["running", "easy-pace", "fatigue"],
    "related": ["Training"],
    "body": "12km easy run at ~5:30/km. Legs felt heavy from yesterday's bike session — cumulative fatigue building. Extracted from [[2026-03-10]]."
  },
  {
    "title": "Henrik",
    "type": "person",
    "domain": "work",
    "tags": ["evaluator", "panel-review"],
    "related": ["Panel Review Split"],
    "body": "Colleague involved in panel reviews. Discussed splitting applications by discipline. Extracted from [[2026-03-10]]."
  },
  {
    "title": "Panel Review Split",
    "type": "task",
    "domain": "work",
    "tags": ["evaluation", "deadline"],
    "related": ["Henrik"],
    "body": "Agreed with [[Henrik]] to split panel review applications by discipline. Need to send my half by Friday (2026-03-14). Extracted from [[2026-03-10]]."
  },
  {
    "title": "Deleuze — The Virtual and Layered Temporality",
    "type": "idea",
    "domain": "writing",
    "tags": ["deleuze", "difference-and-repetition", "temporality", "philosophy"],
    "related": ["Writing Projects"],
    "body": "Re-reading Deleuze's *Difference and Repetition*. The concept of the virtual connects directly to the chapter on layered temporality. The virtual as a structure that is real but not actual — worth developing further. Extracted from [[2026-03-10]]."
  }
]
"""


def build_extraction_prompt(journal_text: str, config: dict, target_date: str, existing_titles: list[str]) -> str:
    domains_desc = "\n".join(
        f"- **{d['name']}**: {d['description']} (keywords: {', '.join(d.get('keywords', []))})"
        for d in config["domains"].values()
    )
    note_types = ", ".join(config["note_types"])
    existing = ", ".join(existing_titles[:100]) if existing_titles else "(none yet)"

    return f"""You are a journal analyst for a personal knowledge management system. Your job is to read a daily journal entry and extract distinct atomic notes from it.

## Context — Life Domains
{domains_desc}

## Existing notes in the system (for linking)
{existing}

## Rules
1. Extract each distinct item as a separate note. One idea = one note. One event = one note. One task = one note.
2. Assign each note a type from: {note_types}
3. Assign a domain if one clearly fits. Use "personal" if none match.
4. Extract tags — emergent from content, not forced. Use lowercase, hyphenated. Include domain-relevant tags.
5. Identify people mentioned. For people NOT already in the existing notes list, create a person-type note.
6. Use [[wikilinks]] in the note body to reference other notes (both existing and newly created).
7. Keep the original voice and feeling. Don't sanitize or over-summarize.
8. For practice logs (training, music, etc.), include specific details: duration, what was practiced/trained, how it felt.
9. Every note body should end with: Extracted from [[{target_date}]].
10. If a person or concept already exists in the system, reference them with [[wikilinks]] but do NOT create a new note for them. Instead, include an "append_to" field with their exact title.
{FEW_SHOT_EXAMPLE}

## Output Format
Return a JSON array of objects, each with:
- "title": short descriptive title (will become the filename)
- "type": one of the note types
- "domain": primary domain key (from config.yaml domains)
- "tags": list of tags (lowercase, no #)
- "related": list of titles this note connects to (used as [[wikilinks]])
- "body": the note content in markdown, using [[wikilinks]] for connections
- "append_to": (optional) if this adds context to an existing note, put the existing note's title here

Journal date: {target_date}

## Journal Entry
{journal_text}

Return ONLY the JSON array. No markdown code fences, no commentary."""


def extract_notes(journal_text: str, config: dict, target_date: str, existing_titles: list[str]) -> list[dict]:
    client = anthropic.Anthropic()
    prompt = build_extraction_prompt(journal_text, config, target_date, existing_titles)

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = message.content[0].text.strip()

    # Strip markdown code fences if present
    if response_text.startswith("```"):
        response_text = re.sub(r"^```(?:json)?\n?", "", response_text)
        response_text = re.sub(r"\n?```$", "", response_text)

    return json.loads(response_text)


def slugify(title: str) -> str:
    """Convert a title to a filename-safe slug."""
    slug = title.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = slug.strip("-")
    return slug


def find_existing_note(title: str, existing_notes: dict[str, Path]) -> Path | None:
    slug = slugify(title)
    return existing_notes.get(slug)


def append_to_note(filepath: Path, note: dict, target_date: str):
    """Append new context to an existing note."""
    existing_content = filepath.read_text(encoding="utf-8")

    new_entry = f"\n\n---\n### Update — {target_date}\n\n{note.get('body', '')}"

    # Also update tags in frontmatter if new ones
    if existing_content.startswith("---"):
        parts = existing_content.split("---", 2)
        if len(parts) >= 3:
            try:
                fm = yaml.safe_load(parts[1])
                existing_tags = set(fm.get("tags", []))
                new_tags = set(note.get("tags", []))
                if new_tags - existing_tags:
                    fm["tags"] = sorted(existing_tags | new_tags)
                    fm["date_modified"] = target_date
                    updated = "---\n" + yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False) + "---" + parts[2] + new_entry
                    filepath.write_text(updated, encoding="utf-8")
                    print(f"  [append] {filepath.name} (updated tags + new context)")
                    return
            except Exception:
                pass

    # Fallback: just append
    filepath.write_text(existing_content + new_entry + "\n", encoding="utf-8")
    print(f"  [append] {filepath.name}")


def write_note(note: dict, target_date: str, existing_notes: dict[str, Path]):
    slug = slugify(note["title"])
    note_type = note.get("type", "note")

    # Check if this should append to an existing note
    append_target = note.get("append_to")
    if append_target:
        existing = find_existing_note(append_target, existing_notes)
        if existing:
            append_to_note(existing, note, target_date)
            return

    # Choose output folder
    if note_type == "person":
        folder = PEOPLE_DIR
    else:
        folder = NOTES_DIR

    filepath = folder / f"{slug}.md"

    # If note already exists, append instead of skipping
    if filepath.exists():
        append_to_note(filepath, note, target_date)
        return

    # Build YAML frontmatter
    frontmatter = {
        "title": note["title"],
        "aliases": [],
        "date_created": target_date,
        "date_modified": target_date,
        "type": note_type,
        "domain": note.get("domain", "personal"),
        "tags": note.get("tags", []),
        "source_entries": [f"[[{target_date}]]"],
        "related": [f"[[{r}]]" for r in note.get("related", [])],
        "status": "active",
    }

    content = "---\n"
    content += yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True, sort_keys=False)
    content += "---\n\n"
    content += note.get("body", "")
    content += "\n"

    filepath.write_text(content, encoding="utf-8")
    print(f"  [new]  {filepath.name}")

    # Register in existing notes for subsequent notes in this batch
    existing_notes[slug] = filepath


def update_journal_entry(journal_path: Path, notes: list[dict]):
    """Add a concepts_extracted section to the journal entry."""
    journal_text = journal_path.read_text(encoding="utf-8")

    # Don't add if already present
    if "## Concepts Extracted" in journal_text:
        return

    links = "\n".join(f"- [[{note['title']}]]" for note in notes)
    section = f"\n\n---\n## Concepts Extracted\n{links}\n"

    journal_path.write_text(journal_text + section, encoding="utf-8")
    print(f"\nUpdated journal entry with {len(notes)} concept links.")


def process_journal(target_date: date):
    journal_path = get_journal_path(target_date)

    if not journal_path.exists():
        print(f"No journal entry found for {target_date.isoformat()}")
        print(f"Expected: {journal_path}")
        print(f"\nCreate your journal entry at: {journal_path}")
        sys.exit(1)

    journal_text = journal_path.read_text(encoding="utf-8")
    if not journal_text.strip():
        print(f"Journal entry for {target_date.isoformat()} is empty.")
        sys.exit(1)

    config = load_config()
    existing_notes = get_existing_notes()
    existing_titles = get_existing_titles(existing_notes)
    date_str = target_date.isoformat()

    print(f"Processing journal: {journal_path.name}")
    print(f"Existing notes in system: {len(existing_notes)}")
    print()

    # Extract notes via Claude
    print("Extracting concepts...")
    notes = extract_notes(journal_text, config, date_str, existing_titles)
    print(f"Found {len(notes)} items.\n")

    # Write each note
    print("Writing notes:")
    for note in notes:
        write_note(note, date_str, existing_notes)

    # Update journal entry with backlinks
    update_journal_entry(journal_path, notes)

    print(f"\nDone. {len(notes)} items processed from {date_str}.")


def main():
    parser = argparse.ArgumentParser(description="Alluvium — Process a daily journal entry into atomic notes.")
    parser.add_argument(
        "date",
        nargs="?",
        default=date.today().isoformat(),
        help="Date to process (YYYY-MM-DD). Defaults to today.",
    )
    args = parser.parse_args()

    try:
        target_date = date.fromisoformat(args.date)
    except ValueError:
        print(f"Invalid date format: {args.date}. Use YYYY-MM-DD.")
        sys.exit(1)

    process_journal(target_date)


if __name__ == "__main__":
    main()
