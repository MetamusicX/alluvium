#!/usr/bin/env python3
"""
Alluvium — Daily Summarizer
Reads the day's journal entry and extracted notes, generates a de-fragmented
structured summary ready for copy-pasting into Day One.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

from llm import call_llm

# --- Paths ---
BASE_DIR = Path(__file__).parent
JOURNAL_DIR = BASE_DIR / "00 Journal"
SUMMARIES_DIR = BASE_DIR / "Day Summaries"
CONFIG_PATH = BASE_DIR / "config.yaml"
DAYONE_SENT_PATH = BASE_DIR / ".dayone_sent.json"

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
        # Check month subfolder
        path = JOURNAL_DIR / target_date.strftime("%Y-%m") / f"{target_date.isoformat()}.md"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    # Strip pipeline-appended metadata sections — they're not journal content.
    for marker in ("## Concepts Extracted", "## Notes Folded In"):
        if marker in text:
            text = text.split(marker)[0].strip()
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
    owner = config.get("owner", "the writer")
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
3. **Keep the original voice** — this is {owner}'s journal, not a corporate report. First person, reflective, honest.
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
    prompt = build_summary_prompt(journal_text, notes, config, target_date)
    return call_llm(prompt, max_tokens=4096)


def _load_dayone_sent() -> set[str]:
    if not DAYONE_SENT_PATH.exists():
        return set()
    try:
        data = json.loads(DAYONE_SENT_PATH.read_text(encoding="utf-8"))
        return set(data.get("sent", []))
    except Exception:
        return set()


def _record_dayone_sent(target_date: date):
    sent = _load_dayone_sent()
    sent.add(target_date.isoformat())
    DAYONE_SENT_PATH.write_text(
        json.dumps({"sent": sorted(sent)}, indent=2) + "\n",
        encoding="utf-8",
    )


def send_to_dayone(summary: str, target_date: date):
    """Send the summary to Day One via CLI with correct date."""
    date_str = target_date.strftime("%Y-%m-%d 21:45")
    try:
        result = subprocess.run(
            ["dayone", "--date", date_str, "--tags", "alluvium", "daily-summary", "--", "new"],
            input=summary,
            text=True,
            capture_output=True,
            timeout=30,
        )
        if result.returncode == 0:
            _record_dayone_sent(target_date)
            print(f"Sent to Day One (CLI, dated {target_date.isoformat()}).")
        else:
            print(f"Day One CLI error: {result.stderr.strip()}")
            # Fallback to URL scheme
            _send_to_dayone_url(summary, target_date)
    except FileNotFoundError:
        print("dayone CLI not found — falling back to URL scheme.")
        _send_to_dayone_url(summary, target_date)
    except Exception as e:
        print(f"Could not send to Day One: {e}")


def _send_to_dayone_url(summary: str, target_date: date):
    """Fallback: send via URL scheme (no custom date support)."""
    import urllib.parse
    try:
        encoded = urllib.parse.quote(summary)
        url = f"dayone://post?entry={encoded}&tags=alluvium,daily-summary"
        subprocess.run(["open", url], check=True, timeout=10)
        _record_dayone_sent(target_date)
        print("Sent to Day One via URL scheme (today's date).")
    except Exception as e:
        print(f"Could not send to Day One: {e}")


def _dayone_enabled(config: dict) -> bool:
    """Day One delivery is opt-out: enabled by default, disabled via config on installs without Day One."""
    return bool(config.get("dayone_enabled", True))


def _summary_already_sent(target_date: date) -> bool:
    """Check the sentinel: was this date already sent to Day One?"""
    return target_date.isoformat() in _load_dayone_sent()


def _exit_if_unsent(target_date: date):
    """Exit non-zero when the send didn't land (sentinel not recorded).

    The summary file is already on disk, so the launchd retry loop / catch-up
    will take the re-send path instead of regenerating.
    """
    if not _summary_already_sent(target_date):
        print("\n⚠ Day One send did not complete — exiting non-zero so the retry loop can re-send.")
        sys.exit(1)


def run_summary(target_date: date):
    print("=== Alluvium — Daily Summary ===\n")

    config = load_config()
    date_str = target_date.isoformat()

    SUMMARIES_DIR.mkdir(exist_ok=True)
    month_dir = SUMMARIES_DIR / target_date.strftime("%Y-%m")
    if month_dir.exists():
        summary_path = month_dir / f"{date_str}.md"
    else:
        summary_path = SUMMARIES_DIR / f"{date_str}.md"

    dayone = _dayone_enabled(config)
    summary_exists = summary_path.exists()
    already_sent = _summary_already_sent(target_date)

    if summary_exists and (already_sent or not dayone):
        print(f"Summary for {date_str} already exists — nothing to do.")
        print(f"\n=== Summary complete ===")
        return

    if summary_exists and not already_sent:
        # File on disk but sentinel says not sent — reuse the file, just send.
        summary = summary_path.read_text(encoding="utf-8").rstrip("\n")
        print(f"Summary file already exists ({summary_path.name}) — re-sending to Day One.")
        send_to_dayone(summary, target_date)
        _exit_if_unsent(target_date)
        print(f"\n=== Summary complete — sent to Day One ===")
        return

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

    summary_path.write_text(summary + "\n", encoding="utf-8")
    print(f"Summary saved: {summary_path.name}")

    if not dayone:
        print("Day One delivery disabled (dayone_enabled: false) — summary saved locally.")
        print(f"\n=== Summary complete ===")
        return

    if already_sent:
        print(f"Sentinel already marks {date_str} as sent — skipping Day One re-send.")
        print(f"\n=== Summary complete — file written, not re-sent ===")
        return

    send_to_dayone(summary, target_date)
    _exit_if_unsent(target_date)

    print(f"\n=== Summary complete — sent to Day One ===")


def main():
    parser = argparse.ArgumentParser(description="Alluvium — Generate a daily summary for Day One.")
    parser.add_argument(
        "date",
        nargs="?",
        default=(date.today() - timedelta(days=1)).isoformat(),
        help="Date to summarize (YYYY-MM-DD). Defaults to yesterday, matching process_journal.py.",
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
