#!/usr/bin/env python3
"""
Alluvium — Cluster Engine (PARA Edition)
Scans all notes and people, classifies them into PARA categories (Projects,
Areas, Resources, Archive), organizes people, and generates Maps of Content.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import yaml

from llm import call_llm_json, is_para_enabled

# --- Paths ---
BASE_DIR = Path(__file__).parent
NOTES_DIR = BASE_DIR / "01 Inbox"
PROJECTS_DIR = BASE_DIR / "1 Projects"
AREAS_DIR = BASE_DIR / "2 Areas"
RESOURCES_DIR = BASE_DIR / "3 Resources"
ARCHIVE_DIR = BASE_DIR / "4 Archive"
PEOPLE_DIR = BASE_DIR / "People"
AUTHORS_DIR = BASE_DIR / "Authors"
CONFIG_PATH = BASE_DIR / "config.yaml"

PARA_DIRS = {
    "projects": PROJECTS_DIR,
    "areas": AREAS_DIR,
    "resources": RESOURCES_DIR,
    "archive": ARCHIVE_DIR,
}

ALL_CONTENT_DIRS = [NOTES_DIR, PROJECTS_DIR, AREAS_DIR, RESOURCES_DIR, ARCHIVE_DIR]
ALL_PEOPLE_DIRS = [PEOPLE_DIR, AUTHORS_DIR]

CLUSTER_THRESHOLD = 3


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def read_note_metadata(filepath: Path) -> dict | None:
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


def collect_notes_from(folders: list[Path]) -> list[dict]:
    notes = []
    for folder in folders:
        if not folder.exists():
            continue
        for f in folder.rglob("*.md"):
            if f.name.startswith("_"):
                continue
            meta = read_note_metadata(f)
            if meta:
                notes.append(meta)
    return notes


def call_claude(prompt: str) -> list[dict]:
    return call_llm_json(prompt, max_tokens=4096)


def sanitize_folder_name(name: str) -> str:
    """Make an LLM-proposed cluster name safe to use as a folder name."""
    clean = name.replace("/", "–").replace(":", " —").strip(". ")
    clean = re.sub(r"[\x00-\x1f]", "", clean)
    return clean or "Unnamed Cluster"


def find_note_path(title: str, notes: list[dict]) -> Path | None:
    for n in notes:
        if n.get("title", "") == title:
            return n["_path"]
    return None


def move_note_to_folder(note_path: Path, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / note_path.name
    if dest == note_path:
        return dest
    shutil.move(str(note_path), str(dest))
    return dest


def generate_moc(name: str, description: str, note_titles: list[str], target_dir: Path):
    moc_path = target_dir / f"_{name}.md"

    # Merge with links already in the MOC — clustering runs only see Inbox
    # notes, so regenerating from scratch would drop earlier members.
    titles = set(note_titles)
    if moc_path.exists():
        existing_text = moc_path.read_text(encoding="utf-8")
        titles.update(re.findall(r"^- \[\[(.+?)\]\]", existing_text, flags=re.MULTILINE))

    frontmatter = {
        "title": name,
        "type": "map-of-content",
        "tags": ["moc"],
        "status": "active",
    }

    links = "\n".join(f"- [[{title}]]" for title in sorted(titles))

    content = "---\n"
    content += yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True, sort_keys=False)
    content += "---\n\n"
    content += f"{description}\n\n"
    content += f"## Notes\n\n{links}\n"

    moc_path.write_text(content, encoding="utf-8")
    return moc_path


# ---------------------------------------------------------------------------
# PARA CLUSTERING
# ---------------------------------------------------------------------------

def build_para_prompt(notes: list[dict], config: dict) -> str:
    domains_desc = "\n".join(
        f"- **{key}** ({d['name']}): {d['description']}"
        for key, d in config["domains"].items()
    )

    para = config.get("para", {})
    para_desc = "\n".join(
        f"- **{cat.upper()}**: {info['description']}. Examples: {', '.join(info.get('examples', []))}"
        for cat, info in para.items()
    )

    notes_summary = "\n".join(
        f"- \"{n.get('title', n['_slug'])}\" | type: {n.get('type', '?')} | domain: {n.get('domain', '?')} | tags: {', '.join(n.get('tags', []))} | location: {n['_path'].parent.name}"
        for n in notes
    )

    return f"""You are an organizational analyst for a personal knowledge system that uses the PARA method (Projects, Areas, Resources, Archive).

