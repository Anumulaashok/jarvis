"""
GitHub integration — uses gh CLI when available, falls back to REST API.
"""
import json
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Optional

from integrations.manager import get_credential, is_configured

SERVICE = "github"


def is_ready() -> bool:
    if is_configured(SERVICE):
        return True
    return shutil.which("gh") is not None


def _token() -> Optional[str]:
    return get_credential(SERVICE, "github_token")


def _api(method: str, path: str, body: dict | None = None) -> dict:
    token = _token()
    if not token:
        raise RuntimeError("GitHub not configured. Set github_token via integration_setup.")

    url = f"https://api.github.com{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept":        "application/vnd.github+json",
            "Content-Type":  "application/json",
            "User-Agent":    "captain-jack",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else {}


def _run_gh(args: list[str], cwd: Path) -> tuple[bool, str]:
    gh = shutil.which("gh")
    if not gh:
        return False, "gh CLI not installed"
    token = _token()
    env = None
    if token:
        import os
        env = {**os.environ, "GH_TOKEN": token}
    r = subprocess.run(
        [gh, *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    out = (r.stdout + r.stderr).strip()
    return r.returncode == 0, out


def git_pull(repo: Path) -> str:
    r = subprocess.run(
        ["git", "pull", "--ff-only"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        return f"git pull failed: {out}"
    return out or "Already up to date."


def git_status(repo: Path) -> str:
    r = subprocess.run(
        ["git", "status", "--short", "--branch"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=30,
    )
    return (r.stdout + r.stderr).strip()


def git_push(repo: Path, branch: str | None = None) -> str:
    args = ["git", "push"]
    if branch:
        args += ["origin", branch]
    r = subprocess.run(args, cwd=str(repo), capture_output=True, text=True, timeout=120)
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        return f"git push failed: {out}"
    return out or "Pushed successfully."


def create_pull_request(
    repo: Path,
    title: str,
    body: str = "",
    base: str = "main",
    head: str | None = None,
) -> str:
    ok, out = _run_gh(
        ["pr", "create", "--title", title, "--body", body, "--base", base]
        + (["--head", head] if head else []),
        repo,
    )
    if ok:
        return out

    # REST fallback — need owner/repo from remote
    remote = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=str(repo), capture_output=True, text=True, timeout=10,
    )
    if remote.returncode != 0:
        return f"PR creation failed: {out}"

    url = remote.stdout.strip()
    # https://github.com/owner/repo.git or git@github.com:owner/repo.git
    if "github.com" in url:
        parts = url.replace(".git", "").rstrip("/").split("/")
        owner, repo_name = parts[-2], parts[-1]
        if ":" in owner:
            owner, repo_name = owner.split(":")[-1], repo_name
    else:
        return f"PR creation failed: {out}"

    current_branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(repo), capture_output=True, text=True, timeout=10,
    )
    branch = head or current_branch.stdout.strip()

    try:
        result = _api("POST", f"/repos/{owner}/{repo_name}/pulls", {
            "title": title,
            "body":  body,
            "head":  branch,
            "base":  base,
        })
        return f"PR created: {result.get('html_url', result)}"
    except Exception as e:
        return f"PR creation failed: {e}"
