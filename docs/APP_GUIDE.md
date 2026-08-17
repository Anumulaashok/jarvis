# Captain Jack — Application Guide

> A detailed reference for understanding what this app does, how it works, where the gaps are, and what to improve next.

**Last updated:** June 5, 2026  
**Codebase path:** `/Users/anumulaashok/test/jarvis`

---

## Table of Contents

1. [What Is This App?](#1-what-is-this-app)
2. [How It Works (Architecture)](#2-how-it-works-architecture)
3. [Project Structure](#3-project-structure)
4. [Features & Tools](#4-features--tools)
5. [Background Agents](#5-background-agents)
6. [Integrations](#6-integrations)
7. [Personas](#7-personas)
8. [Configuration & Secrets](#8-configuration--secrets)
9. [Permissions & Logging](#9-permissions--logging)
10. [Known Gaps & Bugs](#10-known-gaps--bugs)
11. [What the Logs Tell You](#11-what-the-logs-tell-you)
12. [Improvement Roadmap](#12-improvement-roadmap)
13. [Quick Start Checklist](#13-quick-start-checklist)

---

## 1. What Is This App?

**Captain Jack** is a voice-first AI desktop assistant for macOS (also supports Windows/Linux with varying completeness). You talk to it; it listens via Gemini Live audio, decides which tool to call, executes actions on your computer, and speaks back.

Think of it as three layers:

| Layer | What it does |
|-------|----------------|
| **Voice orchestrator** | Real-time conversation via Gemini Live (`main.py`) |
| **Tool executor** | Single-step actions (open app, weather, browser, files…) |
| **Background agents** | Autonomous watchers (Gmail/Mick, Slack) + dev agent (Claude Code) |

The UI is a PyQt6 desktop app (`ui.py`) branded **CAPTAIN JACK** with a sci-fi HUD, activity log, task sidebar, file drop zone, and permission prompts.

> **Naming note:** The codebase still uses "Jarvis" in many internal places (classes, DB tables, log prefixes). The user-facing brand is **Captain Jack**. Three personas — Tommy, Gibbs, Jack — deliver responses in different voices and styles.

---

## 2. How It Works (Architecture)

```
┌─────────────────────────────────────────────────────────────────┐
│                         YOU (voice / text)                      │
└───────────────────────────────┬─────────────────────────────────┘
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│  ui.py — PyQt6 UI (mic, log, permissions, file upload)          │
└───────────────────────────────┬─────────────────────────────────┘
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│  main.py — JarvisLive (Gemini Live WebSocket session)           │
│  • Loads persona prompt + memory + datetime                     │
│  • Registers 28 tools (TOOL_DECLARATIONS)                       │
│  • Dispatches tool calls → actions/ or agent/                   │
└───────┬─────────────────────────────┬─────────────────────────┘
        │                             │
        ▼                             ▼
┌───────────────────┐     ┌─────────────────────────────────────┐
│  actions/*.py     │     │  agent/executor.py (agent_task)     │
│  Direct tools     │     │  planner.py → parallel step runner  │
│  (open_app, etc.) │     │  Up to 6 steps, error recovery      │
└───────────────────┘     └─────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│  agents/watchers/ — background polling (started at boot)        │
│  GmailWatcher (5 min) → MickAgent                               │
│  SlackWatcher (2 min) → speaks channel summaries                │
└─────────────────────────────────────────────────────────────────┘
```

### Request flow (single command)

1. You speak → mic captures audio → sent to Gemini Live
2. Gemini decides to call a tool (e.g. `weather_report`)
3. `JarvisLive._execute_tool()` checks permissions (if sensitive)
4. Action runs in a thread pool
5. Result sent back to Gemini → spoken response

### Request flow (multi-step goal)

1. You say something complex → Gemini calls `agent_task`
2. `AgentExecutor` asks `planner.py` to break goal into steps
3. Steps run in parallel where possible (`depends_on` graph)
4. On failure: retry, skip, replan, or abort
5. Final summary returned (currently **silent** — no voice during execution)

### Boot sequence

```
main.py
  → setup_logging()          # logs/captain_jack_YYYY-MM-DD.log
  → JarvisUI()               # PyQt6 window
  → wait_for_api_key()       # blocks until Gemini key + OS set
  → watcher_manager.start_all()
  → JarvisLive.run()         # Gemini Live session loop
```

---

## 3. Project Structure

```
jarvis/
├── main.py                 # Entry point, Gemini Live, tool routing
├── ui.py                   # PyQt6 UI (~1800 lines)
├── authorize_gmail.py      # One-shot Gmail OAuth CLI
├── requirements.txt        # Python dependencies (incomplete — see gaps)
│
├── actions/                # 23 tool implementations
├── agent/                  # Multi-step planner + executor + task queue
├── agents/                 # Background agents (Mick, Claude Dev, watchers, browser healer)
├── core/                   # Prompts, personas, permissions, logger
├── config/                 # API keys, integration credentials, OAuth tokens
├── integrations/           # Gmail, Slack, GitHub, Postgres, Pinecone clients
├── memory/                 # Local JSON memory, tasks, Mick preferences
├── logs/                   # Daily rotating logs
└── docs/                   # This guide
```

---

## 4. Features & Tools

### 28 live tools (registered in `main.py`)

| Tool | File | What it does |
|------|------|--------------|
| `open_app` | `actions/open_app.py` | Launch any app (Chrome, VS Code, IntelliJ, Terminal, Slack…) |
| `web_search` | `actions/web_search.py` | Gemini search + DuckDuckGo fallback |
| `weather_report` | `actions/weather_report.py` | Weather for a city |
| `send_message` | `actions/send_message.py` | WhatsApp/Telegram/Discord via UI automation |
| `reminder` | `actions/reminder.py` | OS-scheduled reminders |
| `youtube_video` | `actions/youtube_video.py` | Play, summarize, trending |
| `screen_process` | `actions/screen_processor.py` | Screen/webcam + Gemini vision |
| `computer_settings` | `actions/computer_settings.py` | Volume, brightness, WiFi, shortcuts, power |
| `browser_control` | `actions/browser_control.py` | Playwright browser automation; macOS Chrome via AppleScript |
| `file_controller` | `actions/file_controller.py` | File CRUD (sandboxed to home directory) |
| `desktop_control` | `actions/desktop.py` | Wallpaper, organize desktop |
| `code_helper` | `actions/code_helper.py` | Write/edit/run/explain code files |
| `dev_agent` | `actions/dev_agent.py` | Multi-file project builder (Gemini-powered) |
| `computer_control` | `actions/computer_control.py` | Mouse, keyboard, screenshots |
| `game_updater` | `actions/game_updater.py` | Steam/Epic install/update |
| `flight_finder` | `actions/flight_finder.py` | Google Flights search |
| `file_processor` | `actions/file_processor.py` | Process uploaded files (PDF, images, code…) |
| `email_reader` | `actions/email_reader.py` | Read Gmail (API or macOS Chrome fallback) |
| `slack_reader` | `actions/slack_reader.py` | Read Slack (API or macOS Chrome fallback) |
| `task_manager` | `actions/task_manager.py` | Personal to-do list |
| `system_control` | `actions/system_control.py` | Shutdown, restart, sleep, lock |
| `claude_dev` | `actions/claude_dev.py` | **NEW** — Claude Code CLI dev agent |
| `integration_setup` | `actions/integration_setup.py` | Manage API credentials & OAuth |
| `agent_task` | `agent/executor.py` | Multi-step planner for complex goals |
| `mick` | `agents/mick/mick_agent.py` | Gmail agent interface |
| `save_memory` | inline in `main.py` | Store facts to long-term memory |
| `switch_persona` | inline in `main.py` | Switch Tommy / Gibbs / Jack |
| `shutdown_jarvis` | inline in `main.py` | Exit the app |

### Permission-gated tools

These show an **Allow / Deny** overlay on first use (session-scoped):

- `system_control`, `send_message`, `computer_control`, `file_controller`
- `dev_agent`, `generated_code`, `email_reader`, `slack_reader`
- `browser_control`, `claude_dev`

Config: `core/permission_manager.py`

---

## 5. Background Agents

### Mick — Gmail Agent

**Files:** `agents/mick/`

| Component | Role |
|-----------|------|
| `mick_agent.py` | Singleton; processes emails, never speaks directly — routes through active persona |
| `classifier.py` | Gemini classifies emails: report / auto_archive / needs_clarity / auto_reply |
| `preferences.py` | User-taught ignore/important senders (`memory/mick_preferences.json`) |
| `gmail_watcher.py` | Polls Gmail every 5 min → hands new mail to Mick |

**How you interact:**
- Mick speaks via Tommy/Gibbs/Jack: *"Sir, Mick reports a new email from…"*
- You reply → `mick(action="reply", user_reply="...")`
- Check inbox: `mick(action="summary")`

### Claude Dev Agent

**Files:** `agents/claude_dev/`, `actions/claude_dev.py`

Workflow when you delegate a coding task:

1. Opens **IntelliJ IDEA** with the repo
2. Opens **Terminal** in that folder
3. Runs **`git pull`**
4. Runs **Claude Code CLI** (`claude -p "task"`) in the repo
5. Speaks progress updates to you
6. Optionally posts to **Slack** and creates a **GitHub PR**

**Example voice command:**
> "Open the jarvis repo and fix the import errors, then create a PR"

**Requires:** Claude Code CLI installed (`~/.local/bin/claude`), git repo path, optional `github_token` and Slack channel.

### Slack Watcher

**File:** `agents/watchers/slack_watcher.py`

- Polls Slack every 2 minutes
- Speaks unread channel summaries through the active persona
- **Currently broken** if Slack token is expired (see gaps)

### Browser Self-Healer

**Files:** `agents/browser_agent/`

- Invoked via `browser_control` action `heal`
- Uses Gemini vision + pattern memory to recover stuck browser states
- Not automatic on all browser failures

---

## 6. Integrations

| Service | Status | Config | Implementation |
|---------|--------|--------|----------------|
| **Gmail** | ✅ Working (read) | `integrations.json` + `tokens/gmail.json` | `integrations/gmail.py` |
| **Gmail archive** | ⚠️ Broken | Same token | Needs `gmail.modify` scope (currently `readonly` only) |
| **Slack** | ⚠️ Token expired | `integrations.json` + `tokens/slack.json` | `integrations/slack_api.py` |
| **GitHub** | 🔶 Partial | `github_token` in integrations | `integrations/github.py` — git/PR only, no watcher |
| **GCP / Cloud Logging** | ❌ Not built | — | Mentioned in prompts only |
| **Postgres (Neon)** | ✅ Working | `postgres.url` | Tasks + memory mirror + watcher state |
| **Pinecone** | 🔶 Write-only | `pinecone.*` in integrations | Upserts on memory save; search never used |
| **Google Calendar** | ❌ Registry only | — | Listed in `integration_setup`, no code |
| **Notion / Linear / Jira** | ❌ Registry only | — | Listed in `integration_setup`, no code |

### How to configure integrations

Voice/text commands:
```
integration_setup action=status
integration_setup action=set service=gmail key=google_client_id value=...
integration_setup action=auth service=gmail
```

Or edit `config/integrations.json` directly.

---

## 7. Personas

| Persona | Voice | Style | Default? |
|---------|-------|-------|----------|
| **Tommy** | Kore | Professional executive assistant | Intended default |
| **Gibbs** | Orus | NCIS sailor / first mate | |
| **Jack** | Puck | Captain Jack Sparrow | Currently active in `active_persona.json` |

**Switch:** Say *"switch to Tommy"* or use `switch_persona` tool.

**Files:**
- `core/personas/tommy.txt`, `gibbs.txt`, `jack.txt`
- `core/active_persona.json` — persists active choice
- `core/prompt.txt` — fallback if persona file missing

Each persona prompt includes routing rules for Mick and Claude Dev.

---

## 8. Configuration & Secrets

| File | Gitignored | Contents |
|------|------------|----------|
| `config/api_keys.json` | ✅ | `gemini_api_key`, `os_system` |
| `config/integrations.json` | ✅ | Per-service credentials (Gmail, Slack, Postgres, Pinecone…) |
| `config/tokens/gmail.json` | ✅ | Gmail OAuth token |
| `config/tokens/slack.json` | ✅ | Slack rotatable token |
| `core/active_persona.json` | ✅ | Active persona name |
| `memory/long_term.json` | ✅ | Long-term memory (6 categories) |
| `memory/tasks.json` | ✅ | To-do list JSON fallback |
| `memory/mick_preferences.json` | ❌ should be | Mick email ignore rules |

**First-run setup:** UI overlay collects Gemini API key + OS choice → writes `api_keys.json`.

---

## 9. Permissions & Logging

### Permission system (`core/permission_manager.py`)

- First use of a sensitive tool → UI overlay (Allow / Deny)
- Decision cached for the session
- On deny: tool blocked, Captain Jack tells you
- **Gap:** `reset()` on reconnect is dead code (see bugs) — permissions never clear mid-session correctly on reconnect

### Logging (`core/logger.py`)

| Log file | Contents |
|----------|----------|
| `logs/captain_jack_YYYY-MM-DD.log` | All app output, errors, tracebacks |
| `logs/startup_YYYY-MM-DD.log` | Shell-level startup from `.app` launcher |
| `jarvis.error.log` (legacy) | Old single-file log at project root |

- Daily rotation, 14 days retained
- Uncaught exceptions logged with full traceback
- Most modules still use `print("[JARVIS]...")` — not fully migrated to structured logging

**Useful commands:**
```bash
# Today's log
cat logs/captain_jack_$(date +%Y-%m-%d).log

# Errors only
grep -E "ERROR|CRITICAL|failed|403|token_expired" logs/captain_jack_*.log
```

---

## 10. Known Gaps & Bugs

### 🔴 Critical — affects daily use

| # | Issue | Impact | Fix |
|---|-------|--------|-----|
| 1 | **Slack token expired** | Slack watcher fails every 2 min; dev agent can't post updates | Re-authorize Slack; update token in `integrations.json` |
| 2 | **Gmail readonly scope** | Mick can read but **cannot archive** emails (403 errors) | Change `SCOPES` in `integrations/gmail.py` to include `gmail.modify`; re-run OAuth |
| 3 | **Reconnect loop bug** | `permission_manager.reset()` and 3s backoff in `main.py` lines 1227–1235 are **outside** the `while True` loop — unreachable dead code | Move those lines inside the loop |
| 4 | **`agent_task` is silent** | Multi-step tasks run with `speak=None` — no voice progress | Pass `speak=self.speak` to `AgentExecutor` |

### 🟡 Important — incomplete features

| # | Issue | Impact | Fix |
|---|-------|--------|-----|
| 5 | **`task_queue` not wired** | `agent/task_queue.py` exists but nothing uses it; long tasks block voice session | Wire `agent_task` → `TaskQueue.submit()` |
| 6 | **GCP not implemented** | `claude_dev` claims GCP log access but no `integrations/gcp.py` | Build gcloud wrapper or remove from descriptions |
| 7 | **GitHub monitoring missing** | GitHub integration only does git/PR; no PR/issue watcher | Add `GitHubWatcher` like SlackWatcher |
| 8 | **Pinecone search unused** | Vector memory written but never read back into prompts | Call `pinecone_memory.search()` on session start |
| 9 | **Postgres history unused** | `append_history()` defined but never called | Persist conversation turns to DB |
| 10 | **Registry-only integrations** | Notion, Linear, Jira, Google Calendar have no implementation | Build or remove from tool schemas |
| 11 | **`requirements.txt` incomplete** | Missing `slack_sdk`, `psycopg2-binary`, `pinecone`, Google OAuth libs | Add all integration deps; split Windows-only packages |

### 🟢 Polish — technical debt

| # | Issue | Notes |
|---|-------|-------|
| 12 | **Dual Gemini SDKs** | Live audio uses `google.genai`; everything else uses deprecated `google.generativeai` |
| 13 | **"Jarvis" naming remnants** | Classes (`JarvisUI`), DB tables (`jarvis_tasks`), paths (`~/.jarvis_profiles/`) |
| 14 | **macOS-only fallbacks** | `email_reader` / `slack_reader` Chrome scraping uses AppleScript only |
| 15 | **Deprecated `duckduckgo_search`** | Package renamed to `ddgs` — warning in logs |
| 16 | **Planner forbids `generated_code`** | Executor still falls back to generating Python on unknown tools |
| 17 | **Default persona drift** | Code defaults to `tommy` but `active_persona.json` currently says `jack` |

---

## 11. What the Logs Tell You

Based on the June 5, 2026 session (`logs/captain_jack_2026-06-05.log`):

### ✅ Working
- App boots, connects as persona, mic/audio streaming healthy
- Gmail watcher polling; Mick classifying emails
- Browser automation (opened Google Cloud Console for billing setup)
- Multi-step `agent_task` planner executed 5 steps
- Web search with DDG fallback when Gemini 503'd

### ❌ Failing repeatedly
```
[Slack] token_expired
[Mick] archive_email error: insufficientPermissions (gmail.readonly)
[WebSearch] Gemini 503 UNAVAILABLE (temporary, recovered via DDG)
```

### ⚠️ Warnings (non-blocking)
```
google.generativeai package deprecated → migrate to google.genai
duckduckgo_search renamed to ddgs
```

---

## 12. Improvement Roadmap

### Phase 1 — Fix what's broken (1–2 days)

```
Priority  Task
────────  ────
P0        Re-auth Slack (fix token_expired)
P0        Re-auth Gmail with gmail.modify scope (fix Mick archive)
P0        Fix main.py reconnect loop indentation bug
P0        Pass speak= to AgentExecutor for voice during agent_task
P1        Complete requirements.txt with all integration packages
```

### Phase 2 — Make agents actually useful (1 week)

```
Priority  Task
────────  ────
P1        Wire task_queue → agent_task (non-blocking long tasks)
P1        Add cancel/status tool for running tasks
P1        Integrate Pinecone search into memory prompt injection
P1        Persist conversation history to Postgres
P2        GitHub watcher (PR/issue notifications via speak)
P2        Implement GCP Cloud Logging integration for claude_dev
```

### Phase 3 — Polish & scale (ongoing)

```
Priority  Task
────────  ────
P2        Migrate all modules from google.generativeai → google.genai
P2        Rename Jarvis → Captain Jack consistently (classes, tables, paths)
P2        Cross-platform email/Slack fallbacks (not just macOS AppleScript)
P2        Structured logging project-wide (replace print statements)
P2        Build or remove Notion/Linear/Jira/Calendar stubs
P3        Per-tool permission persistence across sessions
P3        Conversation history panel in UI
P3        Task queue UI (show running claude_dev / agent_task jobs)
```

### Architecture target (end state)

```
Voice (Gemini Live)
    │
    ├── Fast tools (< 5s)     → direct actions/, immediate voice response
    │
    ├── Long tasks (> 5s)     → TaskQueue (background)
    │       ├── agent_task    → planner + executor
    │       └── claude_dev    → Claude Code CLI
    │       └── progress      → speak() + Slack updates
    │
    └── Watchers (always on)  → Mick (Gmail), Slack, GitHub
            └── notifications → active persona speaks
```

---

## 13. Quick Start Checklist

### Minimum to run
- [ ] `config/api_keys.json` with valid `gemini_api_key`
- [ ] Python venv with deps from `requirements.txt` (+ integration packages manually)
- [ ] Launch via `python main.py` or `Captain Jack.app`

### Recommended integrations
- [ ] Gmail OAuth (`authorize_gmail.py` or `integration_setup auth`)
- [ ] Slack bot token in `integrations.json`
- [ ] GitHub token (for claude_dev PRs) — optional if `gh` CLI is authed
- [ ] Claude Code CLI (`npm install -g @anthropic-ai/claude-code`)
- [ ] Postgres URL (for tasks + memory) — optional, JSON fallback works
- [ ] Pinecone API key — optional, semantic memory not read yet

### After code changes
- [ ] Restart Captain Jack to load new tools/agents
- [ ] Check `logs/captain_jack_$(date +%Y-%m-%d).log` for errors
- [ ] Test permission overlay on first sensitive tool use

---

## Appendix: Key File Index

| Concern | File |
|---------|------|
| Entry point | `main.py` |
| UI | `ui.py` |
| Tool declarations | `main.py` → `TOOL_DECLARATIONS` |
| Multi-step agent | `agent/executor.py`, `agent/planner.py` |
| Task queue (unused) | `agent/task_queue.py` |
| Permissions | `core/permission_manager.py` |
| Logging | `core/logger.py` |
| Memory | `memory/memory_manager.py` |
| Watchers | `agents/watchers/watcher_manager.py` |
| Mick (Gmail) | `agents/mick/mick_agent.py` |
| Claude Dev | `agents/claude_dev/claude_dev_agent.py` |
| Integrations registry | `integrations/manager.py` |
| Personas | `core/personas/*.txt` |
| Gmail API | `integrations/gmail.py` |
| Slack API | `integrations/slack_api.py` |
| GitHub API | `integrations/github.py` |

---

*This document reflects the codebase as of June 5, 2026. Update it when major features land or gaps are closed.*
