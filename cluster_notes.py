#!/usr/bin/env python3
"""
Alluvium — Cluster Engine
Scans all notes and people, identifies emergent clusters, organizes them into
subfolders, and generates Maps of Content.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import anthropic
import yaml

# --- Paths ---
BASE_DIR = Path(__file__).parent
NOTES_DIR = BASE_DIR / "Notes"
PEOPLE_DIR = BASE_DIR / "People"
AUTHORS_DIR = BASE_DIR / "Authors"
PROJECTS_DIR = BASE_DIR / "Projects"
CONFIG_PATH = BASE_DIR / "config.yaml"

CLUSTER_THRESHOLD = 3  # Minimum notes to form a cluster


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def read_note_metadata(filepath: Path) -> dict | None:
    """Read YAML frontmatter from a note file."""
    text = filepath.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    try:
        fm_text = text.split("---", 2)[1]
        fm = yaml.safe_load(fm_text)
        fm["_path"] = filepath
        fm["_slug"] = filepath.stem
        fm["_body"] = text.split("---", 2)[2] if len(text.split("---", 2)) > 2 else ""
        return fm
    except Exception:
        return None


def collect_notes(folder: Path) -> list[dict]:
    """Gather metadata from all notes in a folder (including subfolders)."""
    notes = []
    if not folder.exists():
        return notes
    for f in folder.rglob("*.md"):
        if f.name.startswith("_"):  # Skip Maps of Content
            continue
        meta = read_note_metadata(f)
        if meta:
            notes.append(meta)
    return notes


def collect_all_notes() -> list[dict]:
    """Gather metadata from all notes across all folders."""
    notes = []
    for folder in [NOTES_DIR, PEOPLE_DIR, AUTHORS_DIR, PROJECTS_DIR]:
        notes.extend(collect_notes(folder))
    return notes


# ---------------------------------------------------------------------------
# NOTES CLUSTERING
# ---------------------------------------------------------------------------

def build_notes_clustering_prompt(notes: list[dict], config: dict) -> str:
    domains_desc = "\n".join(
        f"- **{key}** ({d['name']}): {d['description']}"
        for key, d in config["domains"].items()
    )

    notes_summary = "\n".join(
        f"- \"{n.get('title', n['_slug'])}\" | type: {n.get('type', '?')} | domain: {n.get('domain', '?')} | tags: {', '.join(n.get('tags', []))}"
        for n in notes
    )

    return f"""You are an organizational analyst for a personal knowledge system. Your job is to look at all existing notes and identify natural clusters — groups of notes that belong together.

## Life Domains
{domains_desc}

## All notes in the system
{notes_summary}

## Rules
1. A cluster must have at least {CLUSTER_THRESHOLD} notes to be worth creating.
2. Clusters should be emergent — based on what the notes actually share, not forced categories.
3. A cluster can cut across domains if the connection is strong.
4. Not every note needs to be in a cluster. Unclustered notes stay at the root.
5. Use clear, descriptive cluster names (e.g., "Training Log", "ERC Evaluations", "Nunes Monograph").
6. A note can only belong to one cluster.
7. All athletic/sport/training notes (swim, run, bike, weightlifting, flexibility, etc.) should cluster together under one training cluster.
8. Only propose clusters where the grouping is genuinely useful.
9. If notes are already in a cluster subfolder, you can still include them (they won't be moved again).

## Output Format
Return a JSON array of cluster objects:
- "name": human-readable cluster name (will become the folder name)
- "description": one-sentence description of what unites these notes
- "notes": list of exact note titles that belong in this cluster

If no clusters are warranted yet, return an empty array: []

Return ONLY the JSON array. No markdown code fences, no commentary."""


# ---------------------------------------------------------------------------
# PEOPLE CLUSTERING
# ---------------------------------------------------------------------------

def build_people_clustering_prompt(people: list[dict], config: dict) -> str:
    domains_desc = "\n".join(
        f"- **{key}** ({d['name']}): {d['description']}"
        for key, d in config["domains"].items()
    )

    people_summary = "\n".join(
        f"- \"{p.get('title', p['_slug'])}\" | domain: {p.get('domain', '?')} | tags: {', '.join(p.get('tags', []))} | context: {p.get('_body', '')[:150].strip()}"
        for p in people
    )

    return f"""You are an organizational analyst for a personal knowledge system. Your job is to categorize people into natural groups.

## Life Domains
{domains_desc}

## People categories to consider
- **Friends** — personal friends, social connections
- **Family** — family members
- **Colleagues** — professional colleagues (subcategorize by workplace if clear: Orpheus/Ghent, Switzerland/SNF, ERC, etc.)
- **Online Contacts** — people known through online interactions, social media, tech community
- **Authors** — creators in any field: writers, philosophers, composers, painters, filmmakers, thinkers. People known primarily through their work, not personal interaction. These should be moved to a separate Authors/ folder.

## All people in the system
{people_summary}

## Rules
1. A category must have at least {CLUSTER_THRESHOLD} people to form a cluster within People/.
2. Authors (writers, philosophers, composers, painters, thinkers known through their work) should be flagged with "move_to_authors": true — they will be moved to a separate Authors/ folder.
3. A person can only belong to one category.
4. Not every person needs to be categorized yet. Uncategorized people stay at the People/ root.
5. Use your best judgement based on the context available.

## Output Format
Return a JSON array of objects:
- "name": category name (e.g., "Colleagues — Orpheus", "Online Contacts", "Authors")
- "description": one-sentence description
- "people": list of exact person titles
- "move_to_authors": true/false (if true, these people move to Authors/ instead of a People/ subfolder)

If no categories are warranted yet, return an empty array: []

Return ONLY the JSON array. No markdown code fences, no commentary."""


def call_claude(prompt: str) -> list[dict]:
    """Send a prompt to Claude and parse the JSON response."""
    client = anthropic.Anthropic()

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = message.content[0].text.strip()

    if response_text.startswith("```"):
        response_text = re.sub(r"^```(?:json)?\n?", "", response_text)
        response_text = re.sub(r"\n?```$", "", response_text)

    return json.loads(response_text)


def find_note_path(title: str, notes: list[dict]) -> Path | None:
    """Find a note's file path by its title."""
    for n in notes:
        if n.get("title", "") == title:
            return n["_path"]
    return None


def move_note_to_folder(note_path: Path, target_dir: Path) -> Path:
    """Move a note file into a target folder."""
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / note_path.name
    if dest == note_path:
        return dest
    shutil.move(str(note_path), str(dest))
    return dest


def generate_moc(cluster_name: str, description: str, note_titles: list[str], cluster_dir: Path):
    """Generate a Map of Content file for a cluster."""
    moc_path = cluster_dir / f"_{cluster_name}.md"

    frontmatter = {
        "title": cluster_name,
        "type": "map-of-content",
        "tags": ["moc"],
        "status": "active",
    }

    links = "\n".join(f"- [[{title}]]" for title in sorted(note_titles))

    content = "---\n"
    content += yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True, sort_keys=False)
    content += "---\n\n"
    content += f"{description}\n\n"
    content += f"## Notes\n\n{links}\n"

    moc_path.write_text(content, encoding="utf-8")
    return moc_path


