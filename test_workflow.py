"""Developer workflow tests use disposable directories, never the user's venv/hooks."""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.validate_commit_msg import main, valid_message

ROOT = Path(__file__).resolve().parent


class CommitMessageTests(unittest.TestCase):
    def test_supported_subjects(self):
        for kind in "build chore ci docs feat fix perf refactor revert style test".split():
            for scope in ("", "()", "(monitor)", "(output.json_v2-3)"):
                for breaking in ("", "!"):
                    with self.subTest(kind=kind, scope=scope, breaking=breaking):
                        self.assertTrue(valid_message(f"{kind}{scope}{breaking}: add output"))
        self.assertTrue(valid_message("# template\n\nfeat(): add output\n\nBody text\n"))

    def test_invalid_or_empty_subjects(self):
        for raw in (
            "",
            "\n# comment\n",
            "feat:",
            "feat: ",
            "feat:    ",
            "feat:  extra space",
            " feat: leading space",
            "feat: trailing space ",
            "feat: \tblank",
            "feat:no space",
            "unknown: output",
            "FEAT: output",
            "feat(bad scope): output",
            "feat(scope: output",
            "feat()!!: output",
            "Merge branch x",
            "Release 1.0",
            "BREAKING CHANGE: output",
            "Revert previous commit",
        ):
            with self.subTest(raw=raw):
                self.assertFalse(valid_message(raw))

    def test_cli_valid_invalid_missing_and_unreadable(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stderr(io.StringIO()):
            path = Path(directory) / "message"
            path.write_text("feat(): output\n", encoding="utf-8")
            self.assertEqual(main([str(path)]), 0)
            path.write_text("bad message\n", encoding="utf-8")
            self.assertEqual(main([str(path)]), 1)
            path.write_bytes(b"\xff")
            self.assertEqual(main([str(path)]), 1)
            self.assertEqual(main([directory]), 1)
            self.assertEqual(main([str(path.with_name("missing"))]), 1)
            self.assertEqual(main([]), 1)
            self.assertEqual(main([str(path), "extra"]), 1)


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copyfile(ROOT / "Makefile", self.root / "Makefile")
        self.tools = self.root / "tools"
        self.tools.mkdir()
        # Fake interpreter records operations; a sentinel represents a mismatch.
        self.fake_python = f"""#!{sys.executable}
import json
import sys
from pathlib import Path
root = Path.cwd()
with (root / 'calls').open('a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[1] == '-c' and (root / 'mismatch').exists():
    sys.exit(1)
"""
        self.fake_uv = self.tools / "uv"
        self.fake_uv.write_text(
            f"#!{sys.executable}\n"
            "import json, sys\nfrom pathlib import Path\n"
            "root = Path.cwd()\n"
            "(root / 'uv-call').write_text(json.dumps(sys.argv[1:]))\n"
            "(root / 'venv/bin').mkdir(parents=True)\n"
            f"(root / 'venv/bin/python').write_text({self.fake_python!r})\n"
            "(root / 'venv/bin/python').chmod(0o755)\n"
        )
        self.fake_uv.chmod(0o755)

    def environment(self):
        return {**os.environ, "PATH": f"{self.tools}{os.pathsep}{os.environ.get('PATH', '')}"}

    def make(self, target):
        return subprocess.run(
            ["make", target],
            cwd=self.root,
            env=self.environment(),
            text=True,
            capture_output=True,
            check=False,
        )

    def existing(self):
        path = self.root / "venv/bin/python"
        path.parent.mkdir(parents=True)
        path.write_text(self.fake_python)
        path.chmod(0o755)
        return path

    def calls(self):
        return [json.loads(line) for line in (self.root / "calls").read_text().splitlines()]

    def assert_installs(self):
        calls = self.calls()
        self.assertIn(["-m", "pip", "install", "-r", "requirements.txt"], calls)
        self.assertIn(
            [
                "-m",
                "pre_commit",
                "install",
                "--hook-type",
                "pre-commit",
                "--hook-type",
                "commit-msg",
            ],
            calls,
        )

    def test_absent_environment_uses_exact_seeded_uv_command(self):
        result = self.make("setup")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            json.loads((self.root / "uv-call").read_text()),
            ["venv", "venv", "--python", "3.14", "--seed"],
        )
        self.assert_installs()

    def test_matching_environment_reused_without_uv(self):
        path = self.existing()
        before = path.read_bytes()
        result = self.make("setup")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / "uv-call").exists())
        self.assertEqual(path.read_bytes(), before)
        self.assert_installs()

    def test_mismatch_rejected_before_installs_or_recreation(self):
        path = self.existing()
        (self.root / "mismatch").touch()
        before = path.read_bytes()
        result = self.make("setup")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("existing venv left unchanged", result.stderr)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(self.calls()[0][0], "-c")
        self.assertFalse((self.root / "uv-call").exists())

    def test_incomplete_and_dangling_environment_rejected(self):
        for dangling in (False, True):
            path = self.root / "venv"
            if dangling:
                path.rmdir()
                path.symlink_to(self.root / "missing", target_is_directory=True)
            else:
                path.mkdir()
            result = self.make("setup")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("existing venv left unchanged", result.stderr)
            self.assertFalse((self.root / "uv-call").exists())

    def test_install_hooks_independent_of_python_version(self):
        self.existing()
        (self.root / "mismatch").touch()
        result = self.make("install-hooks")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(all(call[:2] == ["-m", "pre_commit"] for call in self.calls()))
        self.assertFalse((self.root / "uv-call").exists())

    def test_missing_tools_fail_visibly(self):
        result = self.make("install-hooks")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Install the pinned developer tools", result.stderr)


if __name__ == "__main__":
    unittest.main()
