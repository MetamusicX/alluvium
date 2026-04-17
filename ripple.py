#!/usr/bin/env python3
"""
Alluvium — Ripple Engine
After new notes are extracted, this engine propagates their effects across the
entire vault. New knowledge compounds: each new deposit reshapes the existing
terrain by adding cross-references, surfacing unexpected connections, and
enriching context in related notes.

The ripple engine answers: "Given what just arrived, what else in the vault
should know about it?"
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import anthropic
import yaml

# --- Paths ---
BASE_DIR = Path(__file__).parent
INBOX_DIR = BASE_DIR / "0 Inbox"
PROJECTS_DIR = BASE_DIR / "1 Projects"
AREAS_DIR = BASE_DIR / "2 Areas"
RESOURCES_DIR = BASE_DIR / "3 Resources"
ARCHIVE_DIR = BASE_DIR / "4 Archive"
PEOPLE_DIR = BASE_DIR / "People"
AUTHORS_DIR = BASE_DIR / "Authors"
MANIFEST_PATH = BASE_DIR / ".last_run.json"
CONFIG_PATH = BASE_DIR / "config.yaml"

ALL_DIRS = [INBOX_DIR, PROJECTS_DIR, AREAS_DIR, RESOURCES_DIR, ARCHIVE_DIR, PEOPLE_DIR, AUTHORS_DIR]

MAX_RIPPLES_PER_RUN = 15  # Safety limit


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def load_manifest() -> list[str]:
    """Load the list of note titles created in the last processing run."""
    if not MANIFEST_PATH.exists():
        return []
    with open(MANIFEST_PATH) as f:
        data = json.load(f)
    return data.get("new_notes", [])


def save_manifest(new_notes: list[str]):
    """Save a manifest of newly created notes."""
    with open(MANIFEST_PATH, "w") as f:
        json.dump({"new_notes": new_notes}, f, indent=2)


def read_note(filepath: Path) -> dict | None:
    """Read a note's frontmatter and body."""
    text = filepath.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    try:
        parts = text.split("---", 2)
        fm = yaml.safe_load(parts[1])
        body = parts[2].strip() if len(parts) > 2 else ""
        fm["_path"] = filepath
        fm["_body"] = body
        fm["_full_text"] = text
        return fm
    except Exception:
        return None


def collect_all_notes() -> list[dict]:
    """Gather all notes across the entire vault."""
    notes = []
    for folder in ALL_DIRS:
        if not folder.exists():
            continue
        for f in folder.rglob("*.md"):
            if f.name.startswith("_"):  # Skip MOCs
                continue
            meta = read_note(f)
            if meta:
                notes.append(meta)
    return notes


def build_ripple_prompt(new_notes: list[dict], existing_notes: list[dict], config: dict) -> str:
    """Build the prompt that asks Claude to identify meaningful connections."""

    domains_desc = "\n".join(
        f"- **{key}** ({d['name']}): {d['description']}"
        for key, d in config["domains"].items()
    )

    # New notes: full content
    new_section = "\n\n".join(
        f"### \"{n.get('title', '?')}\"\n"
        f"Type: {n.get('type', '?')} | Domain: {n.get('domain', '?')} | Tags: {', '.join(n.get('tags', []))}\n"
        f"Related: {', '.join(str(r) for r in n.get('related', []))}\n\n"
        f"{n.get('_body', '')}"
        for n in new_notes
    )

    # Existing notes: summaries with enough context
    existing_section = "\n".join(
        f"- \"{n.get('title', '?')}\" | {n.get('type', '?')} | {n.get('domain', '?')} | "
        f"tags: {', '.join(n.get('tags', []))} | "
        f"related: {', '.join(str(r) for r in n.get('related', []))} | "
        f"summary: {n.get('_body', '')[:250]}"
        for n in existing_notes
    )

    return f"""You are a knowledge compounding engine for a personal knowledge system. Your job is to make knowledge accumulate and interconnect, like a living brain.

## Context — Life Domains
{domains_desc}

## NEW notes (just created from today's journal)
{new_section}

## ALL existing notes in the vault
{existing_section}

## Your task
Examine each new note and determine which existing notes should be updated because of this new information. Think about:

1. **Direct connections** — the new note mentions or relates to an existing topic, person, or project
2. **Thematic resonance** — the new note shares concepts, methods, or patterns with existing notes (even across different domains)
3. **Cross-domain insights** — a concept from one domain illuminates something in another (e.g., a training concept that parallels a compositional technique)
4. **Evolving understanding** — the new note deepens, contradicts, or extends something in an existing note

## Rules
1. Only propose connections that are genuinely meaningful. Not every note connects to every other note.
2. Do NOT propose connections between notes that are already linked (check the "related" field).
3. Do NOT update notes that were just created (they are in the "new notes" section).
4. Maximum {MAX_RIPPLES_PER_RUN} updates per run.
5. Each update should add real value — a brief insight, a cross-reference, a new perspective. Not just "see also [[X]]".
6. Write in the voice of the vault owner — first person, reflective, concise.
7. Include [[wikilinks]] to connect the notes in Obsidian's graph.

## Output Format
Return a JSON array of update objects:
- "target_title": exact title of the existing note to update
- "connection_type": "cross-reference" | "enrichment" | "insight" | "evolution"
- "reason": one sentence explaining why this connection matters (for the log, not added to the note)
- "append_text": text to append to the existing note (use [[wikilinks]], keep it to 1-3 sentences)
- "add_related": list of new note titles to add to the target's related field
- "add_tags": list of any new tags warranted by this connection (optional, usually empty)

If no meaningful connections exist, return an empty array: []

Return ONLY the JSON array. No markdown code fences, no commentary."""


