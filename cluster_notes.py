#!/usr/bin/env python3
"""
Alluvium — Cluster Engine
Scans all notes, identifies emergent clusters, organizes them into subfolders,
and generates Maps of Content. Run after process_journal.py or on its own.
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
        return fm
    except Exception:
        return None


def collect_all_notes() -> list[dict]:
    """Gather metadata from all notes (including those already in subfolders)."""
    notes = []
    for folder in [NOTES_DIR, PEOPLE_DIR, PROJECTS_DIR]:
        if not folder.exists():
            continue
        for f in folder.rglob("*.md"):
            # Skip Maps of Content
            if f.name.startswith("_"):
                continue
            meta = read_note_metadata(f)
            if meta:
                notes.append(meta)
    return notes


def build_clustering_prompt(notes: list[dict], config: dict) -> str:
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
3. A cluster can cut across domains if the connection is strong (e.g., "Nunes Monograph" might span writing + orpheus).
4. Not every note needs to be in a cluster. Unclustered notes stay at the root.
5. Use clear, descriptive cluster names that a human would recognise (e.g., "Training Log", "ERC Evaluations", "Nunes Monograph").
6. A note can only belong to one cluster.
7. People notes (type: person) should NOT be clustered — they stay in People/.
8. Only propose clusters where the grouping is genuinely useful, not obvious single-domain dumps.

## Output Format
Return a JSON array of cluster objects:
- "name": human-readable cluster name (will become the folder name)
- "description": one-sentence description of what unites these notes
- "notes": list of exact note titles that belong in this cluster

If no clusters are warranted yet, return an empty array: []

Return ONLY the JSON array. No markdown code fences, no commentary."""


def identify_clusters(notes: list[dict], config: dict) -> list[dict]:
    """Ask Claude to identify natural clusters among the notes."""
    # Filter to only Notes/ (not People/)
    clusterable = [n for n in notes if n["_path"].parent == NOTES_DIR or str(n["_path"]).startswith(str(NOTES_DIR))]

    if len(clusterable) < CLUSTER_THRESHOLD:
        print(f"Only {len(clusterable)} notes in system — too few to cluster (threshold: {CLUSTER_THRESHOLD}).")
        return []

    client = anthropic.Anthropic()
    prompt = build_clustering_prompt(clusterable, config)

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = message.content[0].text.strip()

    # Strip markdown code fences if present
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


def move_note_to_cluster(note_path: Path, cluster_dir: Path) -> Path:
    """Move a note file into a cluster subfolder."""
    dest = cluster_dir / note_path.name
    if dest == note_path:
        return dest  # Already there
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


def apply_clusters(clusters: list[dict], all_notes: list[dict]):
    """Create cluster folders, move notes, and generate Maps of Content."""
    if not clusters:
        print("No clusters to create.")
        return

    for cluster in clusters:
        name = cluster["name"]
        description = cluster.get("description", "")
        note_titles = cluster.get("notes", [])

        if len(note_titles) < CLUSTER_THRESHOLD:
            print(f"  [skip] \"{name}\" — only {len(note_titles)} notes (need {CLUSTER_THRESHOLD})")
            continue

        # Create cluster subfolder
        cluster_dir = NOTES_DIR / name
        cluster_dir.mkdir(exist_ok=True)

        # Move notes
        moved = []
        for title in note_titles:
            note_path = find_note_path(title, all_notes)
            if note_path and note_path.exists():
                new_path = move_note_to_cluster(note_path, cluster_dir)
                moved.append(title)
                print(f"  [move] \"{title}\" → {name}/")
            else:
                print(f"  [miss] \"{title}\" — not found, skipping")

        # Generate Map of Content
        if moved:
            moc_path = generate_moc(name, description, moved, cluster_dir)
            print(f"  [moc]  {moc_path.name}")

        print(f"  Cluster \"{name}\": {len(moved)} notes organized.\n")


def run_clustering():
    """Main clustering pipeline."""
    print("=== Alluvium — Clustering ===\n")

    config = load_config()
    all_notes = collect_all_notes()

    print(f"Total notes in system: {len(all_notes)}\n")

    # Identify clusters
    print("Analyzing for emergent clusters...")
    clusters = identify_clusters(all_notes, config)

    if not clusters:
        print("No clusters emerged yet. Keep writing.\n")
        return

    print(f"Found {len(clusters)} potential cluster(s).\n")

    # Apply clustering
    print("Organizing notes:\n")
    apply_clusters(clusters, all_notes)

    print("=== Clustering complete ===")


if __name__ == "__main__":
    run_clustering()