## PARA Categories
{para_desc}

## Life Domains
{domains_desc}

## All notes in the system
{notes_summary}

## Your task
1. Group notes into meaningful clusters (e.g., "Triathlon Training", "Book Manuscript", "LLM Research Wiki").
2. For each cluster, assign a PARA category:
   - **projects**: Active effort with a clear deliverable or deadline
   - **areas**: Ongoing responsibility or practice with no end date
   - **resources**: Reference material, topics of interest, things to learn from
   - **archive**: Completed or inactive
3. A cluster must have at least {CLUSTER_THRESHOLD} notes.
4. Not every note needs to be clustered. Unclustered notes stay in Notes/.
5. A note can only belong to one cluster.
6. All athletic/training notes (swim, run, bike, weights, flexibility) must cluster together.
7. If notes are already in the correct PARA folder and cluster, include them but they won't be moved again.

## Output Format
Return a JSON array of cluster objects:
- "name": human-readable cluster name (becomes the subfolder name)
- "para": one of "projects", "areas", "resources", "archive"
- "description": one-sentence description
- "notes": list of exact note titles

If no clusters are warranted, return an empty array: []

Return ONLY the JSON array. No markdown code fences, no commentary."""


def apply_para_clusters(clusters: list[dict], all_notes: list[dict]):
    if not clusters:
        return

    for cluster in clusters:
        name = sanitize_folder_name(cluster["name"])
        para_cat = cluster.get("para", "resources")
        description = cluster.get("description", "")
        note_titles = cluster.get("notes", [])

        if len(note_titles) < CLUSTER_THRESHOLD:
            print(f"  [skip] \"{name}\" — only {len(note_titles)} notes (need {CLUSTER_THRESHOLD})")
            continue

        # Determine target directory based on PARA category
        para_dir = PARA_DIRS.get(para_cat, RESOURCES_DIR)
        cluster_dir = para_dir / name
        cluster_dir.mkdir(parents=True, exist_ok=True)

        moved = []
        for title in note_titles:
            note_path = find_note_path(title, all_notes)
            if note_path and note_path.exists():
                if note_path.parent == cluster_dir:
                    moved.append(title)
                    continue
                move_note_to_folder(note_path, cluster_dir)
                moved.append(title)
                print(f"  [move] \"{title}\" → {para_cat.capitalize()}/{name}/")
            else:
                print(f"  [miss] \"{title}\" — not found")

        if moved:
            generate_moc(name, description, moved, cluster_dir)
            print(f"  [moc]  _{name}.md")

        print(f"  [{para_cat.upper()}] \"{name}\": {len(moved)} notes.\n")


# ---------------------------------------------------------------------------
# PEOPLE CLUSTERING
# ---------------------------------------------------------------------------

DEFAULT_PEOPLE_CATEGORIES = [
    {"name": "Friends", "description": "personal friends, social connections"},
    {"name": "Family", "description": "family members"},
    {"name": "Colleagues", "description": "professional colleagues and collaborators"},
    {"name": "Online Contacts", "description": "people known through online interactions, tech community, social media"},
]

AUTHORS_CATEGORY = {
    "name": "Authors",
    "description": "creators known through their work: writers, philosophers, composers, painters, filmmakers, thinkers. NOT personal acquaintances who happen to write. These move to Authors/.",
}


def get_people_categories(config: dict) -> list[dict]:
    """People categories from config (people.categories with name + description),
    falling back to generic defaults. The Authors category is always present —
    the Authors/ folder routing depends on it."""
    cats = (config.get("people") or {}).get("categories") or []
    cats = [c for c in cats if isinstance(c, dict) and c.get("name")]
    if not cats:
        cats = list(DEFAULT_PEOPLE_CATEGORIES)
    if not any(c["name"].strip().lower() == "authors" for c in cats):
        cats = cats + [AUTHORS_CATEGORY]
    return cats


def build_people_prompt(people: list[dict], config: dict) -> str:
    domains_desc = "\n".join(
        f"- **{key}** ({d['name']}): {d['description']}"
        for key, d in config["domains"].items()
    )

    categories_desc = "\n".join(
        f"- **{c['name']}** — {c.get('description', '')}"
        for c in get_people_categories(config)
    )

    people_summary = "\n".join(
        f"- \"{p.get('title', p['_slug'])}\" | domain: {p.get('domain', '?')} | tags: {', '.join(p.get('tags', []))} | context: {p.get('_body', '')[:200].strip()}"
        for p in people
    )

    return f"""You are an organizational analyst for a personal knowledge system. Categorize these people.

