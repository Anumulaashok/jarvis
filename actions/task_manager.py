"""
Task manager — PostgreSQL primary storage, JSON fallback.
"""
import json
import sys
from datetime import datetime
from pathlib import Path


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR   = get_base_dir()
TASKS_PATH = BASE_DIR / "memory" / "tasks.json"


# ── JSON fallback ────────────────────────────────────────────────────────────

def _load_json() -> list:
    if not TASKS_PATH.exists():
        return []
    try:
        return json.loads(TASKS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_json(tasks: list) -> None:
    TASKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TASKS_PATH.write_text(json.dumps(tasks, indent=2, ensure_ascii=False), encoding="utf-8")


# ── PostgreSQL helpers ───────────────────────────────────────────────────────

def _pg_ready() -> bool:
    try:
        from integrations.pg_store import is_ready
        return is_ready()
    except Exception:
        return False


def _pg_list() -> list:
    from integrations.pg_store import list_tasks
    rows = list_tasks()
    return [{"id": r["id"], "text": r["text"], "done": r["done"],
             "created": r["created"]} for r in rows]


def _sync_json_to_pg() -> None:
    """One-time migration: push existing JSON tasks into PostgreSQL."""
    try:
        from integrations.pg_store import add_task, list_tasks
        existing = {r["text"] for r in list_tasks()}
        for t in _load_json():
            if t.get("text") and t["text"] not in existing:
                add_task(t["text"])
    except Exception:
        pass


# ── Main ─────────────────────────────────────────────────────────────────────

def task_manager(parameters: dict, player=None, speak=None) -> str:
    action = parameters.get("action", "list")
    use_pg = _pg_ready()

    if use_pg:
        # One-time migration if JSON has tasks PostgreSQL doesn't
        _sync_json_to_pg()

    if action == "add":
        text = (parameters.get("task") or parameters.get("text") or "").strip()
        if not text:
            return "Please provide a task description."

        if use_pg:
            from integrations.pg_store import add_task
            add_task(text)
        else:
            tasks = _load_json()
            tasks.append({"id": len(tasks) + 1, "text": text, "done": False,
                           "created": datetime.now().strftime("%Y-%m-%d %H:%M")})
            _save_json(tasks)

        tasks = _pg_list() if use_pg else _load_json()
        pending = sum(1 for t in tasks if not t["done"])
        return f"Task added: \"{text}\" — {pending} pending, sir."

    elif action == "list":
        tasks = _pg_list() if use_pg else _load_json()
        if not tasks:
            return "Your task list is empty, sir."
        pending = [t for t in tasks if not t["done"]]
        done    = [t for t in tasks if t["done"]]
        lines   = [f"Tasks ({len(pending)} pending, {len(done)} done):"]
        for i, t in enumerate(pending, 1):
            lines.append(f"  {i}. ○ {t['text']}")
        for t in done[:3]:
            lines.append(f"     ✓ {t['text']}")
        return "\n".join(lines)

    elif action == "complete":
        idx  = parameters.get("index")
        text = (parameters.get("task") or parameters.get("text") or "").strip()

        if use_pg:
            from integrations.pg_store import list_tasks, complete_task
            tasks = list_tasks(done=False)
            if idx is not None:
                try:
                    t = tasks[int(idx) - 1]
                    complete_task(t["id"])
                    return f"Marked complete: \"{t['text']}\", sir."
                except IndexError:
                    return f"No task at position {idx}."
            # Match by text
            matches = [t for t in tasks if text.lower() in t["text"].lower()]
            if not matches:
                return f"No task matching \"{text}\" found."
            for t in matches:
                complete_task(t["id"])
            return f"Completed: {', '.join(t['text'] for t in matches)}, sir."
        else:
            tasks = _load_json()
            if idx is not None:
                try:
                    i = int(idx) - 1
                    tasks[i]["done"] = True
                    _save_json(tasks)
                    return f"Marked complete: \"{tasks[i]['text']}\", sir."
                except IndexError:
                    return f"No task at index {idx}."
            hits = [i for i, t in enumerate(tasks) if text.lower() in t["text"].lower()]
            if not hits:
                return f"No task matching \"{text}\" found."
            for i in hits:
                tasks[i]["done"] = True
            _save_json(tasks)
            return f"Completed, sir."

    elif action == "delete":
        idx  = parameters.get("index")
        text = (parameters.get("task") or parameters.get("text") or "").strip()

        if use_pg:
            from integrations.pg_store import list_tasks, delete_task
            tasks = list_tasks()
            if idx is not None:
                try:
                    t = tasks[int(idx) - 1]
                    delete_task(t["id"])
                    return f"Deleted: \"{t['text']}\", sir."
                except IndexError:
                    return f"No task at position {idx}."
            matches = [t for t in tasks if text.lower() in t["text"].lower()]
            if not matches:
                return f"No task matching \"{text}\" found."
            for t in matches:
                delete_task(t["id"])
            return f"Deleted {len(matches)} task(s), sir."
        else:
            tasks = _load_json()
            if idx is not None:
                try:
                    removed = tasks.pop(int(idx) - 1)
                    _save_json(tasks)
                    return f"Deleted: \"{removed['text']}\", sir."
                except IndexError:
                    return f"No task at index {idx}."
            hits = [i for i, t in enumerate(tasks) if text.lower() in t["text"].lower()]
            if not hits:
                return f"No task matching \"{text}\" found."
            for i in reversed(hits):
                tasks.pop(i)
            _save_json(tasks)
            return "Deleted, sir."

    elif action == "clear":
        if use_pg:
            from integrations.pg_store import clear_completed
            n = clear_completed()
            return f"Cleared {n} completed task(s), sir."
        else:
            tasks = _load_json()
            before = len(tasks)
            tasks  = [t for t in tasks if not t["done"]]
            _save_json(tasks)
            return f"Cleared {before - len(tasks)} completed task(s), sir."

    elif action == "clear_all":
        if use_pg:
            from integrations.pg_store import _get_conn
            cur = _get_conn().cursor()
            cur.execute("DELETE FROM jarvis_tasks")
            cur.close()
        _save_json([])
        return "All tasks cleared, sir."

    return f"Unknown task action: {action}"