def apply_ripples(ripples: list[dict], all_notes: list[dict]):
    """Apply the ripple updates to existing notes."""
    if not ripples:
        print("No ripples to apply.")
        return

    for ripple in ripples[:MAX_RIPPLES_PER_RUN]:
        target_title = ripple.get("target_title", "")
        connection_type = ripple.get("connection_type", "cross-reference")
        reason = ripple.get("reason", "")
        append_text = ripple.get("append_text", "")
        add_related = ripple.get("add_related", [])
        add_tags = ripple.get("add_tags", [])

        # Find the target note
        target = None
        for n in all_notes:
            if n.get("title", "") == target_title:
                target = n
                break

        if not target or not target["_path"].exists():
            print(f"  [miss] \"{target_title}\" — not found")
            continue

        filepath = target["_path"]
        text = filepath.read_text(encoding="utf-8")

        # Update frontmatter
        modified = False
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                try:
                    fm = yaml.safe_load(parts[1])

                    # Add new related links
                    existing_related = [str(r) for r in fm.get("related", [])]
                    for rel in add_related:
                        link = f"[[{rel}]]"
                        if link not in existing_related:
                            existing_related.append(link)
                            modified = True
                    fm["related"] = existing_related

                    # Add new tags
                    existing_tags = set(fm.get("tags", []))
                    new_tags = set(add_tags)
                    if new_tags - existing_tags:
                        fm["tags"] = sorted(existing_tags | new_tags)
                        modified = True

                    if modified or append_text:
                        fm["date_modified"] = __import__("datetime").date.today().isoformat()

                    # Rebuild the file
                    body = parts[2]
                    if append_text:
                        body = body.rstrip() + f"\n\n**Connection ({connection_type}):** {append_text}\n"

                    updated = "---\n" + yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False) + "---" + body
                    filepath.write_text(updated, encoding="utf-8")

                    icon = {"cross-reference": "link", "enrichment": "plus", "insight": "spark", "evolution": "arrow"}
                    print(f"  [{connection_type}] \"{target_title}\" — {reason}")

                except Exception as e:
                    print(f"  [error] \"{target_title}\" — {e}")
                    continue


def run_ripple(new_note_titles: list[str] | None = None):
    """Main ripple pipeline."""
    print("=== Alluvium — Ripple Engine ===\n")

    config = load_config()
    all_notes = collect_all_notes()

    # Determine which notes are new
    if new_note_titles is None:
        new_note_titles = load_manifest()

    if not new_note_titles:
        print("No new notes to ripple from. Nothing to do.\n")
        return

    # Split into new and existing
    new_notes = [n for n in all_notes if n.get("title", "") in new_note_titles]
    existing_notes = [n for n in all_notes if n.get("title", "") not in new_note_titles]

    print(f"New notes: {len(new_notes)}")
    print(f"Existing notes: {len(existing_notes)}")

    if not new_notes:
        print("Could not find the new notes in the vault. Skipping.\n")
        return

    if not existing_notes:
        print("No existing notes to ripple into. The vault is still young.\n")
        return

    print("\nAnalyzing connections...\n")

    # Ask Claude to identify ripples
    client = anthropic.Anthropic()
    prompt = build_ripple_prompt(new_notes, existing_notes, config)

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = message.content[0].text.strip()
    if response_text.startswith("```"):
        response_text = re.sub(r"^```(?:json)?\n?", "", response_text)
        response_text = re.sub(r"\n?```$", "", response_text)

    try:
        ripples = json.loads(response_text)
    except json.JSONDecodeError:
        print("Failed to parse ripple response. Skipping.\n")
        return

    if not ripples:
        print("No meaningful connections found this run. The terrain is stable.\n")
        return

    print(f"Found {len(ripples)} ripple(s). Applying:\n")
    apply_ripples(ripples, all_notes)

    print(f"\n=== Ripple complete — {len(ripples)} connection(s) made ===")


if __name__ == "__main__":
    run_ripple()