## Life Domains
{domains_desc}

## People categories
{categories_desc}

## All people in the system
{people_summary}

## Rules
1. A category must have at least {CLUSTER_THRESHOLD} people to form a subfolder.
2. Authors move to a separate Authors/ folder (set "move_to_authors": true).
3. A person belongs to one category only.
4. Uncategorized people stay at the People/ root.
5. Use context clues from tags, domain, and the note body to decide.

## Output Format
Return a JSON array:
- "name": category name
- "description": one-sentence description
- "people": list of exact person titles
- "move_to_authors": true/false

Return ONLY the JSON array. No markdown code fences, no commentary."""


def apply_people_clusters(clusters: list[dict], all_people: list[dict]):
    if not clusters:
        return

    for cluster in clusters:
        name = sanitize_folder_name(cluster["name"])
        description = cluster.get("description", "")
        people_titles = cluster.get("people", [])
        move_to_authors = cluster.get("move_to_authors", False)

        if len(people_titles) < CLUSTER_THRESHOLD:
            print(f"  [skip] \"{name}\" — only {len(people_titles)} people (need {CLUSTER_THRESHOLD})")
            continue

        target_dir = AUTHORS_DIR if move_to_authors else PEOPLE_DIR / name
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
                label = "Authors/" if move_to_authors else f"People/{name}/"
                print(f"  [move] \"{title}\" → {label}")
            else:
                print(f"  [miss] \"{title}\" — not found")

        if moved:
            generate_moc(name, description, moved, target_dir)
            print(f"  [moc]  _{name}.md")

        label = "Authors" if move_to_authors else name
        print(f"  Category \"{label}\": {len(moved)} people.\n")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def run_clustering():
    print("=== Alluvium — PARA Clustering ===\n")

    config = load_config()

    # --- Notes / PARA clustering ---
    # Only cluster notes still in Inbox (unclustered). Already-classified notes stay put.
    all_notes = collect_notes_from(ALL_CONTENT_DIRS)
    inbox_notes = collect_notes_from([NOTES_DIR])
    print(f"Notes in system: {len(all_notes)} ({len(inbox_notes)} in Inbox)")

    if len(inbox_notes) >= CLUSTER_THRESHOLD:
        print(f"Analyzing {len(inbox_notes)} inbox notes for PARA classification...")
        clusters = call_claude(build_para_prompt(inbox_notes, config))
        if clusters:
            print(f"Found {len(clusters)} cluster(s).\n")
            apply_para_clusters(clusters, all_notes)
        else:
            print("No clusters emerged.\n")
    else:
        print(f"Only {len(inbox_notes)} note(s) in Inbox — skipping clustering.\n")

    # --- People clustering ---
    # Only categorize people at the root of People/ or in Uncategorized/
    all_people = collect_notes_from(ALL_PEOPLE_DIRS)
    uncategorized_dirs = [PEOPLE_DIR / "Uncategorized"]
    # Also include people directly in People/ root (not in subfolders)
    root_people = [
        meta for f in PEOPLE_DIR.glob("*.md")
        if not f.name.startswith("_") and (meta := read_note_metadata(f))
    ] if PEOPLE_DIR.exists() else []
    uncategorized_people = collect_notes_from(uncategorized_dirs) + root_people
    print(f"People in system: {len(all_people)} ({len(uncategorized_people)} uncategorized)")

    if len(uncategorized_people) >= CLUSTER_THRESHOLD:
        print(f"Analyzing {len(uncategorized_people)} uncategorized people...")
        people_clusters = call_claude(build_people_prompt(uncategorized_people, config))
        if people_clusters:
            print(f"Found {len(people_clusters)} category(ies).\n")
            apply_people_clusters(people_clusters, all_people)
        else:
            print("No people categories emerged.\n")
    else:
        print(f"Only {len(uncategorized_people)} uncategorized — skipping.\n")

    print("=== PARA Clustering complete ===")


if __name__ == "__main__":
    run_clustering()
