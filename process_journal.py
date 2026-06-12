#!/usr/bin/env python3
"""
Alluvium — Flow becomes knowledge
Reads a daily journal entry and extracts atomic notes with Obsidian-compatible YAML frontmatter.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

from llm import call_llm_json, is_para_enabled

# --- Paths ---
BASE_DIR = Path(__file__).parent
JOURNAL_DIR = BASE_DIR / "00 Journal"
NOTES_DIR = BASE_DIR / "01 Inbox"
PEOPLE_DIR = BASE_DIR / "People"
AUTHORS_DIR = BASE_DIR / "Authors"
PROJECTS_DIR = BASE_DIR / "1 Projects"
AREAS_DIR = BASE_DIR / "2 Areas"
RESOURCES_DIR = BASE_DIR / "3 Resources"
ARCHIVE_DIR = BASE_DIR / "4 Archive"
CONFIG_PATH = BASE_DIR / "config.yaml"

ALL_FOLDERS = [NOTES_DIR, PEOPLE_DIR, AUTHORS_DIR, PROJECTS_DIR, AREAS_DIR, RESOURCES_DIR, ARCHIVE_DIR]

PARA_FOLDERS = {
    "project": PROJECTS_DIR,
    "area": AREAS_DIR,
    "resource": RESOURCES_DIR,
    "archive": ARCHIVE_DIR,
}


SUMMARIES_DIR = BASE_DIR / "Day Summaries"


def archive_previous_month(target_date: date):
    """On the 1st of a month, move the previous month's files into YYYY-MM subfolders."""
    if target_date.day != 1:
        return

    # Calculate previous month
    if target_date.month == 1:
        prev_year, prev_month = target_date.year - 1, 12
    else:
        prev_year, prev_month = target_date.year, target_date.month - 1

    prefix = f"{prev_year}-{prev_month:02d}"

    for folder in [JOURNAL_DIR, SUMMARIES_DIR]:
        if not folder.exists():
            continue
        files = sorted(folder.glob(f"{prefix}-*.md"))
        if not files:
            continue
        archive = folder / prefix
        archive.mkdir(exist_ok=True)
        for f in files:
            dest = archive / f.name
            f.rename(dest)
            print(f"  [archive] {f.name} → {prefix}/")
        print(f"Archived {len(files)} files into {folder.name}/{prefix}/")


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def get_journal_path(target_date: date) -> Path:
    """Find journal file — check root first, then month subfolder."""
    root = JOURNAL_DIR / f"{target_date.isoformat()}.md"
    if root.exists():
        return root
    monthly = JOURNAL_DIR / target_date.strftime("%Y-%m") / f"{target_date.isoformat()}.md"
    if monthly.exists():
        return monthly
    return root  # default to root (for error messages)


def get_existing_notes() -> dict[str, Path]:
    """Return a map of slug -> filepath for all existing notes (including subfolders)."""
    notes = {}
    for folder in ALL_FOLDERS:
        if folder.exists():
            for f in folder.rglob("*.md"):
                if not f.name.startswith("_"):  # Skip Maps of Content
                    notes[f.stem] = f
    return notes


MAX_RECENT_TITLES = 200  # non-people notes shown to the extraction prompt


def _read_title(slug: str, path: Path) -> str:
    """Read a note's title from frontmatter, falling back to the slug."""
    try:
        text = path.read_text(encoding="utf-8")
        if text.startswith("---"):
            fm = yaml.safe_load(text.split("---", 2)[1])
            if fm and "title" in fm:
                return fm["title"]
    except Exception:
        pass
    return slug.replace("-", " ").title()


def get_existing_titles(existing_notes: dict[str, Path]) -> list[str]:
    """Titles shown to the extraction prompt for linking/dedup.

    All People/Authors are always included (so the model never re-creates an
    existing person); other notes are included by recency, capped, so the list
    stays bounded as the vault grows.
    """
    people, others = [], []
    for slug, path in existing_notes.items():
        if PEOPLE_DIR in path.parents or AUTHORS_DIR in path.parents:
            people.append((slug, path))
        else:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0
            others.append((mtime, slug, path))
    others.sort(key=lambda t: t[0], reverse=True)
    selected = people + [(slug, path) for _, slug, path in others[:MAX_RECENT_TITLES]]
    return [_read_title(slug, path) for slug, path in selected]


FEW_SHOT_EXAMPLE = """
## Example

**Journal entry (2026-03-10):**
Ran 12km this morning, easy pace around 5:30/km. Legs felt heavy from yesterday's bike.
Later had a call with Henrik about the panel review — we agreed to split the applications by discipline. Need to send him my half by Friday.
Reading Deleuze's "Difference and Repetition" again. The concept of the virtual is exactly what I need for the chapter on layered temporality.

**Extracted notes:**
[
  {
    "title": "12km Easy Run",
    "type": "practice-log",
    "domain": "sport",
    "tags": ["running", "easy-pace", "fatigue"],
    "related": ["Training Log"],
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
    "body": "Re-reading Deleuze's *Difference and Repetition*. The concept of the virtual — a structure that is real but not actual — maps onto layered temporal strata that only become audible through their interactions. Exactly what the chapter on layered temporality needs. Extracted from [[2026-03-10]]."
  }
]
"""


