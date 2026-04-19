#!/usr/bin/env python3
"""
Alluvium — Daily Summarizer
Reads the day's journal entry and extracted notes, generates a de-fragmented
structured summary ready for copy-pasting into Day One.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import anthropic
import yaml

# --- Paths ---
BASE_DIR = Path(__file__).parent
JOURNAL_DIR = BASE_DIR / "00 Journal"
SUMMARIES_DIR = BASE_DIR / "Day Summaries"
CONFIG_PATH = BASE_DIR / "config.yaml"

# All content folders to scan for today's notes
ALL_CONTENT_DIRS = [
    BASE_DIR / "01 Inbox",
    BASE_DIR / "1 Projects",
    BASE_DIR / "2 Areas",
    BASE_DIR / "3 Resources",
    BASE_DIR / "4 Archive",
    BASE_DIR / "People",
    BASE_DIR / "Authors",
]


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def get_journal_text(target_date: date) -> str:
    path = JOURNAL_DIR / f"{target_date.isoformat()}.md"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    # Strip the "Concepts Extracted" section — that's metadata, not content
    if "## Concepts Extracted" in text:
        text = text.split("## Concepts Extracted")[0].strip()
    return text


def collect_todays_notes(target_date: date) -> list[dict]:
    """Find all notes created or updated on the target date."""
    date_str = target_date.isoformat()
    notes = []
    for folder in ALL_CONTENT_DIRS:
        if not folder.exists():
            continue
        for f in folder.rglob("*.md"):
            if f.name.startswith("_"):
                continue
            text = f.read_text(encoding="utf-8")
            if not text.startswith("---"):
                continue
            try:
                fm_text = text.split("---", 2)[1]
                fm = yaml.safe_load(fm_text)
                body = text.split("---", 2)[2].strip() if len(text.split("---", 2)) > 2 else ""
                # Include if created or modified today
                if str(fm.get("date_created", "")) == date_str or str(fm.get("date_modified", "")) == date_str:
                    notes.append({
                        "title": fm.get("title", f.stem),
                        "type": fm.get("type", "note"),
                        "domain": fm.get("domain", "personal"),
                        "tags": fm.get("tags", []),
                        "body": body[:500],
                    })
            except Exception:
                continue
    return notes


def build_summary_prompt(journal_text: str, notes: list[dict], config: dict, target_date: str) -> str:
    domains_desc = "\n".join(
        f"- **{d['name']}**: {d['description']}"
        for d in config["domains"].values()
    )

    notes_section = "\n\n".join(
        f"### {n['title']}\nType: {n['type']} | Domain: {n['domain']} | Tags: {', '.join(n.get('tags', []))}\n{n['body']}"
        for n in notes
    )

    return f"""You are writing a structured daily summary for a personal journal. Your job is to take a raw journal entry and its extracted notes, then produce a clean, de-fragmented summary of the day.

## Life Domains
{domains_desc}

## Rules
1. **De-fragment**: If the same topic appears at multiple points in the day, bring those moments together under one heading.
2. **Group by theme**, not by time. All training goes together. All work goes together. All writing goes together.
3. **Keep the original voice** — this is Paulo's journal, not a corporate report. First person, reflective, honest.
4. **Be concise but complete** — capture everything meaningful, skip nothing important, but don't pad.
5. **Include people** — who was mentioned or interacted with, and in what context.
6. **Include feelings and reflections** — these matter as much as events.
7. **End with a brief "Open threads" section** — things left unresolved or to follow up on.

## Output structure
Use this exact structure (skip any section that has no content for the day):

# [Full date, e.g., "Saturday, April 19, 2026"]

## Work
(grouped work activities)

## Writing & Research
(all writing, reading, intellectual work)

## Training
(all sport and physical activity)

## Music
(piano practice or other musical activity)

## People
(interactions, who and about what)

## Ideas & Reflections
(creative thoughts, personal observations, feelings)

## Tasks & Next Steps
(what came up, what's pending, what to follow up)

## Open Threads
(unresolved items, things to think about)

---

## Today's raw journal entry
{journal_text}

## Extracted notes from today
{notes_section}

Date: {target_date}

Write the summary now. Output ONLY the markdown summary, nothing else."""


def generate_summary(journal_text: str, notes: list[dict], config: dict, target_date: str) -> str:
    client = anthropic.Anthropic()
    prompt = build_summary_prompt(journal_text, notes, config, target_date)

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    return message.content[0].text.strip()


def send_to_dayone(summary: str, target_date: date):
    """Send the summary to Day One via URL scheme (preserves markdown formatting)."""
    import urllib.parse
    try:
        encoded = urllib.parse.quote(summary)
        date_str = target_date.strftime("%B %d, %Y")
        url = f"dayone://post?entry={encoded}&tags=alluvium,daily-summary"
        subprocess.run(["open", url], check=True, timeout=10)
        print("Sent to Day One (with formatting).")
    except Exception as e:
        print(f"Could not send to Day One: {e}")


def run_summary(target_date: date):
    print("=== Alluvium — Daily Summary ===\n")

    config = load_config()
    date_str = target_date.isoformat()

    # Read journal
    journal_text = get_journal_text(target_date)
    if not journal_text:
        print(f"No journal entry found for {date_str}. Nothing to summarize.\n")
        return

    # Collect today's notes
    notes = collect_todays_notes(target_date)
    print(f"Journal: {date_str}")
    print(f"Notes from today: {len(notes)}\n")

    # Generate summary
    print("Generating summary...")
    summary = generate_summary(journal_text, notes, config, date_str)

    # Save
    SUMMARIES_DIR.mkdir(exist_ok=True)
    summary_path = SUMMARIES_DIR / f"{date_str}.md"
    summary_path.write_text(summary + "\n", encoding="utf-8")

    print(f"Summary saved: {summary_path.name}")

    # Send to Day One
    send_to_dayone(summary, target_date)

    print(f"\n=== Summary complete — sent to Day One ===")


def main():
    parser = argparse.ArgumentParser(description="Alluvium — Generate a daily summary for Day One.")
    parser.add_argument(
        "date",
        nargs="?",
        default=date.today().isoformat(),
        help="Date to summarize (YYYY-MM-DD). Defaults to today.",
    )
    args = parser.parse_args()

    try:
        target_date = date.fromisoformat(args.date)
    except ValueError:
        print(f"Invalid date format: {args.date}. Use YYYY-MM-DD.")
        sys.exit(1)

    run_summary(target_date)


if __name__ == "__main__":
    main()
