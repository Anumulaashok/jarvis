"""
claude_dev action — voice/text entry point for the Claude Dev Agent.
"""
from typing import Callable

from agents.claude_dev.claude_dev_agent import get_claude_dev


def claude_dev(
    parameters: dict,
    player=None,
    speak: Callable | None = None,
) -> str:
    action = parameters.get("action", "run")

    agent = get_claude_dev()
    if speak:
        agent.set_speak(speak)

    if action == "status":
        return agent.get_status(parameters.get("repo_path", ""))

    if action == "run":
        task = parameters.get("task", "").strip()
        if not task:
            return "No task provided, sir. Tell me what Claude should do."

        repo = parameters.get("repo_path", "").strip()
        if not repo:
            return "No repo path provided, sir. Tell me which folder or repository to work in."

        return agent.run_task(
            task=task,
            repo_path=repo,
            branch=parameters.get("branch", ""),
            create_pr=bool(parameters.get("create_pr", False)),
            pr_title=parameters.get("pr_title", ""),
            slack_channel=parameters.get("slack_channel", ""),
            open_ide=parameters.get("open_ide", True),
            speak=speak,
        )

    return f"Unknown claude_dev action: {action}. Use run or status."