def build_extraction_prompt(journal_text: str, config: dict, target_date: str, existing_titles: list[str], para_enabled: bool = True) -> str:
    domains_desc = "\n".join(
        f"- **{d['name']}**: {d['description']} (keywords: {', '.join(d.get('keywords', []))})"
        for d in config["domains"].values()
    )
    note_types = ", ".join(config["note_types"])
    existing = ", ".join(existing_titles) if existing_titles else "(none yet)"
    domain_keys = ", ".join(config["domains"].keys()) + ", personal"

    para_rules = ""
    para_output_field = ""
    if para_enabled:
        para_rules = """
## PARA Classification (Tiago Forte)
Classify each note into one of the PARA categories:
- **project** — an active effort with a clear outcome or deadline
  (e.g. a grant application, preparing for a race, writing a chapter)
- **area** — an ongoing responsibility with no end date
  (e.g. health, career, a professional role)
- **resource** — a topic of interest, reference material, ideas worth keeping
  (e.g. a book insight, a technique, a concept)
- **archive** — completed items, things no longer active

Most freshly extracted notes will be "resource" (ideas, readings, reflections)
or "project" (tasks with deadlines, active work). Use "area" for ongoing
commitments. Use "archive" only when the note explicitly describes something
finished or closed. Person-type notes do not need a PARA category.
"""
        para_output_field = '\n- "para": one of "project", "area", "resource", "archive" — the PARA category (omit for person-type notes)'

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
8. For practice logs (piano, training), include specific details: duration, what was practiced/trained, how it felt.
9. Every note body should end with: Extracted from [[{target_date}]].
10. If a person or concept already exists in the system, reference them with [[wikilinks]] but do NOT create a new note for them. Instead, include an "append_to" field with their exact title.
{para_rules}{FEW_SHOT_EXAMPLE}

