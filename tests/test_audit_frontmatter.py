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


# ---------------------------------------------------------------------------
# --fix
# ---------------------------------------------------------------------------

def note_text(fm_lines, body="Body.\n"):
    return f"---\n{fm_lines}\n---\n{body}"


class TestFixNote:
    @pytest.mark.parametrize("damaged, repaired", [
        # yaml.dump layout written by the old pipeline
        ("related:\n- '[''Note'']'\n- '[[New]]'", "related:\n- '[[Note]]'\n- '[[New]]'"),
        ("related:\n- '['\n- '['\n- N\n- o\n- t\n- e\n- ']'\n- ']'", "related:\n- '[[Note]]'"),
        ("related:\n- '[[[[Note]]]]'", "related:\n- '[[Note]]'"),
        # hand-written layouts
        ("related:\n  - \"['Note']\"", "related:\n- '[[Note]]'"),
        ("related: [\"['A']\",\n    \"[[B]]\"]", "related:\n- '[[A]]'\n- '[[B]]'"),
    ])
    def test_repairs_only_the_related_lines(self, tmp_path, damaged, repaired):
        before = "title: T\n# my comment\ndate_modified: 2026-01-01\n{}\nstatus: active"
        path = tmp_path / "n.md"
        body = "Body with a line that says\nrelated: not frontmatter\n---\nmore\n"
        path.write_text(note_text(before.format(damaged), body))
        assert audit.fix_note(path) == "fixed"
        assert path.read_text() == note_text(before.format(repaired), body)

    def test_second_run_is_a_no_op(self, tmp_path):
        path = tmp_path / "n.md"
        path.write_text(note_text("related:\n- '[''Note'']'"))
        assert audit.fix_note(path) == "fixed"
        fixed = path.read_text()
        assert audit.fix_note(path) == "clean"
        assert path.read_text() == fixed

    @pytest.mark.parametrize("fm_lines", [
        "tags: [l, o, s]",              # tags damage is not auto-fixed
        "related: ['[[Note]]']",        # healthy
        "title: no related key",
    ])
    def test_leaves_other_notes_untouched(self, tmp_path, fm_lines):
        path = tmp_path / "n.md"
        path.write_text(note_text(fm_lines))
        assert audit.fix_note(path) == "clean"
        assert path.read_text() == note_text(fm_lines)

    def test_skips_when_layout_is_ambiguous(self, tmp_path):
        # Duplicate `related` keys: YAML keeps the last, so editing either is unsafe.
        text = note_text("related: [x]\nrelated:\n- '[''Note'']'")
        path = tmp_path / "n.md"
        path.write_text(text)
        assert audit.fix_note(path) == "skipped"
        assert path.read_text() == text

    def test_skips_when_edit_would_change_another_key(self, tmp_path, monkeypatch):
        text = note_text("related:\n- '[''Note'']'\nstatus: active")
        path = tmp_path / "n.md"
        path.write_text(text)
        # Simulate a block replacement that swallows the next key.
        monkeypatch.setattr(audit, "_replace_related_block",
                            lambda fm_text, related: "\nrelated:\n- '[[Note]]'\n")
        assert audit.fix_note(path) == "skipped"
        assert path.read_text() == text


class TestMainFix:
    def run(self, monkeypatch, root):
        monkeypatch.setattr("sys.argv", ["audit_frontmatter.py", str(root), "--fix"])
        with pytest.raises(SystemExit) as exc:
            audit.main()
        return exc.value.code

    def test_fixes_everything_recoverable(self, tmp_path, monkeypatch, capsys):
        write_note(tmp_path, "01 Inbox/a.md", "related:\n- \"['Note']\"")
        write_note(tmp_path, "People/b.md", "related:\n- '[[[[X]]]]'")
        assert self.run(monkeypatch, tmp_path) == 0
        assert "Fixed related links in 2 note(s)" in capsys.readouterr().out
        assert audit.audit(tmp_path) == {}

    def test_reports_what_is_left_for_hand_fixing(self, tmp_path, monkeypatch, capsys):
        write_note(tmp_path, "01 Inbox/a.md", "related:\n- \"['Note']\"")
        tags = write_note(tmp_path, "01 Inbox/t.md", "tags: [l, o, s]")
        tags_before = tags.read_text()
        assert self.run(monkeypatch, tmp_path) == 1
        out = capsys.readouterr().out
        assert "Fixed related links in 1 note(s)" in out
        assert "1 note(s) still need fixing by hand" in out
        assert tags.read_text() == tags_before

    def test_without_fix_nothing_is_written(self, tmp_path, monkeypatch, capsys):
        path = write_note(tmp_path, "01 Inbox/a.md", "related:\n- \"['Note']\"")
        before = path.read_text()
        monkeypatch.setattr("sys.argv", ["audit_frontmatter.py", str(tmp_path)])
        with pytest.raises(SystemExit):
            audit.main()
        assert path.read_text() == before
        assert "Run again with --fix" in capsys.readouterr().out
