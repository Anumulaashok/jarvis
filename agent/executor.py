import json
import re
import sys
import time
import threading
import subprocess
import tempfile
import os
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path
from typing import Callable

from agent.planner       import create_plan, replan
from agent.error_handler import analyze_error, generate_fix, ErrorDecision
from memory.memory_manager import load_memory, format_memory_for_prompt


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]

def _run_generated_code(description: str, speak: Callable | None = None) -> str:
    import google.generativeai as genai

    if speak:
        speak("Writing custom code for this task, sir.")

    home      = Path.home()
    desktop   = home / "Desktop"
    downloads = home / "Downloads"
    documents = home / "Documents"

    if not desktop.exists():
        try:
            import winreg
            key     = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders")
            desktop = Path(winreg.QueryValueEx(key, "Desktop")[0])
        except Exception:
            pass

    genai.configure(api_key=_get_api_key())
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction=(
            "You are an expert Python developer. "
            "Write clean, complete, working Python code. "
            "Use standard library + common packages. "
            "Install missing packages with subprocess + pip if needed. "
            "Return ONLY the Python code. No explanation, no markdown, no backticks.\n\n"
            f"SYSTEM PATHS:\n"
            f"  Desktop   = r'{desktop}'\n"
            f"  Downloads = r'{downloads}'\n"
            f"  Documents = r'{documents}'\n"
            f"  Home      = r'{home}'\n"
        )
    )

    try:
        response = model.generate_content(
            f"Write Python code to accomplish this task:\n\n{description}"
        )
        code = response.text.strip()
        code = re.sub(r"```(?:python)?", "", code).strip().rstrip("`").strip()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            tmp_path = f.name

        print(f"[Executor] 🐍 Running generated code: {tmp_path}")

        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True, text=True,
            timeout=120, cwd=str(Path.home())
        )

        try:
            os.unlink(tmp_path)
        except Exception:
            pass

        output = result.stdout.strip()
        error  = result.stderr.strip()

        if result.returncode == 0 and output:
            return output
        elif result.returncode == 0:
            return "Task completed successfully."
        elif error:
            raise RuntimeError(f"Code error: {error[:400]}")
        return "Completed."

    except subprocess.TimeoutExpired:
        raise RuntimeError("Generated code timed out after 120 seconds.")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Generated code failed: {e}")

def _inject_context(params: dict, tool: str, step_results: dict, goal: str = "") -> dict:
    if not step_results:
        return params

    params = dict(params)

    if tool == "file_controller" and params.get("action") in ("write", "create_file"):
        content = params.get("content", "")
        if not content or len(content) < 50:
            all_results = [
                v for v in step_results.values()
                if v and len(v) > 100 and v not in ("Done.", "Completed.")
            ]
            if all_results:
                combined = "\n\n---\n\n".join(all_results)
                translated = _translate_to_goal_language(combined, goal)
                params["content"] = translated
                print(f"[Executor] 💉 Injected + translated content")

    return params
def _detect_language(text: str) -> str:
    import google.generativeai as genai
    genai.configure(api_key=_get_api_key())
    model = genai.GenerativeModel("gemini-2.5-flash-lite")
    try:
        response = model.generate_content(
            f"What language is this text written in? "
            f"Reply with ONLY the language name in English (e.g. Turkish, English, French).\n\n"
            f"Text: {text[:200]}"
        )
        return response.text.strip()
    except Exception:
        return "English"


