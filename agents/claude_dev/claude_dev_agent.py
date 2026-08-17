"""
Claude Dev Agent — terminal-based developer agent powered by Claude Code CLI.

Workflow:
  1. Open IntelliJ IDEA with the repo
  2. Open Terminal in the repo directory
  3. git pull latest code
  4. Run Claude Code CLI with the task
  5. Send progress updates via speak() and Slack
  6. Optionally push code and raise a PR via GitHub
"""
import platform
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional


_SYSTEM = platform.system()
_CLAUDE_TIMEOUT = 1800  # 30 min max per task


class ClaudeDevAgent:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init()
        return cls._instance

    def _init(self):
        self._speak: Optional[Callable] = None
        self._active: dict[str, dict] = {}
        self._alock = threading.Lock()

    def set_speak(self, fn: Callable) -> None:
        self._speak = fn

    # ── Public API ────────────────────────────────────────────────────────────

    def run_task(
        self,
        task:          str,
        repo_path:     str,
        branch:        str = "",
        create_pr:     bool = False,
        pr_title:      str = "",
        slack_channel: str = "",
        open_ide:      bool = True,
        speak:         Callable | None = None,
    ) -> str:
        """Start a dev task. Runs in background; returns immediately."""
        repo = Path(repo_path).expanduser().resolve()
        if not repo.exists():
            return f"Repo path not found: {repo}"

        if not (repo / ".git").exists():
            return f"Not a git repository: {repo}"

        task_id = f"{repo.name}_{int(time.time())}"
        notify = speak or self._speak

        with self._alock:
            self._active[task_id] = {"status": "starting", "repo": str(repo), "task": task}

        t = threading.Thread(
            target=self._execute,
            args=(task_id, task, repo, branch, create_pr, pr_title, slack_channel, open_ide, notify),
            daemon=True,
        )
        t.start()

        return (
            f"Dev agent started on {repo.name}, sir. "
            f"I'll update you as Claude works through the task."
        )

    def get_status(self, repo_path: str = "") -> str:
        with self._alock:
            if not self._active:
                return "No dev tasks running, sir."
            if repo_path:
                matches = {k: v for k, v in self._active.items()
                             if repo_path in v.get("repo", "")}
                if not matches:
                    return f"No active task for {repo_path}, sir."
                items = matches
            else:
                items = self._active

        lines = []
        for tid, info in items.items():
            lines.append(f"{Path(info['repo']).name}: {info['status']}")
        return "Active dev tasks — " + "; ".join(lines) + "."

    # ── Internal ──────────────────────────────────────────────────────────────

    def _execute(
        self,
        task_id:       str,
        task:          str,
        repo:          Path,
        branch:        str,
        create_pr:     bool,
        pr_title:      str,
        slack_channel: str,
        open_ide:      bool,
        speak:         Callable | None,
    ):
        try:
            self._update(task_id, "setting up", speak, slack_channel)

            if open_ide:
                self._open_intellij(repo)
            self._open_terminal(repo)

            self._update(task_id, "pulling latest code", speak, slack_channel)
            from integrations.github import git_pull
            pull_result = git_pull(repo)
            self._notify(speak, slack_channel,
                         f"Sir, pulled latest code for {repo.name}. {pull_result[:120]}")

            if branch:
                self._checkout_branch(repo, branch)

            self._update(task_id, "running Claude", speak, slack_channel)
            self._notify(speak, slack_channel,
                         f"Sir, Claude is working on: {task[:200]}")

            claude_out = self._run_claude(task, repo)
            self._update(task_id, "finishing up", speak, slack_channel)

            summary = self._summarise(claude_out)
            self._notify(speak, slack_channel,
                         f"Sir, Claude finished on {repo.name}. {summary[:300]}")

            if create_pr:
                from integrations.github import create_pull_request, git_push
                git_push(repo, branch or None)
                title = pr_title or f"feat: {task[:60]}"
                pr_result = create_pull_request(repo, title=title, body=summary[:2000])
                self._notify(speak, slack_channel, f"Sir, {pr_result}")

            self._update(task_id, "done", speak, slack_channel,
                         result=summary)

        except Exception as e:
            self._update(task_id, f"failed: {e}", speak, slack_channel)
            self._notify(speak, slack_channel,
                         f"Sir, the dev task on {repo.name} failed: {e}")

    def _update(self, task_id: str, status: str, speak, slack_channel: str,
                result: str = ""):
        with self._alock:
            if task_id in self._active:
                self._active[task_id]["status"] = status
                if result:
                    self._active[task_id]["result"] = result

    def _notify(self, speak, slack_channel: str, message: str):
        if speak:
            try:
                speak(message)
            except Exception:
                pass
        if slack_channel:
            try:
                from integrations.slack_api import send_message, is_ready
                if is_ready():
                    send_message(slack_channel, f"🤖 Captain Jack Dev Agent\n{message}")
            except Exception as e:
                print(f"[ClaudeDev] Slack notify failed: {e}")

    def _open_intellij(self, repo: Path):
        if _SYSTEM == "Darwin":
            for app in ("IntelliJ IDEA", "IntelliJ IDEA CE", "IntelliJ IDEA Ultimate"):
                r = subprocess.run(
                    ["open", "-a", app, str(repo)],
                    capture_output=True, timeout=15,
                )
                if r.returncode == 0:
                    return
            # idea CLI fallback
            idea = shutil.which("idea") or shutil.which("idea.sh")
            if idea:
                subprocess.run([idea, str(repo)], capture_output=True, timeout=15)
        elif _SYSTEM == "Windows":
            idea = shutil.which("idea64.exe") or shutil.which("idea.exe")
            if idea:
                subprocess.Popen([idea, str(repo)])
        else:
            idea = shutil.which("idea") or shutil.which("idea.sh")
            if idea:
                subprocess.Popen([idea, str(repo)])

    def _open_terminal(self, repo: Path):
        if _SYSTEM == "Darwin":
            script = (
                f'tell application "Terminal"\n'
                f'  do script "cd {repo} && clear && echo \\"Captain Jack Dev Agent — {repo.name}\\""\n'
                f'  activate\n'
                f'end tell'
            )
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=15)
        elif _SYSTEM == "Windows":
            subprocess.Popen(
                ["wt", "-d", str(repo)],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        else:
            for term in ("gnome-terminal", "konsole", "xfce4-terminal"):
                if shutil.which(term):
                    subprocess.Popen([term, "--working-directory", str(repo)])
                    return

    def _checkout_branch(self, repo: Path, branch: str):
        for cmd in (
            ["git", "checkout", branch],
            ["git", "checkout", "-b", branch],
        ):
            r = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                return

    def _find_claude(self) -> Optional[str]:
        for candidate in (
            shutil.which("claude"),
            str(Path.home() / ".claude" / "local" / "claude"),
            "/usr/local/bin/claude",
            "/opt/homebrew/bin/claude",
        ):
            if candidate and Path(candidate).exists():
                return candidate
        return shutil.which("claude")

    def _build_prompt(self, task: str, repo: Path) -> str:
        return (
            f"You are working in the repository at {repo}.\n\n"
            f"TASK:\n{task}\n\n"
            "You have access to:\n"
            "- Terminal / shell commands (git, npm, pip, docker, etc.)\n"
            "- GitHub (gh CLI or git) — commit, push, create PRs\n"
            "- Slack — send status updates if needed\n"
            "- Google Cloud Console / gcloud — fetch production logs to debug issues\n\n"
            "Instructions:\n"
            "1. Understand the codebase before making changes\n"
            "2. Make minimal, focused changes\n"
            "3. Run tests if they exist\n"
            "4. Commit with a clear message when done\n"
            "5. Summarise what you changed and why at the end\n"
        )

    def _run_claude(self, task: str, repo: Path) -> str:
        claude = self._find_claude()
        if not claude:
            raise RuntimeError(
                "Claude Code CLI not found. Install it: npm install -g @anthropic-ai/claude-code"
            )

        prompt = self._build_prompt(task, repo)
        cmd = [
            claude,
            "-p", prompt,
            "--output-format", "text",
        ]

        print(f"[ClaudeDev] Running: {claude} in {repo}")
        r = subprocess.run(
            cmd,
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=_CLAUDE_TIMEOUT,
        )
        output = (r.stdout + r.stderr).strip()
        if r.returncode != 0 and not output:
            raise RuntimeError(f"Claude exited with code {r.returncode}")
        return output

    def _summarise(self, output: str) -> str:
        if not output:
            return "Task completed with no output."
        # Return last meaningful chunk (Claude's summary is usually at the end)
        lines = [l for l in output.splitlines() if l.strip()]
        if len(lines) > 20:
            return "\n".join(lines[-20:])
        return output[:2000]


def get_claude_dev() -> ClaudeDevAgent:
    return ClaudeDevAgent()