## Output Format
Return a JSON array of objects, each with:
- "title": short descriptive title (will become the filename)
- "type": one of the note types
- "domain": primary domain key ({domain_keys})
- "tags": list of tags (lowercase, no #)
- "related": list of titles this note connects to (used as [[wikilinks]])
- "body": the note content in markdown, using [[wikilinks]] for connections
- "append_to": (optional) if this adds context to an existing note, put the existing note's title here{para_output_field}

Journal date: {target_date}

## Journal Entry
{journal_text}

Return ONLY the JSON array. No markdown code fences, no commentary."""


def extract_notes(journal_text: str, config: dict, target_date: str, existing_titles: list[str], para_enabled: bool = True) -> list[dict]:
    prompt = build_extraction_prompt(journal_text, config, target_date, existing_titles, para_enabled)
    return call_llm_json(prompt, max_tokens=8192)


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

    # Update frontmatter (date_modified always, tags when new ones appear)
    # so the daily summary picks this note up as touched today.
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
                print(f"  [append] {filepath.name}")
                return
            except Exception:
                pass

    # Fallback: just append (no parseable frontmatter)
    filepath.write_text(existing_content + new_entry + "\n", encoding="utf-8")
    print(f"  [append] {filepath.name}")


def write_note(note: dict, target_date: str, existing_notes: dict[str, Path], para_enabled: bool = True):
    slug = slugify(note["title"])
    note_type = note.get("type", "note")

    # Check if this should append to an existing note
    append_target = note.get("append_to")
    if append_target:
        existing = find_existing_note(append_target, existing_notes)
        if existing and existing.exists():
            append_to_note(existing, note, target_date)
            return

    # Choose output folder
    if note_type == "person":
        folder = PEOPLE_DIR
    elif para_enabled and note.get("para") in PARA_FOLDERS:
        folder = PARA_FOLDERS[note["para"]]
    else:
        folder = NOTES_DIR

    folder.mkdir(parents=True, exist_ok=True)
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
    }
    if note.get("para"):
        frontmatter["para"] = note["para"]
    frontmatter.update({
        "tags": note.get("tags", []),
        "source_entries": [f"[[{target_date}]]"],
        "related": [f"[[{r}]]" for r in note.get("related", [])],
        "status": "active",
    })

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

    # Notes that were appended to an existing note should link to that note's
    # title — the new title never became a file.
    link_titles = []
    for note in notes:
        title = note.get("append_to") or note["title"]
        if title not in link_titles:
            link_titles.append(title)
    links = "\n".join(f"- [[{title}]]" for title in link_titles)
    section = f"\n\n---\n## Concepts Extracted\n{links}\n"

    journal_path.write_text(journal_text + section, encoding="utf-8")
    print(f"\nUpdated journal entry with {len(notes)} concept links.")


def _already_extracted(journal_path: Path) -> bool:
    """Check if this journal entry has already been processed."""
    if not journal_path.exists():
        return False
    return "## Concepts Extracted" in journal_path.read_text(encoding="utf-8")


def process_journal(target_date: date, para_enabled: bool = True):
    # Archive previous month's files on the 1st
    archive_previous_month(target_date)

    journal_path = get_journal_path(target_date)

    if not journal_path.exists():
        print(f"No journal entry found for {target_date.isoformat()}")
        print(f"Expected: {journal_path}")
        print(f"\nCreate your journal entry at: {journal_path}")
        sys.exit(1)

    # Deduplication: skip extraction if already done, but still run summary.
    # The summary stage has its own dedup (file-existence check), so this is safe.
    already_extracted = _already_extracted(journal_path)
    if already_extracted:
        print(f"Journal {journal_path.name} already extracted — skipping extraction, still running summary.")
        print("(Delete the '## Concepts Extracted' section to force re-extraction.)")
        from summarize import run_summary
        run_summary(target_date)
        return

    journal_text = journal_path.read_text(encoding="utf-8")
    if not journal_text.strip():
        print(f"Journal entry for {target_date.isoformat()} is empty.")
        sys.exit(1)

    config = load_config()
    existing_notes = get_existing_notes()
    existing_titles = get_existing_titles(existing_notes)
    date_str = target_date.isoformat()

    from llm import get_provider_key, get_model, PROVIDERS
    provider_key = get_provider_key()
    model = get_model(provider_key)
    provider_name = PROVIDERS[provider_key]["name"]

    print(f"Processing journal: {journal_path.name}")
    print(f"Provider: {provider_name} ({model})")
    print(f"PARA organization: {'enabled' if para_enabled else 'disabled'}")
    print(f"Existing notes in system: {len(existing_notes)}")
    print()

    # Extract notes via LLM
    print("Extracting concepts...")
    notes = extract_notes(journal_text, config, date_str, existing_titles, para_enabled)
    print(f"Found {len(notes)} items.\n")

    if not notes:
        # Don't mark the journal as extracted — leave it eligible for a retry.
        print("Extraction returned no notes — journal left unmarked for re-extraction.")
        from summarize import run_summary
        print()
        run_summary(target_date)
        return

    # Write each note, track new ones
    print("Writing notes:")
    new_note_titles = []
    for note in notes:
        slug = slugify(note["title"])
        is_new = slug not in existing_notes
        write_note(note, date_str, existing_notes, para_enabled)
        if is_new and not note.get("append_to"):
            new_note_titles.append(note["title"])

    # Update journal entry with backlinks
    update_journal_entry(journal_path, notes)

    print(f"\nDone. {len(notes)} items processed from {date_str}.")

    # Save manifest for ripple engine
    from ripple import save_manifest, run_ripple
    save_manifest(new_note_titles, date_str)

    # Run clustering (only when PARA is enabled) — non-fatal
    if para_enabled:
        try:
            from cluster_notes import run_clustering
            print()
            run_clustering()
        except Exception as e:
            print(f"\n⚠ Clustering failed (non-fatal): {e}")

    # Run ripple engine — compound knowledge — non-fatal
    if new_note_titles:
        try:
            print()
            run_ripple(new_note_titles, date_str)
        except Exception as e:
            print(f"\n⚠ Ripple engine failed (non-fatal): {e}")

    # Generate daily summary for Day One — this MUST run
    from summarize import run_summary
    print()
    run_summary(target_date)


def main():
    from llm import PROVIDERS

    parser = argparse.ArgumentParser(description="Process a daily journal entry into atomic notes.")
    parser.add_argument(
        "date",
        nargs="?",
        default=(date.today() - timedelta(days=1)).isoformat(),
        help="Date to process (YYYY-MM-DD). Defaults to yesterday (script runs in the morning).",
    )
    parser.add_argument(
        "--provider",
        choices=list(PROVIDERS.keys()),
        help="AI provider (overrides config.yaml and ALLUVIUM_PROVIDER).",
    )
    parser.add_argument(
        "--model",
        help="Model ID (overrides config.yaml and ALLUVIUM_MODEL).",
    )
    para_group = parser.add_mutually_exclusive_group()
    para_group.add_argument(
        "--para", dest="para", action="store_true", default=None,
        help="Enable PARA organization (overrides config).",
    )
    para_group.add_argument(
        "--no-para", dest="para", action="store_false",
        help="Disable PARA organization (flat Inbox).",
    )
    args = parser.parse_args()

    try:
        target_date = date.fromisoformat(args.date)
    except ValueError:
        print(f"Invalid date format: {args.date}. Use YYYY-MM-DD.")
        sys.exit(1)

    # Apply CLI overrides via environment (picked up by llm.py)
    import os
    if args.provider:
        os.environ["ALLUVIUM_PROVIDER"] = args.provider
    if args.model:
        os.environ["ALLUVIUM_MODEL"] = args.model

    # Determine PARA: CLI flag > env > config
    if args.para is not None:
        para_enabled = args.para
    else:
        para_enabled = is_para_enabled()

    process_journal(target_date, para_enabled)


if __name__ == "__main__":
    main()