def _translate_to_goal_language(content: str, goal: str) -> str:
    if not goal:
        return content
    try:
        import google.generativeai as genai
        genai.configure(api_key=_get_api_key())
        model = genai.GenerativeModel("gemini-2.5-flash")

        target_lang = _detect_language(goal)
        print(f"[Executor] 🌐 Translating to: {target_lang}")

        prompt = (
            f"You are a professional translator. "
            f"Translate the following text into {target_lang}.\n"
            f"IMPORTANT:\n"
            f"- Translate EVERYTHING, leave nothing in English\n"
            f"- Keep all facts, numbers, and data intact\n"
            f"- Keep the structure and formatting\n"
            f"- Output ONLY the translated text, nothing else\n\n"
            f"Text to translate:\n{content[:4000]}"
        )
        response = model.generate_content(prompt)
        translated = response.text.strip()
        print(f"[Executor] ✅ Translation done ({target_lang})")
        return translated
    except Exception as e:
        print(f"[Executor] ⚠️ Translation failed: {e}")
        return content

def _call_tool(tool: str, parameters: dict, speak: Callable | None) -> str:
    from core.permission_manager import get_permission_manager
    pm = get_permission_manager()
    if pm.needs_permission(tool) and not pm.request(tool):
        raise PermissionError(f"User denied permission for '{tool}'.")

    if tool == "open_app":
        from actions.open_app import open_app
        return open_app(parameters=parameters, player=None) or "Done."

    elif tool == "web_search":
        from actions.web_search import web_search
        return web_search(parameters=parameters, player=None) or "Done."
    elif tool == "game_updater":
        from actions.game_updater import game_updater
        return game_updater(parameters=parameters, player=None, speak=speak) or "Done."
    elif tool == "browser_control":
        from actions.browser_control import browser_control
        return browser_control(parameters=parameters, player=None) or "Done."

    elif tool == "file_controller":
        from actions.file_controller import file_controller
        return file_controller(parameters=parameters, player=None) or "Done."

    elif tool == "code_helper":
        from actions.code_helper import code_helper
        return code_helper(parameters=parameters, player=None, speak=speak) or "Done."

    elif tool == "dev_agent":
        from actions.dev_agent import dev_agent
        return dev_agent(parameters=parameters, player=None, speak=speak) or "Done."

    elif tool == "screen_process":
        from actions.screen_processor import screen_process
        screen_process(parameters=parameters, player=None)
        return "Screen captured and analyzed."

    elif tool == "send_message":
        from actions.send_message import send_message
        return send_message(parameters=parameters, player=None) or "Done."

    elif tool == "reminder":
        from actions.reminder import reminder
        return reminder(parameters=parameters, player=None) or "Done."

    elif tool == "youtube_video":
        from actions.youtube_video import youtube_video
        return youtube_video(parameters=parameters, player=None) or "Done."

    elif tool == "weather_report":
        from actions.weather_report import weather_action
        return weather_action(parameters=parameters, player=None) or "Done."

    elif tool == "computer_settings":
        from actions.computer_settings import computer_settings
        return computer_settings(parameters=parameters, player=None) or "Done."

    elif tool == "desktop_control":
        from actions.desktop import desktop_control
        return desktop_control(parameters=parameters, player=None) or "Done."

    elif tool == "computer_control":
        from actions.computer_control import computer_control
        return computer_control(parameters=parameters, player=None) or "Done."

    elif tool == "generated_code":
        description = parameters.get("description", "")
        if not description:
            raise ValueError("generated_code requires a 'description' parameter.")
        return _run_generated_code(description, speak=speak)

    elif tool == "flight_finder":
        from actions.flight_finder import flight_finder
        return flight_finder(parameters=parameters, player=None, speak=speak) or "Done."

    elif tool == "email_reader":
        from actions.email_reader import email_reader
        return email_reader(parameters=parameters, player=None, speak=speak) or "Done."

    elif tool == "task_manager":
        from actions.task_manager import task_manager
        return task_manager(parameters=parameters, player=None) or "Done."

    elif tool == "system_control":
        from actions.system_control import system_control
        return system_control(parameters=parameters, player=None, speak=speak) or "Done."

    elif tool == "slack_reader":
        from actions.slack_reader import slack_reader
        return slack_reader(parameters=parameters, player=None, speak=speak) or "Done."

    elif tool == "claude_dev":
        from actions.claude_dev import claude_dev
        return claude_dev(parameters=parameters, player=None, speak=speak) or "Done."

    else:
        print(f"[Executor] ⚠️ Unknown tool '{tool}' — falling back to generated_code")
        return _run_generated_code(f"Accomplish this task: {parameters}", speak=speak)

