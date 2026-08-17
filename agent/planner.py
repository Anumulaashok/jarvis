import json
import re
import sys
from pathlib import Path


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


PLANNER_PROMPT = """You are the planning module of MARK XXV, a personal AI assistant.
Your job: break any user goal into parallel-aware steps using ONLY the tools listed below.

ABSOLUTE RULES:
- NEVER use generated_code or write Python scripts. It does not exist.
- Use web_search for ANY information retrieval, research, or current data.
- Use file_controller to save content to disk.
- Max 6 steps. Use the minimum steps needed.

PARALLELISM — use depends_on to express execution order:
- depends_on: [] → this step starts immediately (runs in parallel with other ready steps)
- depends_on: [1, 2] → wait for steps 1 AND 2 to finish before starting this step
- Independent steps (web searches, app opens, etc.) MUST have depends_on: []
- A step that compiles, writes, or sends results from other steps MUST list those step numbers
- Minimize dependencies — only add a step number if you genuinely need its output

AVAILABLE TOOLS AND THEIR PARAMETERS:

open_app
  app_name: string (required)

web_search
  query: string (required) — write a clear, focused search query
  mode: "search" or "compare" (optional, default: search)
  items: list of strings (optional, for compare mode)
  aspect: string (optional, for compare mode)

game_updater
  action: "update" | "install" | "list" | "download_status" | "schedule" (required)
  platform: "steam" | "epic" | "both" (optional, default: both)
  game_name: string (optional)
  app_id: string (optional)
  shutdown_when_done: boolean (optional)

browser_control
  action: "go_to" | "search" | "click" | "type" | "scroll" | "get_text" | "press" | "close" (required)
  url: string (for go_to)
  query: string (for search)
  text: string (for click/type)
  direction: "up" | "down" (for scroll)

file_controller
  action: "write" | "create_file" | "read" | "list" | "delete" | "move" | "copy" | "find" | "disk_usage" (required)
  path: string — use "desktop" for Desktop folder
  name: string — filename
  content: string — file content (for write/create_file)

computer_settings
  action: string (required)
  description: string — natural language description
  value: string (optional)

computer_control
  action: "type" | "click" | "hotkey" | "press" | "scroll" | "screenshot" | "screen_find" | "screen_click" (required)
  text: string (for type)
  x, y: int (for click)
  keys: string (for hotkey, e.g. "ctrl+c")
  key: string (for press)
  direction: "up" | "down" (for scroll)
  description: string (for screen_find/screen_click)

screen_process
  text: string (required) — what to analyze or ask about the screen
  angle: "screen" | "camera" (optional)

send_message
  receiver: string (required)
  message_text: string (required)
  platform: string (required)

reminder
  date: string YYYY-MM-DD (required)
  time: string HH:MM (required)
  message: string (required)

desktop_control
  action: "wallpaper" | "organize" | "clean" | "list" | "task" (required)
  path: string (optional)
  task: string (optional)

youtube_video
  action: "play" | "summarize" | "trending" (required)
  query: string (for play)

weather_report
  city: string (required)

flight_finder
  origin: string (required)
  destination: string (required)
  date: string (required)

code_helper
  action: "write" | "edit" | "run" | "explain" (required)
  description: string (required)
  language: string (optional)
  output_path: string (optional)
  file_path: string (optional)

dev_agent
  description: string (required)
  language: string (optional)

email_reader
  action: "check" | "summary" (required)
  count: int (optional, number of emails, default 10)

task_manager
  action: "add" | "list" | "complete" | "delete" | "clear" | "clear_all" (required)
  task: string (for add/complete/delete — the task text)
  index: int (optional, 1-based task number for complete/delete)

system_control
  action: "shutdown" | "restart" | "sleep" | "lock" (required)
  delay: int (optional, seconds to wait before acting, default 0)

slack_reader
  action: "summary" | "unread" (required)
  channel: string (optional, channel name or ID to focus on)

claude_dev
  action: "run" | "status" (required)
  task: string (required for run — what Claude should do)
  repo_path: string (required for run — absolute path to git repo)
  branch: string (optional)
  create_pr: boolean (optional, push and open PR when done)
  pr_title: string (optional)
  slack_channel: string (optional, e.g. "#dev-updates")
  open_ide: boolean (optional, default true)

integration_setup
  action: "status" | "set" | "auth" | "list" (required)
  service: string (optional: gmail | slack | github | notion | linear | jira | google_calendar)
  key: string (optional: credential key to set)
  value: string (optional: credential value)
EXAMPLES:

EXAMPLES (note depends_on for parallel execution):

Goal: "research quantum computing and blockchain and save both to files"
[
  {"step":1,"tool":"web_search","parameters":{"query":"quantum computing overview 2025"},"depends_on":[],"critical":true},
  {"step":2,"tool":"web_search","parameters":{"query":"blockchain technology overview 2025"},"depends_on":[],"critical":true},
  {"step":3,"tool":"file_controller","parameters":{"action":"write","path":"desktop","name":"quantum_computing.txt","content":""},"depends_on":[1],"critical":false},
  {"step":4,"tool":"file_controller","parameters":{"action":"write","path":"desktop","name":"blockchain.txt","content":""},"depends_on":[2],"critical":false}
]

Goal: "What is the price of Bitcoin"
[
  {"step":1,"tool":"web_search","parameters":{"query":"Bitcoin price today USD"},"depends_on":[],"critical":true}
]

Goal: "research mechanical engineering and save it to a notepad file"
[
  {"step":1,"tool":"web_search","parameters":{"query":"mechanical engineering overview definition history"},"depends_on":[],"critical":true},
  {"step":2,"tool":"web_search","parameters":{"query":"mechanical engineering applications and future trends"},"depends_on":[],"critical":true},
  {"step":3,"tool":"file_controller","parameters":{"action":"write","path":"desktop","name":"mechanical_engineering.txt","content":""},"depends_on":[1,2],"critical":false}
]

Goal: "Install PUBG from Steam"
[{"step":1,"tool":"game_updater","parameters":{"action":"install","platform":"steam","game_name":"PUBG"},"depends_on":[],"critical":true}]

Goal: "Send John a message on WhatsApp saying there is a meeting tomorrow"
[{"step":1,"tool":"send_message","parameters":{"receiver":"John","message_text":"There is a meeting tomorrow","platform":"WhatsApp"},"depends_on":[],"critical":true}]

Goal: "Open the clock and set a reminder for 30 minutes later"
[{"step":1,"tool":"reminder","parameters":{"date":"[today]","time":"[now+30min]","message":"Reminder"},"depends_on":[],"critical":true}]

OUTPUT — return ONLY valid JSON, no markdown, no explanation, no code blocks:
{
  "goal": "...",
  "steps": [
    {
      "step": 1,
      "tool": "tool_name",
      "description": "what this step does",
      "parameters": {},
      "depends_on": [],
      "critical": true
    }
  ]
}
"""


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def create_plan(goal: str, context: str = "") -> dict:
    import google.generativeai as genai

    genai.configure(api_key=_get_api_key())
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash-lite",
        system_instruction=PLANNER_PROMPT
    )

    user_input = f"Goal: {goal}"
    if context:
        user_input += f"\n\nContext: {context}"

    try:
        response = model.generate_content(user_input)
        text     = response.text.strip()
        text     = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()

        plan = json.loads(text)

        if "steps" not in plan or not isinstance(plan["steps"], list):
            raise ValueError("Invalid plan structure")

        for step in plan["steps"]:
            if step.get("tool") in ("generated_code",):
                print(f"[Planner] ⚠️ generated_code detected in step {step.get('step')} — replacing with web_search")
                desc = step.get("description", goal)
                step["tool"] = "web_search"
                step["parameters"] = {"query": desc[:200]}
            if "depends_on" not in step or not isinstance(step.get("depends_on"), list):
                step["depends_on"] = []

        print(f"[Planner] ✅ Plan: {len(plan['steps'])} steps")
        independent = sum(1 for s in plan["steps"] if not s.get("depends_on"))
        print(f"[Planner] ⚡ {independent} step(s) run immediately in parallel")
        for s in plan["steps"]:
            deps = s.get("depends_on", [])
            dep_str = f" [after {deps}]" if deps else " [parallel]"
            print(f"  Step {s['step']}: [{s['tool']}] {s.get('description','')}{dep_str}")

        return plan

    except json.JSONDecodeError as e:
        print(f"[Planner] ⚠️ JSON parse failed: {e}")
        return _fallback_plan(goal)
    except Exception as e:
        print(f"[Planner] ⚠️ Planning failed: {e}")
        return _fallback_plan(goal)


