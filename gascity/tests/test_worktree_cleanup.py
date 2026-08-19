from __future__ import annotations

import pathlib
import subprocess
import tempfile
import tomllib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "assets" / "scripts" / "cleanup-worktree.sh"
FORMULA = ROOT / "formulas" / "thunderdome-land.formula.toml"
PROMPT = ROOT / "assets" / "workflows" / "thunderdome-land" / "cleanup.md"


class WorktreeCleanupScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.rig = pathlib.Path(self.tempdir.name) / "rig"
        self.rig.mkdir()
        self._git("init", "-b", "main")
        self._git("config", "user.email", "tests@example.com")
        self._git("config", "user.name", "Tests")
        (self.rig / "tracked.txt").write_text("base\n", encoding="utf-8")
        self._git("add", "tracked.txt")
        self._git("commit", "-m", "base")
        self.promoted_sha = self._git("rev-parse", "HEAD").stdout.strip()
        (self.rig / "worktrees").mkdir()

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.rig), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    def _worktree(self, name: str) -> pathlib.Path:
        path = self.rig / "worktrees" / name
        self._git("worktree", "add", "--detach", str(path), self.promoted_sha)
        return path

    def _cleanup(
        self, path: pathlib.Path, owner_id: str, *, check: bool = False
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(SCRIPT), str(self.rig), str(path), owner_id, self.promoted_sha],
            check=check,
            capture_output=True,
            text=True,
        )

    def test_removes_clean_owned_worktree_reachable_from_promoted_sha(self) -> None:
        worktree = self._worktree("sp-source")

        result = self._cleanup(worktree, "sp-source", check=True)

        self.assertFalse(worktree.exists())
        self.assertIn("removed", result.stdout)
        self.assertNotIn(str(worktree), self._git("worktree", "list", "--porcelain").stdout)

    def test_preserves_dirty_owned_worktree(self) -> None:
        worktree = self._worktree("sp-source")
        (worktree / "evidence.txt").write_text("preserve\n", encoding="utf-8")

        result = self._cleanup(worktree, "sp-source")

        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(worktree.exists())
        self.assertIn("dirty", result.stderr)

    def test_refuses_unrelated_worktree_name(self) -> None:
        worktree = self._worktree("unrelated-user-worktree")

        result = self._cleanup(worktree, "sp-source")

        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(worktree.exists())
        self.assertIn("owner", result.stderr)

    def test_refuses_head_not_reachable_from_promoted_sha(self) -> None:
        worktree = self._worktree("sp-source")
        (worktree / "tracked.txt").write_text("candidate-only\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(worktree), "commit", "-am", "candidate"],
            check=True,
            capture_output=True,
            text=True,
        )

        result = self._cleanup(worktree, "sp-source")

        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(worktree.exists())
        self.assertIn("not reachable", result.stderr)


class ThunderdomeCleanupContractTests(unittest.TestCase):
    def test_landing_formula_runs_post_settlement_cleanup(self) -> None:
        data = tomllib.loads(FORMULA.read_text(encoding="utf-8"))
        steps = {step["id"]: step for step in data["steps"]}

        self.assertEqual(steps["body"]["needs"], ["promote"])
        self.assertEqual(steps["body"]["metadata"]["gc.kind"], "scope")
        self.assertEqual(steps["promote"]["metadata"]["gc.scope_ref"], "body")
        self.assertEqual(steps["promote"]["metadata"]["gc.on_fail"], "abort_scope")
        self.assertEqual(steps["cleanup"]["needs"], ["body"])
        self.assertEqual(steps["cleanup"]["metadata"]["gc.kind"], "cleanup")
        self.assertEqual(steps["cleanup"]["metadata"]["gc.scope_role"], "teardown")

    def test_cleanup_prompt_is_fail_safe_and_ownership_bounded(self) -> None:
        text = PROMPT.read_text(encoding="utf-8")

        for required in (
            "gc.thunderdome.state=promoted",
            "verified release SHA",
            "candidate IDs",
            "source IDs",
            "repair bead IDs",
            "canonical summary and review paths",
            "worktree basename",
            "cleanup-worktree.sh",
            "dirty worktrees",
            "preserve",
            "gc.cleanup.blocked_paths",
            "Never use `--force`",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)


if __name__ == "__main__":
    unittest.main()
