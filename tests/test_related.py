"""`related` link handling across the pipeline.

Frontmatter `related` can be empty (None), a single quoted link, or — when typed
the Obsidian way without quotes — `related: [[Note]]`, which YAML parses as a
nested list [["Note"]]. LLM replies may send titles with or without brackets.
Every script must end up with a flat list of "[[Title]]" links and never write
back characters, Python list reprs, or double brackets.
"""

import pytest
import yaml

import llm
import process_journal
import ripple
import standalone

DATE = "2026-04-19"


def write_note(path, related_line, body="Body."):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntitle: {path.stem}\n{related_line}\n---\n{body}\n", encoding="utf-8")
    return path


def frontmatter(path):
    return yaml.safe_load(path.read_text(encoding="utf-8").split("---", 2)[1])


# Frontmatter shapes seen in the wild, and the links they should normalise to.
FRONTMATTER_CASES = [
    ("related:", []),
    ("", []),                                            # no related key
    ('related: "[[Note]]"', ["[[Note]]"]),               # single quoted link
    ("related: [[Note]]", ["[[Note]]"]),                 # unquoted -> [["Note"]]
    ("related: [[A], [B]]", ["[[A]]", "[[B]]"]),         # unquoted flow list
    ('related:\n  - [[A]]\n  - "[[B]]"', ["[[A]]", "[[B]]"]),  # mixed block list
    ('related: ["[[A]]", "[[A]]"]', ["[[A]]"]),          # duplicates
]


class TestNormalizeRelated:
    @pytest.mark.parametrize("value, expected", [
        (None, []),
        ("Note", ["[[Note]]"]),
        ("[[Note]]", ["[[Note]]"]),
        ("  [[ Note ]] ", ["[[Note]]"]),
        ([["Note"]], ["[[Note]]"]),
        ([[["A"]], "[[B]]", "C", None, "", "[[]]"], ["[[A]]", "[[B]]", "[[C]]"]),
        (["[[Note|alias]]"], ["[[Note|alias]]"]),
        ([2026], ["[[2026]]"]),
        (["A", "[[A]]"], ["[[A]]"]),
    ])
    def test_normalize(self, value, expected):
        assert llm.normalize_related(value) == expected


class TestRippleRead:
    @pytest.mark.parametrize("related_line, expected", FRONTMATTER_CASES)
    def test_read_note(self, tmp_path, related_line, expected):
        assert ripple.read_note(write_note(tmp_path / "n.md", related_line))["related"] == expected

    def test_prompt_lists_links_not_reprs(self, tmp_path):
        note = ripple.read_note(write_note(tmp_path / "n.md", "related: [[Note]]"))
        config = {"domains": {"work": {"name": "Job", "description": "Work"}}}
        prompt = ripple.build_ripple_prompt([note], [note], config)
        assert "Related: [[Note]]\n" in prompt
        assert "related: [[Note]] |" in prompt
        assert "['Note']" not in prompt


class TestRippleApply:
    def apply(self, path, add_related):
        notes = [ripple.read_note(path)]
        ripple.apply_ripples([{"target_title": path.stem, "add_related": add_related,
                               "add_tags": [], "reason": "r"}], notes, DATE)
        return frontmatter(path)

    @pytest.mark.parametrize("related_line, existing", FRONTMATTER_CASES)
    def test_adds_link_to_any_existing_shape(self, tmp_path, related_line, existing):
        fm = self.apply(write_note(tmp_path / "n.md", related_line), ["New"])
        assert fm["related"] == existing + ["[[New]]"]
        assert fm["date_modified"] == DATE

    @pytest.mark.parametrize("add_related", ["Note", ["[[Note]]"], [["Note"]]])
    def test_does_not_duplicate_existing_link(self, tmp_path, add_related):
        path = write_note(tmp_path / "n.md", "related: [[Note]]")
        fm = self.apply(path, add_related)
        assert fm["related"] == ["[[Note]]"]
        assert "date_modified" not in fm  # nothing new was added

    def test_llm_string_title_is_not_split(self, tmp_path):
        fm = self.apply(write_note(tmp_path / "n.md", "related:"), "Tempo run")
        assert fm["related"] == ["[[Tempo run]]"]


class TestProcessJournalNewNote:
    @pytest.mark.parametrize("related, expected", [
        (["Training Log"], ["[[Training Log]]"]),
        (["[[Training Log]]"], ["[[Training Log]]"]),  # LLM already bracketed it
        ("Training Log", ["[[Training Log]]"]),         # string, not list
        (None, []),
    ])
    def test_related_links(self, tmp_path, monkeypatch, related, expected):
        monkeypatch.setattr(process_journal, "NOTES_DIR", tmp_path)
        note = {"title": "Tempo run", "type": "idea", "related": related, "body": "8k."}
        process_journal.write_note(note, DATE, {}, para_enabled=False)
        assert frontmatter(tmp_path / "tempo-run.md")["related"] == expected


class TestStandaloneFoldIn:
    CONFIG = {"domains": {"work": {"name": "Job", "description": "Work"}}, "note_types": ["idea"]}

    @pytest.mark.parametrize("related_line, expected", FRONTMATTER_CASES)
    def test_user_related_is_preserved_as_links(self, tmp_path, monkeypatch, related_line, expected):
        monkeypatch.setattr(standalone, "INBOX_DIR", tmp_path)
        monkeypatch.setattr(standalone, "call_llm_json",
                            lambda prompt, max_tokens: {"domain": "work", "type": "idea", "tags": []})
        path = write_note(tmp_path / "n.md", related_line)
        standalone._fold_in(path, DATE, self.CONFIG, {}, para_enabled=False)
        assert frontmatter(path)["related"] == expected