def _fallback_plan(goal: str) -> dict:
    print("[Planner] 🔄 Fallback plan")
    return {
        "goal": goal,
        "steps": [
            {
                "step": 1,
                "tool": "web_search",
                "description": f"Search for: {goal}",
                "parameters": {"query": goal},
                "depends_on": [],
                "critical": True
            }
        ]
    }


def replan(goal: str, completed_steps: list, failed_step: dict, error: str) -> dict:
    import google.generativeai as genai

    genai.configure(api_key=_get_api_key())
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction=PLANNER_PROMPT
    )

    completed_summary = "\n".join(
        f"  - Step {s['step']} ({s['tool']}): DONE" for s in completed_steps
    )

    prompt = f"""Goal: {goal}

Already completed:
{completed_summary if completed_summary else '  (none)'}

Failed step: [{failed_step.get('tool')}] {failed_step.get('description')}
Error: {error}

Create a REVISED plan for the remaining work only. Do not repeat completed steps."""

    try:
        response = model.generate_content(prompt)
        text     = response.text.strip()
        text     = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
        plan     = json.loads(text)

        for step in plan.get("steps", []):
            if step.get("tool") == "generated_code":
                step["tool"] = "web_search"
                step["parameters"] = {"query": step.get("description", goal)[:200]}
            if "depends_on" not in step or not isinstance(step.get("depends_on"), list):
                step["depends_on"] = []

        print(f"[Planner] 🔄 Revised plan: {len(plan['steps'])} steps")
        return plan
    except Exception as e:
        print(f"[Planner] ⚠️ Replan failed: {e}")
        return _fallback_plan(goal)