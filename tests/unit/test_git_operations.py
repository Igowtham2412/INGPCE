import subprocess
import unittest
from unittest.mock import call, patch

from app.git_operations import GitOperationError, commit_all, push_current_branch


def git_result(stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["git"], returncode=0, stdout=stdout, stderr="",
    )


class GitOperationsTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
