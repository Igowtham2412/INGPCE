import subprocess
import unittest
from unittest.mock import call, patch

from app.git_operations import (
    GitOperationError,
    _remote_display,
    commit_all,
    push_current_branch,
    set_commit_identity,
    status,
)


def git_result(stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["git"], returncode=0, stdout=stdout, stderr="",
    )


class GitOperationsTests(unittest.TestCase):
    def setUp(self):
        self.connection_patch = patch(
            "app.git_operations._connection_details",
            return_value={
                "commit_name": "INGPCE",
                "commit_email": "ops@example.com",
                "origin_repository": "github.com/owner/repo",
                "credential_helper": "manager",
                "github_cli_account": None,
            },
        )
        self.connection_patch.start()
        self.addCleanup(self.connection_patch.stop)

    def test_commit_stages_all_changes_and_uses_fixed_git_arguments(self):
        with patch(
            "app.git_operations._run_git",
            side_effect=[
                git_result("feature/settings\n"),
                git_result("## feature/settings\n M README.md\n?? new-file.py\n"),
                git_result(),
                git_result("[feature/settings abc123] Add settings\n"),
            ],
        ) as run_git:
            result = commit_all("Add settings")

        self.assertEqual(result["branch"], "feature/settings")
        self.assertEqual(
            run_git.call_args_list,
            [
                call("branch", "--show-current"),
                call("status", "--short", "--branch", "--untracked-files=all"),
                call("add", "--all"),
                call("commit", "-m", "Add settings"),
            ],
        )

    def test_push_is_refused_when_the_working_tree_is_dirty(self):
        with patch(
            "app.git_operations._run_git",
            side_effect=[
                git_result("feature/settings\n"),
                git_result("## feature/settings\n M README.md\n"),
            ],
        ) as run_git, self.assertRaisesRegex(GitOperationError, "working-tree changes"):
            push_current_branch()

        self.assertEqual(run_git.call_count, 2)

    def test_push_targets_only_origin_head(self):
        with patch(
            "app.git_operations._run_git",
            side_effect=[
                git_result("feature/settings\n"),
                git_result("## feature/settings\n"),
                git_result(""),
            ],
        ) as run_git:
            result = push_current_branch()

        self.assertIn("pushed to origin", result["result"])
        self.assertEqual(run_git.call_args, call("push", "origin", "HEAD"))

    def test_remote_display_hides_embedded_credentials(self):
        self.assertEqual(
            _remote_display("https://user:token@github.com/owner/repo.git"),
            "github.com/owner/repo",
        )
        self.assertEqual(
            _remote_display("git@github.com:owner/repo.git"),
            "github.com/owner/repo",
        )

    def test_status_distinguishes_remote_and_commit_identity_from_cli_account(self):
        with patch(
            "app.git_operations._run_git",
            side_effect=[
                git_result("feature/settings\n"),
                git_result("## feature/settings\n"),
            ],
        ):
            result = status()

        self.assertEqual(
            result["connection"]["origin_repository"],
            "github.com/owner/repo",
        )
        self.assertEqual(result["connection"]["commit_name"], "INGPCE")
        self.assertIsNone(result["connection"]["github_cli_account"])

    def test_commit_identity_updates_repository_local_git_config_only(self):
        with (
            patch("app.git_operations._run_git") as run_git,
            patch(
                "app.git_operations._connection_details",
                return_value={
                    "commit_name": "Safety Operator",
                    "commit_email": "operator@example.com",
                },
            ),
        ):
            result = set_commit_identity("Safety Operator", "operator@example.com")

        self.assertEqual(result["commit_name"], "Safety Operator")
        self.assertEqual(
            run_git.call_args_list,
            [
                call("config", "--local", "user.name", "Safety Operator"),
                call("config", "--local", "user.email", "operator@example.com"),
            ],
        )

    def test_commit_identity_rejects_invalid_email(self):
        with (
            patch("app.git_operations._run_git") as run_git,
            self.assertRaisesRegex(GitOperationError, "valid commit author email"),
        ):
            set_commit_identity("Safety Operator", "not-an-email")

        run_git.assert_not_called()


if __name__ == "__main__":
    unittest.main()
