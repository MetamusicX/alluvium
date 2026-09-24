"""Tag handling across the pipeline.

Hand-edited frontmatter can hold an empty `tags:` (None), a bare `tags: solo`,
or numbers like `2026`, and LLM replies can return a string where a list is
expected. Every script must treat these as a list of strings — never crash,
and never split a tag into its letters when writing it back to disk.
"""

import pytest
import yaml

import cluster_notes
import llm
import process_journal
import ripple
import standalone

DATE = "2026-04-19"


def write_note(path, tags_line, body="Body."):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntitle: {path.stem}\n{tags_line}\n---\n{body}\n", encoding="utf-8")
    return path


def frontmatter(path):
    return yaml.safe_load(path.read_text(encoding="utf-8").split("---", 2)[1])


# The frontmatter shapes seen in the wild, and what they should normalise to.
FRONTMATTER_CASES = [
    ("tags:", []),
    ("tags: solo", ["solo"]),
    ("tags: [2026, run]", ["2026", "run"]),
    ("tags: [run, '']", ["run"]),
    ("", []),  # no tags key at all
]


# ---------------------------------------------------------------------------
# The shared helper
# ---------------------------------------------------------------------------

class TestNormalizeTags:
    @pytest.mark.parametrize("value, expected", [
        (None, []),
        ([], []),
        ("solo", ["solo"]),
        (2026, ["2026"]),
        (["a", 2026, None, "", "  b  "], ["a", "2026", "b"]),
        (("a", "b"), ["a", "b"]),
    ])
    def test_normalize(self, value, expected):
        assert llm.normalize_tags(value) == expected

    def test_set_input(self):
        assert sorted(llm.normalize_tags({"a", "b"})) == ["a", "b"]


# ---------------------------------------------------------------------------
# Readers: notes loaded for prompts always carry a clean list
# ---------------------------------------------------------------------------

class TestReaders:
    @pytest.mark.parametrize("tags_line, expected", FRONTMATTER_CASES)
    def test_cluster_notes(self, tmp_path, tags_line, expected):
        meta = cluster_notes.read_note_metadata(write_note(tmp_path / "n.md", tags_line))
        assert meta["tags"] == expected

    @pytest.mark.parametrize("tags_line, expected", FRONTMATTER_CASES)
    def test_ripple(self, tmp_path, tags_line, expected):
        note = ripple.read_note(write_note(tmp_path / "n.md", tags_line))
        assert note["tags"] == expected

    def test_cluster_prompts_do_not_split_single_tag(self, tmp_path):
        note = cluster_notes.read_note_metadata(write_note(tmp_path / "n.md", "tags: solo"))
        config = {"domains": {"work": {"name": "Job", "description": "Work"}}}
        assert "tags: solo |" in cluster_notes.build_para_prompt([note], config)
        assert "tags: solo |" in cluster_notes.build_people_prompt([note], config)

    def test_ripple_candidates_match_whole_tags(self, tmp_path):
        new = ripple.read_note(write_note(tmp_path / "new.md", "tags: solo"))
        # "sol" shares letters with "solo" but not the tag itself.
        letters = ripple.read_note(write_note(tmp_path / "letters.md", "tags: [s, o, l]"))
        match = ripple.read_note(write_note(tmp_path / "match.md", "tags: [solo]"))
        chosen = ripple.select_candidates([new], [letters, match], cap=1)
        assert chosen == [match]


# ---------------------------------------------------------------------------
# Writers: tags written back to disk are never mangled
# ---------------------------------------------------------------------------

class TestProcessJournalAppend:
    @pytest.mark.parametrize("tags_line, existing", FRONTMATTER_CASES)
    def test_merges_tags_and_marks_modified(self, tmp_path, tags_line, existing):
        path = write_note(tmp_path / "n.md", tags_line)
        process_journal.append_to_note(path, {"tags": ["new"], "body": "More."}, DATE)
        fm = frontmatter(path)
        assert fm["tags"] == sorted(set(existing) | {"new"})
        # The frontmatter path must run (not the plain-append fallback), or the
        # daily summary never sees this note as touched today.
        assert fm["date_modified"] == DATE

    def test_llm_string_tag_is_not_split(self, tmp_path):
        path = write_note(tmp_path / "n.md", "tags: [solo]")
        process_journal.append_to_note(path, {"tags": "run", "body": "More."}, DATE)
        assert frontmatter(path)["tags"] == ["run", "solo"]


class TestProcessJournalNewNote:
    def test_llm_string_tag_is_not_split(self, tmp_path, monkeypatch):
        monkeypatch.setattr(process_journal, "NOTES_DIR", tmp_path)
        note = {"title": "Tempo run", "type": "practice-log", "tags": "run", "body": "8k."}
        process_journal.write_note(note, DATE, {}, para_enabled=False)
        assert frontmatter(tmp_path / "tempo-run.md")["tags"] == ["run"]


class TestRippleApply:
    def apply(self, path, add_tags):
        notes = [ripple.read_note(path)]
        ripple.apply_ripples([{"target_title": path.stem, "add_tags": add_tags,
                               "add_related": [], "reason": "r"}], notes, DATE)
        return frontmatter(path)

    @pytest.mark.parametrize("tags_line, existing", FRONTMATTER_CASES)
    def test_merges_tags(self, tmp_path, tags_line, existing):
        fm = self.apply(write_note(tmp_path / "n.md", tags_line), ["new"])
        assert fm["tags"] == sorted(set(existing) | {"new"})
        assert fm["date_modified"] == DATE

    def test_llm_string_tag_is_not_split(self, tmp_path):
        fm = self.apply(write_note(tmp_path / "n.md", "tags: [solo]"), "run")
        assert fm["tags"] == ["run", "solo"]


class TestStandalone:
    CONFIG = {"domains": {"work": {"name": "Job", "description": "Work"}}, "note_types": ["idea"]}

    @pytest.fixture
    def classify_reply(self, monkeypatch):
        def _set(reply):
            monkeypatch.setattr(standalone, "call_llm_json", lambda prompt, max_tokens: reply)
        return _set

    @pytest.mark.parametrize("reply_tags, expected", [
        ("Run", ["run"]),
        (["Run", 2026, None], ["run", "2026"]),
        (None, []),
    ])
    def test_classify_normalises_llm_tags(self, classify_reply, reply_tags, expected):
        classify_reply({"domain": "work", "type": "idea", "tags": reply_tags})
        assert standalone._classify("t", "b", self.CONFIG, para_enabled=False)["tags"] == expected

    @pytest.mark.parametrize("tags_line, existing", FRONTMATTER_CASES)
    def test_fold_in_merges_user_tags(self, tmp_path, monkeypatch, classify_reply, tags_line, existing):
        monkeypatch.setattr(standalone, "INBOX_DIR", tmp_path)
        classify_reply({"domain": "work", "type": "idea", "tags": ["new"]})
        path = write_note(tmp_path / "n.md", tags_line)
        standalone._fold_in(path, DATE, self.CONFIG, {}, para_enabled=False)
        assert frontmatter(path)["tags"] == sorted(set(existing) | {"new"})