def apply_notes_clusters(clusters: list[dict], all_notes: list[dict]):
    """Create cluster folders for notes, move them, generate MOCs."""
    if not clusters:
        return

    for cluster in clusters:
        name = cluster["name"]
        description = cluster.get("description", "")
        note_titles = cluster.get("notes", [])

        if len(note_titles) < CLUSTER_THRESHOLD:
            print(f"  [skip] \"{name}\" — only {len(note_titles)} notes (need {CLUSTER_THRESHOLD})")
            continue

        cluster_dir = NOTES_DIR / name
        cluster_dir.mkdir(exist_ok=True)

        moved = []
        for title in note_titles:
            note_path = find_note_path(title, all_notes)
            if note_path and note_path.exists():
                # Don't move if already in this cluster
                if note_path.parent == cluster_dir:
                    moved.append(title)
                    continue
                move_note_to_folder(note_path, cluster_dir)
                moved.append(title)
                print(f"  [move] \"{title}\" → Notes/{name}/")
            else:
                print(f"  [miss] \"{title}\" — not found")

        if moved:
            generate_moc(name, description, moved, cluster_dir)
            print(f"  [moc]  _{name}.md")

        print(f"  Cluster \"{name}\": {len(moved)} notes.\n")


def apply_people_clusters(clusters: list[dict], all_people: list[dict]):
    """Organize people into subcategories and move authors."""
    if not clusters:
        return

    for cluster in clusters:
        name = cluster["name"]
        description = cluster.get("description", "")
        people_titles = cluster.get("people", [])
        move_to_authors = cluster.get("move_to_authors", False)

        if len(people_titles) < CLUSTER_THRESHOLD:
            print(f"  [skip] \"{name}\" — only {len(people_titles)} people (need {CLUSTER_THRESHOLD})")
            continue

        if move_to_authors:
            target_dir = AUTHORS_DIR
        else:
            target_dir = PEOPLE_DIR / name

        target_dir.mkdir(parents=True, exist_ok=True)

        moved = []
        for title in people_titles:
            person_path = find_note_path(title, all_people)
            if person_path and person_path.exists():
                if person_path.parent == target_dir:
                    moved.append(title)
                    continue
                move_note_to_folder(person_path, target_dir)
                moved.append(title)
                folder_label = "Authors/" if move_to_authors else f"People/{name}/"
                print(f"  [move] \"{title}\" → {folder_label}")
            else:
                print(f"  [miss] \"{title}\" — not found")

        if moved:
            generate_moc(name, description, moved, target_dir)
            print(f"  [moc]  _{name}.md")

        label = "Authors" if move_to_authors else name
        print(f"  Category \"{label}\": {len(moved)} people.\n")


def run_clustering():
    """Main clustering pipeline."""
    print("=== Alluvium — Clustering ===\n")

    config = load_config()

    # --- Notes clustering ---
    all_notes = collect_notes(NOTES_DIR)
    print(f"Notes in system: {len(all_notes)}")

    if len(all_notes) >= CLUSTER_THRESHOLD:
        print("Analyzing notes for emergent clusters...")
        notes_clusters = call_claude(build_notes_clustering_prompt(all_notes, config))
        if notes_clusters:
            print(f"Found {len(notes_clusters)} note cluster(s).\n")
            apply_notes_clusters(notes_clusters, all_notes)
        else:
            print("No new note clusters emerged.\n")
    else:
        print("Too few notes to cluster.\n")

    # --- People clustering ---
    all_people = collect_notes(PEOPLE_DIR)
    print(f"People in system: {len(all_people)}")

    if len(all_people) >= CLUSTER_THRESHOLD:
        print("Analyzing people for emergent categories...")
        people_clusters = call_claude(build_people_clustering_prompt(all_people, config))
        if people_clusters:
            print(f"Found {len(people_clusters)} people category(ies).\n")
            apply_people_clusters(people_clusters, all_people)
        else:
            print("No people categories emerged yet.\n")
    else:
        print("Too few people to categorize.\n")

    print("=== Clustering complete ===")


if __name__ == "__main__":
    run_clustering()
