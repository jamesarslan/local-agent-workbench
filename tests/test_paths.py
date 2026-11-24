import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import paths


class PathsTest(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory(prefix="awb-paths-")
        self.addCleanup(holder.cleanup)
        self.tmp = str(paths.canonical(holder.name))
        self.root = Path(self.tmp)

    def test_empty_and_relative_rejected(self):
        for bad in ("", "   ", "docs", None, 5, b"/tmp"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                paths.canonical(bad)
        with self.assertRaisesRegex(ValueError, "full path"):
            paths.canonical("docs")
        with self.assertRaisesRegex(ValueError, "full path"):
            paths.project_key("./docs")

    def test_missing_and_file_rejected(self):
        file = self.root / "notes.txt"
        file.write_text("x")
        for bad in (self.root / "missing", file):
            with self.assertRaises(ValueError) as caught:
                paths.canonical(bad)
            self.assertEqual(str(caught.exception), paths.BAD_FOLDER)
        self.assertEqual(paths.canonical(file, folder=False), file)
        with self.assertRaises(ValueError) as caught:
            paths.canonical(self.tmp + "/a\0b")
        self.assertEqual(str(caught.exception), paths.BAD_FOLDER)

    def test_trailing_separator_and_dots(self):
        (self.root / "x").mkdir()
        key = paths.project_key(self.tmp)
        self.assertEqual(paths.project_key(self.tmp + os.sep), key)
        self.assertEqual(paths.project_key(os.path.join(self.tmp, "x", "..")), key)
        self.assertEqual(paths.project_key("  " + self.tmp + "  "), key)
        self.assertEqual(paths.project_key(Path(self.tmp)), key)

    def test_surrounding_spaces_are_dropped_only_for_a_path_that_does_not_exist(self):
        plain = self.root / "proj"
        plain.mkdir()
        self.assertEqual(paths.canonical(f"{plain} "), plain)
        self.assertTrue(paths.same(f"{plain} ", plain))
        with self.assertRaises(ValueError) as caught:
            paths.canonical(f" {self.root / 'missing'} ")
        self.assertEqual(str(caught.exception), paths.BAD_FOLDER)
        with self.assertRaises(ValueError) as caught:
            paths.canonical(" docs ")
        self.assertEqual(str(caught.exception), paths.RELATIVE)

    @unittest.skipIf(os.name == "nt", "Windows drops a trailing space from a name")
    def test_folder_named_with_a_trailing_space_is_its_own_folder(self):
        plain, spaced = self.root / "proj", self.root / "proj "
        spaced.mkdir()
        self.assertEqual(paths.canonical(str(spaced)).name, "proj ")
        plain.mkdir()
        self.assertEqual(paths.canonical(str(spaced)).name, "proj ")
        self.assertEqual(paths.canonical(str(plain)).name, "proj")
        self.assertFalse(paths.same(str(spaced), str(plain)))
        self.assertNotEqual(paths.project_key(str(spaced)), paths.project_key(str(plain)))

    def test_home_is_expanded(self):
        with patch.dict(os.environ, {"HOME": self.tmp, "USERPROFILE": self.tmp}):
            self.assertEqual(paths.canonical("~"), self.root)

    @unittest.skipIf(os.name == "nt", "symlinks need extra rights on Windows")
    def test_symlink_resolves_to_target(self):
        real = self.root / "real"
        real.mkdir()
        (self.root / "link").symlink_to(real, target_is_directory=True)
        self.assertEqual(
            paths.project_key(self.root / "link"), paths.project_key(real)
        )

    @unittest.skipUnless(sys.platform == "darwin", "macOS true-case lookup")
    def test_macos_true_case(self):
        (self.root / "MyProj").mkdir()
        wrong = os.path.join(self.tmp, "myproj")
        if not os.path.isdir(wrong):
            self.skipTest("this volume is case-sensitive")
        self.assertEqual(paths.canonical(wrong).name, "MyProj")
        self.assertEqual(paths.project_key(wrong), str(self.root / "MyProj"))

    @unittest.skipUnless(sys.platform == "darwin", "macOS true-case lookup")
    def test_macos_true_case_keeps_input_when_unreadable(self):
        with patch("os.open", side_effect=PermissionError):
            self.assertEqual(paths._true_case(self.tmp), self.tmp)

    def test_other_platforms_skip_true_case(self):
        with (
            patch.object(paths.sys, "platform", "linux"),
            patch.object(paths, "_true_case") as lookup,
        ):
            self.assertEqual(paths.canonical(self.tmp), self.root)
            lookup.assert_not_called()

    def test_fold_windows_branch(self):
        self.assertEqual(
            paths.fold("C:/Users/Me/Proj", windows=True), "c:\\users\\me\\proj"
        )
        self.assertEqual(paths.fold("/Users/Me", windows=False), "/Users/Me")
        with patch.object(paths.os, "name", "nt"):
            self.assertEqual(paths.fold("C:\\Users\\Me"), "c:\\users\\me")
        with patch.object(paths.os, "name", "posix"):
            self.assertEqual(paths.fold("/Users/Me"), "/Users/Me")

    def test_try_key_and_same(self):
        missing = os.path.join(self.tmp, "missing")
        file = self.root / "log.jsonl"
        file.write_text("{}")
        self.assertIsNone(paths.try_key(missing))
        self.assertIsNone(paths.try_key(None))
        self.assertEqual(paths.try_key(file), paths.fold(str(file)))
        self.assertTrue(paths.same(self.tmp, self.tmp + os.sep))
        self.assertFalse(paths.same(self.tmp, missing))
        self.assertFalse(paths.same(missing, missing))

    def test_inside_accepts_plain_names(self):
        first = paths.inside(self.root, ".agent-workbench", "profiles.json")
        second = paths.inside(self.root, ".agents/skills/workbench-x/SKILL.md")
        self.assertEqual(first, self.root / ".agent-workbench" / "profiles.json")
        self.assertEqual(
            second.relative_to(self.root).as_posix(),
            ".agents/skills/workbench-x/SKILL.md",
        )
        (self.root / ".agents" / "skills").mkdir(parents=True)
        self.assertEqual(paths.inside(self.root, ".agents", "skills/a.md").name, "a.md")

    def test_inside_rejects_bad_segments(self):
        for bad in (
            "..",
            "a/../b",
            "",
            "con",
            "NUL.txt",
            "lpt1.tar.gz",
            "name.",
            "a:b",
            "a\\b",
            "a b",
            "/etc/passwd",
            "a//b",
            "x" * 101,
            None,
        ):
            with self.assertRaises(ValueError, msg=repr(bad)) as caught:
                paths.inside(self.root, bad)
            self.assertEqual(str(caught.exception), paths.BAD_NAME)
        with self.assertRaises(ValueError):
            paths.inside(self.root)

    @unittest.skipIf(os.name == "nt", "symlinks need extra rights on Windows")
    def test_inside_rejects_links(self):
        outside = self.root / "outside"
        project = self.root / "project"
        outside.mkdir()
        project.mkdir()
        (project / ".claude").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, r"^\.claude goes through a link"):
            paths.inside(project, ".claude/skills/x/SKILL.md")
        (project / "AGENTS.md").symlink_to(outside / "gone.md")
        with self.assertRaisesRegex(ValueError, "AGENTS.md goes through a link"):
            paths.inside(project, "AGENTS.md")

    def test_inside_rejects_junctions(self):
        with patch.object(Path, "is_junction", return_value=True):
            with self.assertRaisesRegex(ValueError, "goes through a link"):
                paths.inside(self.root, "skills")

    def test_inside_names_the_whole_path_when_a_folder_cannot_be_examined(self):
        rel = ".agents/skills/x/SKILL.md"
        sentence = f"{rel} cannot be reached. Check the permissions of its folder, then try again."
        real = os.lstat

        def lstat(path, *args, **kwargs):
            if Path(path).name == "skills":
                raise PermissionError(13, "Permission denied")
            return real(path, *args, **kwargs)

        (self.root / ".agents").mkdir()
        with patch.object(paths.os, "lstat", side_effect=lstat):
            with self.assertRaises(ValueError) as caught:
                paths.inside(self.root, rel)
            self.assertEqual(str(caught.exception), sentence)
            self.assertEqual(paths.inside(self.root, ".agents"), self.root / ".agents")
        with patch.object(paths.os, "lstat", side_effect=ValueError("embedded null byte")):
            with self.assertRaises(ValueError) as caught:
                paths.inside(self.root, rel)
            self.assertEqual(str(caught.exception), sentence)

    @unittest.skipIf(
        os.name == "nt" or getattr(os, "geteuid", lambda: 1)() == 0,
        "needs a folder this user cannot enter",
    )
    def test_inside_reports_a_folder_that_cannot_be_entered(self):
        (self.root / ".agents" / "skills").mkdir(parents=True)
        os.chmod(self.root / ".agents", 0)
        self.addCleanup(os.chmod, self.root / ".agents", 0o755)
        with self.assertRaisesRegex(ValueError, r"^\.agents/skills/a\.md cannot be reached\."):
            paths.inside(self.root, ".agents/skills/a.md")
        self.assertEqual(paths.inside(self.root, ".agents"), self.root / ".agents")

    def test_inside_rejects_file_as_folder(self):
        (self.root / ".agents").write_text("x")
        with self.assertRaisesRegex(ValueError, r"^\.agents is a file"):
            paths.inside(self.root, ".agents/skills")
        self.assertEqual(paths.inside(self.root, ".agents"), self.root / ".agents")


if __name__ == "__main__":
    unittest.main()
