"""Narrow Git status, commit, and push operations for the admin dashboard."""
from __future__ import annotations

import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
COMMAND_TIMEOUT_SECONDS = 120


class GitOperationError(RuntimeError):
    pass


def _run_git(*arguments: str) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitOperationError(f"Git command could not complete: {error}") from error
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise GitOperationError(detail or "Git command failed")
    return result


def status() -> dict[str, object]:
    branch = _run_git("branch", "--show-current").stdout.strip()
    if not branch:
        raise GitOperationError("Git is in detached HEAD state; switch to a branch first")
    output = _run_git("status", "--short", "--branch", "--untracked-files=all").stdout
    lines = output.splitlines()
    return {
        "branch": branch,
        "changes": [line for line in lines if line and not line.startswith("##")],
        "clean": len(lines) <= 1,
        "remote": "origin",
    }


def commit_all(message: str) -> dict[str, str]:
    if not isinstance(message, str) or not message.strip() or len(message) > 200:
        raise GitOperationError("Enter a commit message between 1 and 200 characters")
    current = status()
    if current["clean"]:
        raise GitOperationError("There are no changes to commit")
    _run_git("add", "--all")
    _run_git("commit", "-m", message.strip())
    return {
        "branch": str(current["branch"]),
        "message": message.strip(),
        "result": "Changes committed successfully.",
    }


def push_current_branch() -> dict[str, str]:
    current = status()
    if current["changes"]:
        raise GitOperationError(
            "Commit or discard the current working-tree changes before pushing"
        )
    _run_git("push", "origin", "HEAD")
    return {
        "branch": str(current["branch"]),
        "result": "Current branch pushed to origin successfully.",
    }
