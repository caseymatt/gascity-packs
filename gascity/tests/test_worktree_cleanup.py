from __future__ import annotations

import os
import pathlib
import subprocess
import tempfile
import tomllib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "assets" / "scripts" / "cleanup-worktree.sh"
FORMULA = ROOT / "formulas" / "thunderdome-land.formula.toml"
PROMPT = ROOT / "assets" / "workflows" / "thunderdome-land" / "cleanup.md"
ASSEMBLE_PROMPT = ROOT / "assets" / "workflows" / "thunderdome-land" / "assemble-land.md"
VERIFY_PROMPT = ROOT / "assets" / "workflows" / "thunderdome-land" / "verify-repair.md"
ITEM_PROMPT = (
    ROOT
    / "assets"
    / "workflows"
    / "thunderdome-work-item"
    / "prepare-worktree.md"
)
CANDIDATE_PROMPT = ROOT / "assets" / "workflows" / "thunderdome-build" / "integrate.md"


class WorktreeCleanupScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = pathlib.Path(self.tempdir.name)
        self.rig = self.root / "rig"
        (self.rig / "worktrees").mkdir(parents=True)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.calls = self.root / "gc-calls"
        self.promoted_sha = "a" * 40
        fake_gc = self.bin_dir / "gc"
        fake_gc.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"${GC_FAKE_CALLS:?}"

emit() {
  local reclaimed="$1"
  local reclaimable="$2"
  local dry_run="$3"
  local reason="$4"
  printf '{"id":"%s","owner":"%s","rig":"test","path":"%s","reclaimed":%s,"dry_run":%s,"reclaimable":%s,"reason":"%s","head_sha":"%s","published_ref":"%s","published_sha":"%s"}\n' \
    "${GC_FAKE_ID:?}" "${GC_FAKE_OWNER:?}" "${GC_FAKE_PATH:?}" \
    "$reclaimed" "$dry_run" "$reclaimable" "$reason" \
    "${GC_FAKE_HEAD:-}" "${GC_FAKE_PUBLISHED_REF:-}" "${GC_FAKE_PUBLISHED_SHA:-}"
}

if [[ "${1:-}" == "worktree" && "${2:-}" == "list" ]]; then
  if [[ "${GC_FAKE_MODE:?}" == "registry-failure" ]]; then
    printf '{"reason":"registry unavailable; preserved"}\n'
    exit 74
  fi
  printf '[{"id":"%s","owner":"%s","path":"%s"}]\n' \
    "${GC_FAKE_ID:?}" "${GC_FAKE_OWNER:?}" "${GC_FAKE_PATH:?}"
  exit 0
fi

if [[ "${1:-}" != "worktree" || "${2:-}" != "reclaim" ]]; then
  printf 'unexpected gc command\n' >&2
  exit 64
fi
[[ "${3:-}" == "${GC_FAKE_ID:?}" ]]

dry_run=false
for arg in "$@"; do
  if [[ "$arg" == "--dry-run" ]]; then
    dry_run=true
  fi
done
if [[ "$dry_run" == true ]]; then
  emit false true true "would reclaim"
  exit 0
fi

case "${GC_FAKE_MODE:?}" in
  published)
    rm -rf -- "${GC_FAKE_PATH:?}"
    emit true true false "published head verified"
    ;;
  ancestry)
    rm -rf -- "${GC_FAKE_PATH:?}"
    emit true true false "HEAD is ancestor of promoted SHA"
    ;;
  unpublished-nonancestor)
    emit false false false "unpublished HEAD is not an ancestor of promoted SHA"
    exit 1
    ;;
  dirty)
    emit false false false "dirty worktree preserved"
    exit 1
    ;;
  probe-failure)
    emit false false false "repository probe failed; preserved"
    exit 74
    ;;
  *)
    printf 'unexpected fake mode\n' >&2
    exit 64
    ;;
