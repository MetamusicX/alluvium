"""Tests for summarize.py — journal/note collection, prompt building, Day One delivery,
and the run_summary decision flow (generate / re-send / skip).

Every path the module touches is redirected into a temp vault, the LLM is stubbed,
and subprocess.run is blocked unless a test fakes it (see conftest.py).
"""

import json
import subprocess
from datetime import date

import pytest

import summarize

DAY = date(2026, 4, 19)
CONFIG = """\
owner: Sam
dayone_enabled: {dayone}
domains:
  work:
    name: My Job
    description: Main professional work
  sport:
    name: Training
    description: Athletic training
"""


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path, monkeypatch):
    """A throwaway Alluvium folder tree wired into summarize's module-level paths."""
    monkeypatch.setattr(summarize, "BASE_DIR", tmp_path)
    monkeypatch.setattr(summarize, "JOURNAL_DIR", tmp_path / "00 Journal")
    monkeypatch.setattr(summarize, "SUMMARIES_DIR", tmp_path / "Day Summaries")
    monkeypatch.setattr(summarize, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(summarize, "DAYONE_SENT_PATH", tmp_path / ".dayone_sent.json")
    content_dirs = [tmp_path / name for name in
                    ("01 Inbox", "1 Projects", "2 Areas", "3 Resources", "4 Archive", "People", "Authors")]
    monkeypatch.setattr(summarize, "ALL_CONTENT_DIRS", content_dirs)
    (tmp_path / "00 Journal").mkdir()
    (tmp_path / "config.yaml").write_text(CONFIG.format(dayone="false"))
    return tmp_path


def set_dayone(vault, enabled):
    (vault / "config.yaml").write_text(CONFIG.format(dayone=str(enabled).lower()))


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def note(title="A note", created="2026-04-19", modified="2026-04-10", tags="[x, y]", body="Body text."):
    return (f"---\ntitle: {title}\ntype: idea\ndomain: work\ntags: {tags}\n"
            f"date_created: {created}\ndate_modified: {modified}\n---\n{body}\n")


@pytest.fixture
def llm_calls(monkeypatch):
    """Stub the LLM; record prompts and return a fixed summary."""
    calls = []

    def _call_llm(prompt, max_tokens):
        calls.append({"prompt": prompt, "max_tokens": max_tokens})
        return "# Summary"

    monkeypatch.setattr(summarize, "call_llm", _call_llm)
    return calls


def fake_run(monkeypatch, *outcomes):
    """Make subprocess.run yield each outcome in turn: a CompletedProcess or an exception."""
    calls = []
    queue = list(outcomes)

    def _run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        outcome = queue.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(summarize.subprocess, "run", _run)
    return calls


def completed(returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout="", stderr=stderr)


def sent_dates(vault):
    path = vault / ".dayone_sent.json"
    return json.loads(path.read_text())["sent"] if path.exists() else []


# ---------------------------------------------------------------------------
# Reading the journal
# ---------------------------------------------------------------------------

class TestJournalText:
    def test_flat_layout(self, vault):
        write(vault / "00 Journal" / "2026-04-19.md", "Ran 10k.")
        assert summarize.get_journal_text(DAY) == "Ran 10k."

    def test_month_subfolder(self, vault):
        write(vault / "00 Journal" / "2026-04" / "2026-04-19.md", "Piano.")
        assert summarize.get_journal_text(DAY) == "Piano."

    def test_missing_entry(self, vault):
        assert summarize.get_journal_text(DAY) == ""

    @pytest.mark.parametrize("marker", ["## Concepts Extracted", "## Notes Folded In"])
    def test_strips_pipeline_metadata(self, vault, marker):
        write(vault / "00 Journal" / "2026-04-19.md", f"Real entry.\n\n{marker}\n- [[Note]]\n")
        assert summarize.get_journal_text(DAY) == "Real entry."


# ---------------------------------------------------------------------------
# Collecting today's notes
# ---------------------------------------------------------------------------

class TestCollectNotes:
    def test_includes_created_or_modified_today(self, vault):
        write(vault / "01 Inbox" / "created.md", note(title="Created"))
        write(vault / "2 Areas" / "deep" / "modified.md",
              note(title="Modified", created="2026-01-01", modified="2026-04-19"))
        write(vault / "People" / "old.md", note(title="Old", created="2026-01-01", modified="2026-01-02"))
        titles = sorted(n["title"] for n in summarize.collect_todays_notes(DAY))
        assert titles == ["Created", "Modified"]

    def test_note_fields(self, vault):
        write(vault / "01 Inbox" / "n.md", note(body="Hello."))
        [n] = summarize.collect_todays_notes(DAY)
        assert n == {"title": "A note", "type": "idea", "domain": "work", "tags": ["x", "y"], "body": "Hello."}

    def test_body_truncated_to_500_chars(self, vault):
        write(vault / "01 Inbox" / "n.md", note(body="a" * 800))
        [n] = summarize.collect_todays_notes(DAY)
        assert len(n["body"]) == 500

    def test_defaults_for_sparse_frontmatter(self, vault):
        write(vault / "01 Inbox" / "sparse.md", "---\ndate_created: 2026-04-19\n---\nBody\n")
        [n] = summarize.collect_todays_notes(DAY)
        assert n["title"] == "sparse"
        assert (n["type"], n["domain"], n["tags"]) == ("note", "personal", [])

    @pytest.mark.parametrize("tags, expected", [
        ("", []),                      # empty `tags:` parses as None
        ("solo", ["solo"]),            # single tag, not a list
        ("[2026, run]", ["2026", "run"]),  # non-string items
        ("[]", []),
    ])
    def test_tags_are_normalised_to_list_of_strings(self, vault, tags, expected):
        write(vault / "01 Inbox" / "n.md", note(tags=tags))
        [n] = summarize.collect_todays_notes(DAY)
        assert n["tags"] == expected

    def test_skips_underscore_files_and_non_frontmatter(self, vault):
        write(vault / "01 Inbox" / "_index.md", note())
        write(vault / "01 Inbox" / "plain.md", "No frontmatter, 2026-04-19")
        assert summarize.collect_todays_notes(DAY) == []

    def test_malformed_frontmatter_is_skipped_not_fatal(self, vault):
        write(vault / "01 Inbox" / "broken.md", "---\ntitle: [unclosed\n---\nbody")
        write(vault / "01 Inbox" / "empty.md", "---\n---\nbody")
        write(vault / "01 Inbox" / "good.md", note(title="Good"))
        assert [n["title"] for n in summarize.collect_todays_notes(DAY)] == ["Good"]

    def test_missing_folders_are_fine(self, vault):
        assert summarize.collect_todays_notes(DAY) == []


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

class TestPrompt:
    def config(self, **extra):
        return {"domains": {"work": {"name": "My Job", "description": "Main professional work"}}, **extra}

    def test_includes_all_inputs(self):
        notes = [{"title": "Tempo run", "type": "practice-log", "domain": "sport",
                  "tags": ["run", "tempo"], "body": "8k at threshold"}]
        prompt = summarize.build_summary_prompt("Journal body", notes, self.config(owner="Sam"), "2026-04-19")
        assert "- **My Job**: Main professional work" in prompt
        assert "this is Sam's journal" in prompt
        assert "Journal body" in prompt
        assert "### Tempo run\nType: practice-log | Domain: sport | Tags: run, tempo\n8k at threshold" in prompt
        assert "Date: 2026-04-19" in prompt

    def test_owner_defaults(self):
        prompt = summarize.build_summary_prompt("", [], self.config(), "2026-04-19")
        assert "this is the writer's journal" in prompt

    def test_generate_summary_uses_prompt(self, llm_calls):
        assert summarize.generate_summary("J", [], self.config(), "2026-04-19") == "# Summary"
        assert llm_calls[0]["max_tokens"] == 4096
        assert "Date: 2026-04-19" in llm_calls[0]["prompt"]


# ---------------------------------------------------------------------------
# Day One delivery
# ---------------------------------------------------------------------------

class TestSentinel:
    def test_record_and_load(self, vault):
        summarize._record_dayone_sent(date(2026, 4, 20))
        summarize._record_dayone_sent(DAY)
        summarize._record_dayone_sent(DAY)
        assert sent_dates(vault) == ["2026-04-19", "2026-04-20"]
        assert summarize._summary_already_sent(DAY)

    def test_corrupt_sentinel_reads_as_empty(self, vault):
        write(vault / ".dayone_sent.json", "{not json")
        assert summarize._load_dayone_sent() == set()

    @pytest.mark.parametrize("config, expected", [({}, True), ({"dayone_enabled": False}, False)])
    def test_dayone_enabled_defaults_on(self, config, expected):
        assert summarize._dayone_enabled(config) is expected


class TestSendToDayOne:
    def test_cli_success_records_sentinel(self, vault, monkeypatch):
        calls = fake_run(monkeypatch, completed())
        summarize.send_to_dayone("# Summary", DAY)
        cmd, kwargs = calls[0]
        assert cmd[:3] == ["dayone", "--date", "2026-04-19 21:45"]
        assert kwargs["input"] == "# Summary"
        assert sent_dates(vault) == ["2026-04-19"]

    def test_cli_error_falls_back_to_url_scheme(self, vault, monkeypatch):
        calls = fake_run(monkeypatch, completed(returncode=1, stderr="bad"), completed())
        summarize.send_to_dayone("a b&c", DAY)
        cmd, _ = calls[1]
        assert cmd[0] == "open"
        assert cmd[1].startswith("dayone://post?entry=a%20b%26c&")
        assert sent_dates(vault) == ["2026-04-19"]

    def test_missing_cli_falls_back_to_url_scheme(self, vault, monkeypatch):
        calls = fake_run(monkeypatch, FileNotFoundError("dayone"), completed())
        summarize.send_to_dayone("x", DAY)
        assert calls[1][0][0] == "open"
        assert sent_dates(vault) == ["2026-04-19"]

    def test_both_paths_failing_records_nothing(self, vault, monkeypatch):
        fake_run(monkeypatch, FileNotFoundError("dayone"), FileNotFoundError("open"))
        summarize.send_to_dayone("x", DAY)
        assert sent_dates(vault) == []

    def test_cli_timeout_does_not_crash_or_record(self, vault, monkeypatch):
        fake_run(monkeypatch, subprocess.TimeoutExpired(cmd="dayone", timeout=30))
        summarize.send_to_dayone("x", DAY)
        assert sent_dates(vault) == []


# ---------------------------------------------------------------------------
# run_summary decision flow
# ---------------------------------------------------------------------------

class TestRunSummary:
    def journal(self, vault):
        write(vault / "00 Journal" / "2026-04-19.md", "Today I ran.")

    def test_no_journal_does_nothing(self, vault, llm_calls):
        summarize.run_summary(DAY)
        assert llm_calls == []
        assert list((vault / "Day Summaries").iterdir()) == []

    def test_generates_and_saves_without_dayone(self, vault, llm_calls):
        self.journal(vault)
        write(vault / "01 Inbox" / "n.md", note(title="Run"))
        summarize.run_summary(DAY)
        assert (vault / "Day Summaries" / "2026-04-19.md").read_text() == "# Summary\n"
        assert "### Run" in llm_calls[0]["prompt"]
        assert sent_dates(vault) == []

    def test_odd_tags_do_not_break_the_summary(self, vault, llm_calls):
        self.journal(vault)
        write(vault / "01 Inbox" / "empty.md", note(title="Empty", tags=""))
        write(vault / "01 Inbox" / "single.md", note(title="Single", tags="solo"))
        summarize.run_summary(DAY)
        prompt = llm_calls[0]["prompt"]
        assert "Tags: solo\n" in prompt
        assert "Tags: \n" in prompt
        assert (vault / "Day Summaries" / "2026-04-19.md").exists()

    def test_saves_into_existing_month_folder(self, vault, llm_calls):
        self.journal(vault)
        (vault / "Day Summaries" / "2026-04").mkdir(parents=True)
        summarize.run_summary(DAY)
        assert (vault / "Day Summaries" / "2026-04" / "2026-04-19.md").exists()

    def test_existing_summary_is_not_regenerated(self, vault, llm_calls):
        self.journal(vault)
        write(vault / "Day Summaries" / "2026-04-19.md", "Old\n")
        summarize.run_summary(DAY)
        assert llm_calls == []
        assert (vault / "Day Summaries" / "2026-04-19.md").read_text() == "Old\n"

    def test_generates_and_sends_to_dayone(self, vault, llm_calls, monkeypatch):
        set_dayone(vault, True)
        self.journal(vault)
        calls = fake_run(monkeypatch, completed())
        summarize.run_summary(DAY)
        assert calls[0][1]["input"] == "# Summary"
        assert sent_dates(vault) == ["2026-04-19"]

    def test_existing_unsent_summary_is_resent_not_regenerated(self, vault, llm_calls, monkeypatch):
        set_dayone(vault, True)
        write(vault / "Day Summaries" / "2026-04-19.md", "Saved summary\n")
        calls = fake_run(monkeypatch, completed())
        summarize.run_summary(DAY)
        assert llm_calls == []
        assert calls[0][1]["input"] == "Saved summary"
        assert sent_dates(vault) == ["2026-04-19"]

    def test_existing_sent_summary_does_nothing(self, vault, llm_calls):
        set_dayone(vault, True)
        write(vault / "Day Summaries" / "2026-04-19.md", "Saved\n")
        summarize._record_dayone_sent(DAY)
        summarize.run_summary(DAY)  # subprocess.run is blocked, so any send would fail the test
        assert llm_calls == []

    def test_sent_but_file_missing_regenerates_without_resending(self, vault, llm_calls):
        set_dayone(vault, True)
        self.journal(vault)
        summarize._record_dayone_sent(DAY)
        summarize.run_summary(DAY)
        assert (vault / "Day Summaries" / "2026-04-19.md").read_text() == "# Summary\n"

    def test_failed_send_exits_nonzero_but_keeps_file(self, vault, llm_calls, monkeypatch):
        set_dayone(vault, True)
        self.journal(vault)
        fake_run(monkeypatch, FileNotFoundError("dayone"), FileNotFoundError("open"))
        with pytest.raises(SystemExit) as exc:
            summarize.run_summary(DAY)
        assert exc.value.code == 1
        assert (vault / "Day Summaries" / "2026-04-19.md").exists()


class TestMain:
    def test_invalid_date_exits(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["summarize.py", "19-04-2026"])
        with pytest.raises(SystemExit) as exc:
            summarize.main()
        assert exc.value.code == 1

    def test_parses_date_argument(self, monkeypatch):
        seen = []
        monkeypatch.setattr(summarize, "run_summary", seen.append)
        monkeypatch.setattr("sys.argv", ["summarize.py", "2026-04-19"])
        summarize.main()
        assert seen == [DAY]
