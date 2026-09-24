"""Tests for audit_frontmatter.py — flags notes damaged by the pre-fix tag/related handling."""

import pytest

import audit_frontmatter as audit


def write_note(root, rel, fm_lines):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntitle: {path.stem}\n{fm_lines}\n---\nBody\n", encoding="utf-8")
    return path


class TestCheckTags:
    @pytest.mark.parametrize("tags", [
        None, "solo", [], ["run", "piano"], ["c", "python"], [2026, 1],
    ])
    def test_healthy(self, tags):
        assert audit.check_tags(tags) == []

    def test_split_letters(self):
        [msg] = audit.check_tags(["l", "o", "run", "s"])
        assert "['l', 'o', 's']" in msg
        assert "fix by hand" in msg


class TestCheckRelated:
    @pytest.mark.parametrize("related", [
        None, "[[Note]]", [], ["[[A]]", "[[B]]"], ["Plain title"], [["Note"]],
    ])
    def test_healthy(self, related):
        assert audit.check_related(related) == []

    def test_split_characters_are_rejoined(self):
        related = list("[[Note]]") + ["[[New]]"]
        problems = audit.check_related(related)
        assert problems[0].startswith("related split into characters")
        assert problems[-1] == "suggested related: ['[[Note]]', '[[New]]']"

    def test_list_repr(self):
        problems = audit.check_related(["['A']", "['B']", "[[New]]"])
        assert problems[-1] == "suggested related: ['[[A]]', '[[B]]', '[[New]]']"

    def test_double_bracketed(self):
        problems = audit.check_related(["[[[[Note]]]]", "[[Note]]"])
        assert problems[0] == "related entry is double-bracketed: '[[[[Note]]]]'"
        assert problems[-1] == "suggested related: ['[[Note]]']"  # de-duplicated

    def test_keeps_healthy_entries_in_suggestion(self):
        problems = audit.check_related(["[[Keep]]", "['A']"])
        assert problems[-1] == "suggested related: ['[[Keep]]', '[[A]]']"


class TestAudit:
    def test_finds_damage_across_folders(self, tmp_path):
        write_note(tmp_path, "01 Inbox/a.md", "tags: [l, o, s]")
        write_note(tmp_path, "2 Areas/deep/b.md", "related:\n  - \"['Note']\"")
        write_note(tmp_path, "People/c.md", "tags: [friend]\nrelated: ['[[Note]]']")
        write_note(tmp_path, "00 Journal/d.md", "tags: [l, o, s]")  # not a note folder
        findings = audit.audit(tmp_path)
        assert sorted(str(p) for p in findings) == ["01 Inbox/a.md", "2 Areas/deep/b.md"]

    def test_ignores_unparseable_and_frontmatterless_files(self, tmp_path):
        (tmp_path / "01 Inbox").mkdir()
        (tmp_path / "01 Inbox" / "plain.md").write_text("no frontmatter")
        (tmp_path / "01 Inbox" / "broken.md").write_text("---\ntags: [unclosed\n---\n")
        (tmp_path / "01 Inbox" / "scalar.md").write_text("---\njust a string\n---\n")
        assert audit.audit(tmp_path) == {}

    def test_is_read_only(self, tmp_path):
        path = write_note(tmp_path, "01 Inbox/a.md", "tags: [l, o, s]")
        before = path.read_text()
        audit.audit(tmp_path)
        assert path.read_text() == before


class TestMain:
    def run(self, monkeypatch, root):
        monkeypatch.setattr("sys.argv", ["audit_frontmatter.py", str(root)])
        with pytest.raises(SystemExit) as exc:
            audit.main()
        return exc.value.code

    def test_exit_zero_when_clean(self, tmp_path, monkeypatch, capsys):
        write_note(tmp_path, "01 Inbox/a.md", "tags: [run]")
        assert self.run(monkeypatch, tmp_path) == 0
        assert "No damaged" in capsys.readouterr().out

    def test_exit_one_and_lists_notes(self, tmp_path, monkeypatch, capsys):
        write_note(tmp_path, "01 Inbox/a.md", "tags: [l, o, s]")
        assert self.run(monkeypatch, tmp_path) == 1
        out = capsys.readouterr().out
        assert "Found 1 note(s)" in out
        assert "01 Inbox/a.md" in out