esac
""",
            encoding="utf-8",
        )
        fake_gc.chmod(0o755)

    def _worktree(self, lifecycle_id: str) -> pathlib.Path:
        path = self.rig / "worktrees" / lifecycle_id
        path.mkdir()
        (path / "evidence.txt").write_text("preserve unless reclaimed\n", encoding="utf-8")
        return path

    def _cleanup(
        self,
        path: pathlib.Path,
        owner_id: str,
        *,
        mode: str,
        registry_owner: str | None = None,
        dry_run: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "PATH": f"{self.bin_dir}:{os.environ['PATH']}",
            "GC_FAKE_CALLS": str(self.calls),
            "GC_FAKE_ID": path.name,
            "GC_FAKE_OWNER": registry_owner or owner_id,
            "GC_FAKE_PATH": str(path.resolve()),
            "GC_FAKE_MODE": mode,
            "GC_FAKE_HEAD": "b" * 40,
            "GC_FAKE_PUBLISHED_REF": "refs/gc/published/test",
            "GC_FAKE_PUBLISHED_SHA": "b" * 40 if mode == "published" else "",
        }
        if dry_run:
            env["GC_WORKTREE_CLEANUP_DRY_RUN"] = "1"
        return subprocess.run(
            [str(SCRIPT), str(self.rig), str(path), owner_id, self.promoted_sha],
            check=False,
            capture_output=True,
            env=env,
            text=True,
        )

    def test_reclaims_published_loser_without_promoted_ancestry(self) -> None:
        worktree = self._worktree("sp-loser")

        result = self._cleanup(worktree, "sp-loser", mode="published")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(worktree.exists())
        self.assertIn('"reclaimed":true', result.stdout)
        self.assertIn('"published_sha":"' + "b" * 40, result.stdout)

    def test_reclaims_unpublished_head_via_promoted_ancestry_fallback(self) -> None:
        worktree = self._worktree("sp-landed")

        result = self._cleanup(worktree, "sp-landed", mode="ancestry")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(worktree.exists())
        self.assertIn("ancestor of promoted SHA", result.stdout)

    def test_routes_repair_integration_name_and_epoch_owner_to_registry(self) -> None:
        worktree = self._worktree("repair-int-sp-epoch-r2")

        result = self._cleanup(worktree, "sp-epoch", mode="published")

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls.read_text(encoding="utf-8")
        self.assertIn(f"worktree list {worktree.resolve()} --json", calls)
        self.assertIn("worktree reclaim repair-int-sp-epoch-r2", calls)

    def test_preserves_unpublished_nonancestor_with_reason(self) -> None:
        worktree = self._worktree("sp-divergent")

        result = self._cleanup(worktree, "sp-divergent", mode="unpublished-nonancestor")

        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(worktree.exists())
        self.assertIn("unpublished HEAD is not an ancestor", result.stdout)

    def test_preserves_dirty_and_probe_failure_paths_with_reasons(self) -> None:
        for mode, reason in (
            ("dirty", "dirty worktree preserved"),
            ("probe-failure", "repository probe failed; preserved"),
        ):
            with self.subTest(mode=mode):
                worktree = self._worktree(f"sp-{mode}")
                result = self._cleanup(worktree, worktree.name, mode=mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(worktree.exists())
                self.assertIn(reason, result.stdout)

    def test_registry_failure_and_owner_mismatch_never_reclaim(self) -> None:
        registry_failure = self._worktree("sp-registry-failure")
        result = self._cleanup(
            registry_failure,
            registry_failure.name,
            mode="registry-failure",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(registry_failure.exists())
        self.assertIn("registry unavailable", result.stderr)

        owner_mismatch = self._worktree("sp-owner-mismatch")
        result = self._cleanup(
            owner_mismatch,
            owner_mismatch.name,
            mode="published",
            registry_owner="someone-else",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(owner_mismatch.exists())
        self.assertIn("owner mismatch", result.stderr)

    def test_environment_dry_run_reports_without_deleting(self) -> None:
        worktree = self._worktree("sp-dry-run")

        result = self._cleanup(worktree, worktree.name, mode="ancestry", dry_run=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(worktree.exists())
        self.assertIn('"reclaimed":false', result.stdout)
        self.assertIn('"dry_run":true', result.stdout)
        self.assertIn('"reclaimable":true', result.stdout)
        self.assertIn("--dry-run", self.calls.read_text(encoding="utf-8"))


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

    def test_cleanup_prompt_uses_bounded_registry_reclaim_contract(self) -> None:
        text = PROMPT.read_text(encoding="utf-8")

        for required in (
            "gc.thunderdome.state=promoted",
            "gc worktree list",
            "gc worktree reclaim",
            "Every typed candidate record",
            "gc.worktree.id",
            "gc.worktree.owner",
            "copied from its candidate workflow root at enqueue",
            "never reconstruct a candidate",
            "infer its workflow root",
            "Reject missing candidate lifecycle fields",
            "duplicate lifecycle IDs",
            "mismatch the immutable enqueue evidence",
            "thunderdome-epoch-<epoch-id>",
            "verify-<epoch-id>-r<N>",
            "repair-int-<epoch-id>-r<N>",
            "published losing source or repair head",
            "unpublished non-ancestor",
            "promoted-SHA ancestry",
            "GC_WORKTREE_CLEANUP_DRY_RUN",
            "reclaimed=false",
            "dry_run=true",
            "reclaimable=true",
            "a dirty worktree must be preserved",
            "exact reclaim reason must identify the dirty state",
            "gc.cleanup.blocked_paths",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)
        self.assertNotIn("git worktree remove", text)
        self.assertNotIn("git worktree prune", text)
        self.assertNotIn(
            "thunderdome-candidate-<candidate-workflow-root-id>",
            text,
        )

    def test_all_five_worktree_creation_flows_use_unique_registered_ids(self) -> None:
        item = ITEM_PROMPT.read_text(encoding="utf-8")
        candidate = CANDIDATE_PROMPT.read_text(encoding="utf-8")
        assemble = ASSEMBLE_PROMPT.read_text(encoding="utf-8")
        verify = VERIFY_PROMPT.read_text(encoding="utf-8")

        self.assertIn('gc worktree create "<source-anchor-id>"', item)
        self.assertIn("thunderdome-candidate-<workflow-root-id>", candidate)
        self.assertIn("gc worktree create", candidate)
        self.assertIn("thunderdome-epoch-<epoch-id>", assemble)
        self.assertIn("gc worktree create", assemble)
        self.assertIn("verify-<epoch-id>-r<N>", verify)
        self.assertIn("repair-int-<epoch-id>-r<N>", verify)
        self.assertIn(
            "$GC_RIG_ROOT/worktrees/repair-int-<epoch-id>-r<N>",
            verify,
        )

    def test_land_builds_use_registered_unique_target_and_publication(self) -> None:
        assemble = ASSEMBLE_PROMPT.read_text(encoding="utf-8")
        verify = VERIFY_PROMPT.read_text(encoding="utf-8")

        self.assertIn(
            "$GC_RIG_ROOT/worktrees/.cargo-targets/thunderdome-epoch-<epoch-id>/attempt-1",
            assemble,
        )
        self.assertIn(
            "$GC_RIG_ROOT/worktrees/.cargo-targets/verify-<epoch-id>-r<N>/attempt-1",
            verify,
        )
        self.assertIn(
            "$GC_RIG_ROOT/worktrees/.cargo-targets/repair-int-<epoch-id>-r<N>/attempt-1",
            verify,
        )
        self.assertIn('gc worktree publish "thunderdome-epoch-<epoch-id>"', assemble)
        self.assertIn('gc worktree publish "repair-int-<epoch-id>-r<N>"', verify)
        self.assertIn("published_sha", assemble)
        self.assertIn("published_sha", verify)
        self.assertIn("gc.worktree.path", assemble)
        self.assertIn("gc.worktree.path", verify)
        for text in (assemble, verify):
            self.assertNotIn("git worktree add", text)
            self.assertNotIn("git worktree remove", text)

    def test_compatibility_script_only_delegates_to_central_reclaim(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")

        self.assertIn('worktree list "$worktree" --json', text)
        self.assertIn('worktree reclaim "$registered_id"', text)
        self.assertIn("GC_WORKTREE_CLEANUP_DRY_RUN", text)
        self.assertNotIn("git worktree", text)
        self.assertNotIn("--force", text)
        self.assertNotIn("prune", text)


if __name__ == "__main__":
    unittest.main()
