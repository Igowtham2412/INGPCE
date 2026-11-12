"""Narrow Git status, commit, and push operations for the admin dashboard."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

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


def _optional_git_value(*arguments: str) -> str | None:
    try:
        value = _run_git(*arguments).stdout.strip()
    except GitOperationError:
        return None
    return value or None


def _remote_display(remote: str | None) -> str | None:
    if not remote:
        return None
    parsed = urlsplit(remote)
    if parsed.scheme and parsed.hostname:
        host, path = parsed.hostname, parsed.path
    else:
        match = re.fullmatch(r"(?:[^@]+@)?([^:]+):(.+)", remote)
        if not match:
            return None
        host, path = match.groups()
    normalized_path = path.strip("/").removesuffix(".git")
    return f"{host}/{normalized_path}" if normalized_path else host


def _github_cli_account() -> str | None:
    gh = shutil.which("gh")
    if not gh:
        return None
    try:
        result = subprocess.run(
            [gh, "auth", "status", "--hostname", "github.com"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = f"{result.stdout}\n{result.stderr}"
    match = re.search(
        r"Logged in to github\.com account ([A-Za-z0-9-]+)",
        output,
        flags=re.IGNORECASE,
    )
    return match.group(1) if result.returncode == 0 and match else None


def _connection_details() -> dict[str, str | None]:
    name = _optional_git_value("config", "--get", "user.name")
    email = _optional_git_value("config", "--get", "user.email")
    return {
        "commit_name": name,
        "commit_email": email,
        "origin_repository": _remote_display(
            _optional_git_value("remote", "get-url", "origin"),
        ),
        "credential_helper": _optional_git_value(
            "config", "--get", "credential.helper",
        ),
        "github_cli_account": _github_cli_account(),
    }


def set_commit_identity(name: str, email: str) -> dict[str, str | None]:
    name = name.strip() if isinstance(name, str) else ""
    email = email.strip() if isinstance(email, str) else ""
    if not name or len(name) > 100 or any(ord(char) < 32 for char in name):
        raise GitOperationError("Commit author name must be 1-100 printable characters")
    if (
        len(email) > 254
        or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
        or any(ord(char) < 32 for char in email)
    ):
        raise GitOperationError("Enter a valid commit author email address")

    _run_git("config", "--local", "user.name", name)
    _run_git("config", "--local", "user.email", email)
    return _connection_details()


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
        "connection": _connection_details(),
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