class AgentExecutor:

    MAX_REPLAN_ATTEMPTS = 2
    MAX_PARALLEL_WORKERS = 4

    def execute(
        self,
        goal:        str,
        speak:       Callable | None        = None,
        cancel_flag: threading.Event | None = None,
    ) -> str:
        print(f"\n[Executor] 🎯 Goal: {goal}")

        memory  = load_memory()
        context = format_memory_for_prompt(memory)
        plan    = create_plan(goal, context=context)

        replan_attempts = 0
        completed_steps: list = []

        while True:
            steps = plan.get("steps", [])

            if not steps:
                msg = "I couldn't create a valid plan for this task, sir."
                if speak: speak(msg)
                return msg

            outcome = self._execute_parallel(steps, goal, speak, cancel_flag)

            if outcome["cancelled"]:
                if speak: speak("Task cancelled, sir.")
                return "Task cancelled."

            completed_steps = outcome["completed_steps"]

            if outcome["success"]:
                return self._summarize(goal, completed_steps, speak)

            failed_step  = outcome["failed_step"]
            failed_error = outcome["failed_error"]

            if replan_attempts >= self.MAX_REPLAN_ATTEMPTS:
                msg = f"Task failed after {replan_attempts} replan attempts, sir."
                if speak: speak(msg)
                return msg

            if speak: speak("Adjusting my approach, sir.")
            replan_attempts += 1
            plan = replan(goal, completed_steps, failed_step, failed_error)

    # ------------------------------------------------------------------ #

    def _execute_parallel(
        self,
        steps_list:  list,
        goal:        str,
        speak:       Callable | None,
        cancel_flag: threading.Event | None,
    ) -> dict:
        """
        Run the plan steps in parallel where dependencies allow.
        Steps with depends_on:[] start immediately together.
        A step with depends_on:[1,2] waits until steps 1 and 2 are done.
        """
        steps      = {s["step"]: s for s in steps_list}
        completed  = {}           # step_num -> result str
        completed_steps = []      # step dicts
        pending    = set(steps.keys())
        running    = {}           # future -> step_num
        results_lock = threading.Lock()

        failed_step  = None
        failed_error = ""

        with ThreadPoolExecutor(max_workers=self.MAX_PARALLEL_WORKERS,
                                thread_name_prefix="AgentStep") as pool:
            while pending or running:

                if cancel_flag and cancel_flag.is_set():
                    for f in running:
                        f.cancel()
                    return {"success": False, "cancelled": True,
                            "completed_steps": completed_steps,
                            "failed_step": None, "failed_error": ""}

                # Submit every pending step whose dependencies are satisfied
                newly_submitted = set()
                for step_num in sorted(pending):
                    step = steps[step_num]
                    deps = step.get("depends_on", [])
                    if all(d in completed for d in deps):
                        params = _inject_context(
                            step["parameters"], step["tool"], completed, goal
                        )
                        step_copy = {**step, "parameters": params}
                        future = pool.submit(
                            self._run_step, step_copy, speak, cancel_flag
                        )
                        running[future] = step_num
                        newly_submitted.add(step_num)
                        print(f"[Executor] 🚀 Step {step_num} [{step['tool']}] started"
                              + (f" (needs {deps})" if deps else " (parallel)"))
                pending -= newly_submitted

                if not running:
                    # Deadlock: remaining steps have unresolvable deps
                    if pending:
                        print(f"[Executor] ⚠️ Steps {pending} have unresolvable dependencies — skipping")
                    break

                # Wait for at least one step to finish before re-checking
                done_futures, _ = wait(list(running.keys()), return_when=FIRST_COMPLETED)

                for f in done_futures:
                    step_num = running.pop(f)
                    step     = steps[step_num]
                    try:
                        result = f.result()
                        with results_lock:
                            completed[step_num] = result
                            completed_steps.append(step)
                        print(f"[Executor] ✅ Step {step_num} done: {str(result)[:120]}")

                    except Exception as e:
                        error_msg = str(e)
                        print(f"[Executor] ❌ Step {step_num} failed: {error_msg}")

                        if step.get("critical", True):
                            failed_step  = step
                            failed_error = error_msg
                            # Cancel siblings still running
                            for rf in list(running):
                                rf.cancel()
                            running.clear()
                            pending.clear()
                            return {
                                "success": False, "cancelled": False,
                                "completed_steps": completed_steps,
                                "failed_step": failed_step,
                                "failed_error": failed_error,
                            }
                        else:
                            # Non-critical: mark as skipped so dependents can proceed
                            with results_lock:
                                completed[step_num] = "(skipped — non-critical failure)"
                                completed_steps.append(step)
                            print(f"[Executor] ⏭️ Step {step_num} skipped (non-critical)")

        return {
            "success": True, "cancelled": False,
            "completed_steps": completed_steps,
            "failed_step": None, "failed_error": "",
        }

    def _run_step(
        self,
        step:        dict,
        speak:       Callable | None,
        cancel_flag: threading.Event | None,
    ) -> str:
        """Run a single step with up to 3 attempts and smart error recovery."""
        tool     = step["tool"]
        params   = step["parameters"]
        step_num = step["step"]

        for attempt in range(1, 4):
            if cancel_flag and cancel_flag.is_set():
                raise RuntimeError("Cancelled")
            try:
                result = _call_tool(tool, params, speak)
                return result or "Done."

            except Exception as e:
                error_msg = str(e)
                print(f"[Executor] ⚠️  Step {step_num} attempt {attempt} failed: {error_msg[:120]}")

                recovery = analyze_error(step, error_msg, attempt=attempt)
                decision = recovery["decision"]
                user_msg = recovery.get("user_message", "")

                if speak and user_msg:
                    speak(user_msg)

                if decision == ErrorDecision.RETRY and attempt < 3:
                    time.sleep(2)
                    continue

                if decision == ErrorDecision.SKIP:
                    return "(skipped)"

                if decision == ErrorDecision.ABORT:
                    raise RuntimeError(recovery.get("reason", error_msg))

                # REPLAN or exhausted retries — try a generated fix once
                fix_suggestion = recovery.get("fix_suggestion", "")
                if fix_suggestion and tool != "generated_code":
                    try:
                        fixed_step = generate_fix(step, error_msg, fix_suggestion)
                        if speak: speak("Trying an alternative approach, sir.")
                        return _call_tool(fixed_step["tool"], fixed_step["parameters"], speak) or "Done."
                    except Exception as fix_err:
                        print(f"[Executor] ⚠️ Fix attempt failed: {fix_err}")

                raise RuntimeError(error_msg)

        raise RuntimeError("Max retries exceeded")

    # ------------------------------------------------------------------ #

    def _summarize(self, goal: str, completed_steps: list, speak: Callable | None) -> str:
        fallback = f"All done, sir. Completed {len(completed_steps)} steps for: {goal[:60]}."
        try:
            import google.generativeai as genai
            genai.configure(api_key=_get_api_key())
            model     = genai.GenerativeModel(model_name="gemini-2.5-flash-lite")
            steps_str = "\n".join(f"- {s.get('description', '')}" for s in completed_steps)
            prompt    = (
                f'User goal: "{goal}"\n'
                f"Completed steps:\n{steps_str}\n\n"
                "Write a single natural sentence summarizing what was accomplished. "
                "Address the user as 'sir'. Be direct and positive."
            )
            response = model.generate_content(prompt)
            summary  = response.text.strip()
            if speak: speak(summary)
            return summary
        except Exception:
            if speak: speak(fallback)
            return fallback