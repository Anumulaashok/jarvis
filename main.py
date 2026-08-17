import asyncio
import re
import threading
import json
import sys
import traceback
from pathlib import Path

# Logging must be set up before any other import so all modules inherit it
from core.logger import setup_logging, get_logger
setup_logging()
_log = get_logger("captain_jack.main")

import sounddevice as sd
from google import genai
from google.genai import types
from ui import JarvisUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
)

from actions.file_processor import file_processor
from actions.flight_finder     import flight_finder
from actions.open_app          import open_app
from actions.weather_report    import weather_action
from actions.send_message      import send_message
from actions.reminder          import reminder
from actions.computer_settings import computer_settings
from actions.screen_processor  import screen_process
from actions.youtube_video     import youtube_video
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.file_controller   import file_controller
from actions.code_helper       import code_helper
from actions.dev_agent         import dev_agent
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control
from actions.game_updater      import game_updater
from core.permission_manager   import get_permission_manager


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR             = get_base_dir()
API_CONFIG_PATH      = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH          = BASE_DIR / "core" / "prompt.txt"
PERSONAS_DIR         = BASE_DIR / "core" / "personas"
ACTIVE_PERSONA_PATH  = BASE_DIR / "core" / "active_persona.json"
LIVE_MODEL           = "models/gemini-2.5-flash-native-audio-preview-12-2025"
CHANNELS             = 1
SEND_SAMPLE_RATE     = 16000
RECEIVE_SAMPLE_RATE  = 24000
CHUNK_SIZE           = 1024

PERSONA_VOICES = {
    "tommy": "Kore",
    "gibbs": "Orus",
    "jack":  "Puck",
}

PERSONA_GREETINGS = {
    "tommy": "You just came online as Tommy. Greet the user in one professional sentence.",
    "gibbs": "You just came online as Joshamee Gibbs, first mate of the Black Pearl. Give a short loyal sailor greeting to the Captain — one or two salty seafarer lines.",
    "jack":  "You just came online as Captain Jack Sparrow. Give a wildly dramatic, funny entrance — two lines maximum, maximum pirate swagger.",
}

def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _get_active_persona() -> str:
    try:
        with open(ACTIVE_PERSONA_PATH, encoding="utf-8") as f:
            return json.load(f).get("persona", "tommy")
    except Exception:
        return "tommy"


def _set_active_persona(name: str) -> None:
    with open(ACTIVE_PERSONA_PATH, "w", encoding="utf-8") as f:
        json.dump({"persona": name}, f)


def _load_system_prompt() -> str:
    persona = _get_active_persona()
    persona_file = PERSONAS_DIR / f"{persona}.txt"
    try:
        return persona_file.read_text(encoding="utf-8")
    except Exception:
        pass
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are a professional AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks."
        )

class _PersonaSwitchSignal(Exception):
    def __init__(self, persona: str):
        super().__init__(f"persona_switch:{persona}")
        self.persona = persona


_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

def _clean_transcript(text: str) -> str:    
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()

TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens any application on the computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": "Searches the web for any information.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query"},
                "mode":   {"type": "STRING", "description": "search (default) or compare"},
                "items":  {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Items to compare"},
                "aspect": {"type": "STRING", "description": "price | specs | reviews"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "weather_report",
        "description": "Gives the weather report to user",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "send_message",
        "description": "Sends a text message via WhatsApp, Telegram, or other messaging platform.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name"},
                "message_text": {"type": "STRING", "description": "The message to send"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc."}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube. Use for: playing videos, summarizing a video's content, "
            "getting video info, or showing trending videos."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending (default: play)"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures and analyzes the screen or webcam image. "
            "MUST be called when user asks what is on screen, what you see, "
            "analyze my screen, look at camera, etc. "
            "You have NO visual ability without this tool. "
            "After calling this tool, stay SILENT — the vision module speaks directly."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
            "Use for ANY single computer control command. NEVER route to agent_task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "The action to perform"},
                "description": {"type": "STRING", "description": "Natural language description of what to do"},
                "value":       {"type": "STRING", "description": "Optional value: volume level, text to type, etc."}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Controls any web browser. Use for: opening websites, searching the web, "
            "clicking elements, filling forms, scrolling, screenshots, navigation, any web-based task. "
            "Always pass the 'browser' parameter when the user specifies a browser (e.g. 'open in Edge', "
            "'use Firefox', 'open Chrome'). Multiple browsers can run simultaneously."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | get_url | press | new_tab | close_tab | screenshot | back | forward | reload | switch | list_browsers | close | close_all | heal | switch_profile | list_profiles | read_page"},
                "browser":     {"type": "STRING", "description": "Target browser: chrome | edge | firefox | opera | operagx | brave | vivaldi | safari. Omit to use the currently active browser."},
                "url":         {"type": "STRING", "description": "URL for go_to / new_tab action"},
                "query":       {"type": "STRING", "description": "Search query for search action"},
                "engine":      {"type": "STRING", "description": "Search engine: google | bing | duckduckgo | yandex (default: google)"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
                "text":        {"type": "STRING", "description": "Text to click or type, or profile name for switch_profile"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type, or task goal for heal"},
                "direction":   {"type": "STRING", "description": "up | down for scroll"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount in pixels (default: 500)"},
                "key":         {"type": "STRING", "description": "Key name for press action (e.g. Enter, Escape, F5)"},
                "path":        {"type": "STRING", "description": "Save path for screenshot"},
                "incognito":   {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        "description": "Controls the desktop: wallpaper, organize, clean, list, stats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Writes, edits, explains, runs, or builds code files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "write | edit | explain | run | build | auto (default: auto)"},
                "description": {"type": "STRING", "description": "What the code should do or what change to make"},
                "language":    {"type": "STRING", "description": "Programming language (default: python)"},
                "output_path": {"type": "STRING", "description": "Where to save the file"},
                "file_path":   {"type": "STRING", "description": "Path to existing file for edit/explain/run/build"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "dev_agent",
        "description": "Builds complete multi-file projects from scratch: plans, writes files, installs deps, opens VSCode, runs and fixes errors.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language (default: python)"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "agent_task",
        "description": (
            "Executes complex multi-step tasks requiring multiple different tools. "
            "Examples: 'research X and save to file', 'find and organize files'. "
            "DO NOT use for single commands. NEVER use for Steam/Epic — use game_updater."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal":     {"type": "STRING", "description": "Complete description of what to accomplish"},
                "priority": {"type": "STRING", "description": "low | normal | high (default: normal)"}
            },
            "required": ["goal"]
        }
    },
    {
        "name": "computer_control",
        "description": "Direct computer control: type, click, hotkeys, scroll, move mouse, screenshots, find elements on screen.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": (
            "THE ONLY tool for ANY Steam or Epic Games request. "
            "Use for: installing, downloading, updating games, listing installed games, "
            "checking download status, scheduling updates. "
            "ALWAYS call directly for any Steam/Epic/game request. "
            "NEVER use agent_task, browser_control, or web_search for Steam/Epic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status (default: update)"},
                "platform":  {"type": "STRING",  "description": "steam | epic | both (default: both)"},
                "game_name": {"type": "STRING",  "description": "Game name (partial match supported)"},
                "app_id":    {"type": "STRING",  "description": "Steam AppID for install (optional)"},
                "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23 (default: 3)"},
                "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59 (default: 0)"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when download finishes"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
        "name": "shutdown_jarvis",
        "description": (
            "Shuts down the assistant completely. "
            "Call this when the user expresses intent to end the conversation, "
            "close the assistant, say goodbye, or stop Captain Jack. "
            "The user can say this in ANY language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "email_reader",
        "description": (
            "Reads the user's Gmail inbox. Use 'check' to list unread email subjects and senders. "
            "Use 'summary' to get an AI-summarized digest of unread emails. "
            "Requires email_address and email_password in api_keys.json (use a Gmail App Password)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "check | summary"},
                "count":  {"type": "INTEGER", "description": "Max emails to show (default 10)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "task_manager",
        "description": (
            "Manages the user's personal task / to-do list. "
            "Use to add tasks, list all tasks, mark tasks complete, delete tasks, or clear completed ones. "
            "Tasks persist across sessions and are shown live in the UI sidebar."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "add | list | complete | delete | clear | clear_all"},
                "task":   {"type": "STRING", "description": "Task text (for add/complete/delete)"},
                "index":  {"type": "INTEGER", "description": "1-based task number (for complete/delete)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "system_control",
        "description": (
            "Controls the operating system power state. "
            "Use for: shutting down the computer, restarting it, putting it to sleep, or locking the screen. "
            "Can optionally wait a specified number of seconds before acting."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "shutdown | restart | sleep | lock"},
                "delay":  {"type": "INTEGER", "description": "Seconds to wait before acting (default 0)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "slack_reader",
        "description": (
            "Reads the user's Slack workspace in the existing Chrome browser. "
            "Use for: checking Slack updates, getting a summary of recent messages, listing unread notifications. "
            "Opens app.slack.com in the already-running Chrome — no new window opened."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":  {"type": "STRING", "description": "summary | unread"},
                "channel": {"type": "STRING", "description": "Optional channel ID or name to focus on"},
            },
            "required": ["action"]
        }
    },
    {
    "name": "file_processor",
    "description": (
        "Processes any file that the user has uploaded or dropped onto the interface. "
        "Use this when the user refers to an uploaded file and wants an action on it. "
        "Supports: images (describe/ocr/resize/compress/convert), "
        "PDFs (summarize/extract_text/to_word), "
        "Word docs & text files (summarize/fix/reformat/translate), "
        "CSV/Excel (analyze/stats/filter/sort/convert), "
        "JSON/XML (validate/format/analyze), "
        "code files (explain/review/fix/optimize/run/document/test), "
        "audio (transcribe/trim/convert/info), "
        "video (trim/extract_audio/extract_frame/compress/transcribe/info), "
        "archives (list/extract), "
        "presentations (summarize/extract_text). "
        "ALWAYS call this tool when a file has been uploaded and the user gives a command about it. "
        "If the user's command is ambiguous, pick the most logical action for that file type."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."
            },
            "action": {
                "type": "STRING",
                "description": (
                    "What to do with the file. Examples by type:\n"
                    "image: describe | ocr | resize | compress | convert | info\n"
                    "pdf: summarize | extract_text | to_word | info\n"
                    "docx/txt: summarize | fix | reformat | translate_hint | word_count | to_bullet\n"
                    "csv/excel: analyze | stats | filter | sort | convert | info\n"
                    "json: validate | format | analyze | to_csv\n"
                    "code: explain | review | fix | optimize | run | document | test\n"
                    "audio: transcribe | trim | convert | info\n"
                    "video: trim | extract_audio | extract_frame | compress | transcribe | info | convert\n"
                    "archive: list | extract\n"
                    "pptx: summarize | extract_text | analyze"
                )
            },
            "instruction": {
                "type": "STRING",
                "description": "Free-form instruction if action doesn't cover it. E.g. 'translate this to Turkish', 'find all email addresses'"
            },
            "format": {
                "type": "STRING",
                "description": "Target format for conversion. E.g. 'mp3', 'pdf', 'csv', 'png'"
            },
            "width":     {"type": "INTEGER", "description": "Target width for image resize"},
            "height":    {"type": "INTEGER", "description": "Target height for image resize"},
            "scale":     {"type": "NUMBER",  "description": "Scale factor for image resize (e.g. 0.5)"},
            "quality":   {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"},
            "start":     {"type": "STRING",  "description": "Start time for trim: seconds or HH:MM:SS"},
            "end":       {"type": "STRING",  "description": "End time for trim: seconds or HH:MM:SS"},
            "timestamp": {"type": "STRING",  "description": "Timestamp for video frame extraction HH:MM:SS"},
            "column":    {"type": "STRING",  "description": "Column name for CSV filter/sort"},
            "value":     {"type": "STRING",  "description": "Filter value for CSV filter"},
            "condition": {"type": "STRING",  "description": "Filter condition: equals|contains|gt|lt"},
            "ascending": {"type": "BOOLEAN", "description": "Sort order for CSV sort (default: true)"},
            "save":      {"type": "BOOLEAN", "description": "Save result to file (default: true)"},
            "destination": {"type": "STRING", "description": "Output folder for archive extract"},
        },
        "required": []
    }
},
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Fatih, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "switch_persona",
        "description": (
            "Switches the active personality of the assistant. "
            "Call this when the user says a persona name to invoke it: "
            "'Tommy' = professional assistant, "
            "'Gibbs' = gruff NCIS Marine style, "
            "'Jack' = Captain Jack Sparrow humorous pirate. "
            "The session will restart with the new persona immediately."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "persona": {
                    "type": "STRING",
                    "description": "tommy | gibbs | jack"
                }
            },
            "required": ["persona"]
        }
    },
    {
        "name": "mick",
        "description": (
            "Interface with Mick, the Gmail agent. "
            "Use action='summary' to get Mick's inbox summary. "
            "Use action='reply' to pass the user's response to Mick's pending question (include user_reply). "
            "Use action='ignore_sender' with sender= to tell Mick to always ignore that sender. "
            "Use action='important_sender' with sender= to mark a sender as always important. "
            "Use action='ignore_keyword' with keyword= to auto-archive emails matching that keyword. "
            "Use action='pending' to check if Mick has open questions for the user. "
            "Route all user feedback about email handling through this tool."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":     {"type": "STRING", "description": "summary | reply | ignore_sender | important_sender | ignore_keyword | pending"},
                "user_reply": {"type": "STRING", "description": "The user's response to Mick's pending question"},
                "sender":     {"type": "STRING", "description": "Email address or domain to ignore/flag"},
                "keyword":    {"type": "STRING", "description": "Keyword to auto-archive"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "claude_dev",
        "description": (
            "Delegate a coding task to the Claude Dev Agent. "
            "Opens IntelliJ IDEA and Terminal in the repo, pulls latest code, "
            "then runs Claude Code CLI to complete the task. "
            "Claude can write code, run terminal commands, push to GitHub, "
            "create PRs, send Slack updates, and fetch GCP production logs. "
            "Use action='run' with task and repo_path. "
            "Use action='status' to check running tasks. "
            "Set create_pr=true to push and open a PR when done. "
            "Set slack_channel to post progress updates (e.g. '#dev-updates')."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":        {"type": "STRING",  "description": "run | status (default: run)"},
                "task":          {"type": "STRING",  "description": "What Claude should do (fix bug, add feature, debug prod issue, etc.)"},
                "repo_path":     {"type": "STRING",  "description": "Absolute path to the git repo folder"},
                "branch":        {"type": "STRING",  "description": "Git branch to work on (optional)"},
                "create_pr":     {"type": "BOOLEAN", "description": "Push and create a PR when done (default: false)"},
                "pr_title":      {"type": "STRING",  "description": "PR title if create_pr is true"},
                "slack_channel": {"type": "STRING",  "description": "Slack channel for progress updates (e.g. '#dev')"},
                "open_ide":      {"type": "BOOLEAN", "description": "Open IntelliJ and Terminal (default: true)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "integration_setup",
        "description": (
            "Manage app integrations: Gmail, Slack, GitHub, Notion, Linear, Jira, Google Calendar. "
            "Use action='status' to see what's connected. "
            "Use action='set' to save a credential (service + key + value). "
            "Use action='auth' to run OAuth flow for Gmail or test Slack connection. "
            "Use action='list' to show all available integrations."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":  {"type": "STRING", "description": "status | set | auth | list"},
                "service": {"type": "STRING", "description": "gmail | slack | github | notion | linear | jira | google_calendar"},
                "key":     {"type": "STRING", "description": "Credential key (e.g. google_client_id, slack_bot_token)"},
                "value":   {"type": "STRING", "description": "Credential value"},
            },
            "required": ["action"]
        }
    },
]

class JarvisLive:

    def __init__(self, ui: JarvisUI):
        self.ui                    = ui
        self.session               = None
        self.audio_in_queue        = None
        self.out_queue             = None
        self._loop                 = None
        self._is_speaking          = False
        self._speaking_lock        = threading.Lock()
        self.ui.on_text_command    = self._on_text_command
        self._turn_done_event: asyncio.Event | None = None
        self._switch_persona_name: str | None = None
        self._restart_flag         = threading.Event()
        self.ui.on_restart         = self._request_restart

        pm = get_permission_manager()
        pm.set_request_fn(self.ui.request_permission)

    def _check_permission(self, tool: str) -> bool:
        """Returns True if allowed to run. Speaks a denial message if blocked."""
        pm = get_permission_manager()
        if not pm.needs_permission(tool):
            return True
        if not pm.request(tool):
            self.speak(f"Sir, I need your permission to use {tool.replace('_', ' ')}. Access was denied.")
            return False
        return True

    def _on_text_command(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def _request_restart(self):
        """Called by the UI restart button — triggers a clean session reconnect."""
        self._restart_flag.set()
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._do_restart(), self._loop)

    async def _do_restart(self):
        raise _PersonaSwitchSignal(_get_active_persona())

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        parts = [time_ctx]
        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)

        persona    = _get_active_persona()
        voice_name = PERSONA_VOICES.get(persona, "Charon")

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": TOOL_DECLARATIONS}],
            session_resumption=types.SessionResumptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice_name
                    )
                )
            ),
        )

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        print(f"[JARVIS] 🔧 {name}  {args}")
        self.ui.set_state("THINKING")

        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop   = asyncio.get_event_loop()
        result = "Done."

        try:
            if name == "open_app":
                r = await loop.run_in_executor(None, lambda: open_app(parameters=args, response=None, player=self.ui))
                result = r or f"Opened {args.get('app_name')}."

            elif name == "weather_report":
                r = await loop.run_in_executor(None, lambda: weather_action(parameters=args, player=self.ui))
                result = r or "Weather delivered."

            elif name == "browser_control":
                if not await loop.run_in_executor(None, lambda: self._check_permission("browser_control")):
                    result = "Permission denied."
                else:
                    r = await loop.run_in_executor(None, lambda: browser_control(parameters=args, player=self.ui))
                    result = r or "Done."

            elif name == "file_controller":
                if not await loop.run_in_executor(None, lambda: self._check_permission("file_controller")):
                    result = "Permission denied."
                else:
                    r = await loop.run_in_executor(None, lambda: file_controller(parameters=args, player=self.ui))
                    result = r or "Done."

            elif name == "send_message":
                if not await loop.run_in_executor(None, lambda: self._check_permission("send_message")):
                    result = "Permission denied."
                else:
                    r = await loop.run_in_executor(None, lambda: send_message(parameters=args, response=None, player=self.ui, session_memory=None))
                    result = r or f"Message sent to {args.get('receiver')}."

            elif name == "reminder":
                r = await loop.run_in_executor(None, lambda: reminder(parameters=args, response=None, player=self.ui))
                result = r or "Reminder set."

            elif name == "youtube_video":
                r = await loop.run_in_executor(None, lambda: youtube_video(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "screen_process":
                threading.Thread(
                    target=screen_process,
                    kwargs={"parameters": args, "response": None,
                            "player": self.ui, "session_memory": None},
                    daemon=True
                ).start()
                result = "Vision module activated. Stay completely silent — vision module will speak directly."

            elif name == "computer_settings":
                r = await loop.run_in_executor(None, lambda: computer_settings(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "desktop_control":
                r = await loop.run_in_executor(None, lambda: desktop_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "code_helper":
                r = await loop.run_in_executor(None, lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "dev_agent":
                if not await loop.run_in_executor(None, lambda: self._check_permission("dev_agent")):
                    result = "Permission denied."
                else:
                    r = await loop.run_in_executor(None, lambda: dev_agent(parameters=args, player=self.ui, speak=self.speak))
                    result = r or "Done."

            elif name == "agent_task":
                from agent.executor import AgentExecutor
                r = await loop.run_in_executor(
                    None,
                    lambda: AgentExecutor().execute(goal=args.get("goal", ""), speak=None)
                )
                result = r or "Done."

            elif name == "web_search":
                r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."
            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                r = await loop.run_in_executor(
                    None,
                    lambda: file_processor(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Done."

            elif name == "computer_control":
                if not await loop.run_in_executor(None, lambda: self._check_permission("computer_control")):
                    result = "Permission denied."
                else:
                    r = await loop.run_in_executor(None, lambda: computer_control(parameters=args, player=self.ui))
                    result = r or "Done."

            elif name == "game_updater":
                r = await loop.run_in_executor(None, lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "flight_finder":
                r = await loop.run_in_executor(None, lambda: flight_finder(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "shutdown_jarvis":
                self.ui.write_log("SYS: Shutdown requested.")
                self.speak("Goodbye, sir.")
                def _shutdown():
                    import time, os
                    time.sleep(1)
                    os._exit(0)
                threading.Thread(target=_shutdown, daemon=True).start()

            elif name == "email_reader":
                if not await loop.run_in_executor(None, lambda: self._check_permission("email_reader")):
                    result = "Permission denied."
                else:
                    from actions.email_reader import email_reader
                    r = await loop.run_in_executor(
                        None, lambda: email_reader(parameters=args, player=self.ui, speak=self.speak)
                    )
                    result = r or "Done."

            elif name == "task_manager":
                from actions.task_manager import task_manager
                r = await loop.run_in_executor(
                    None, lambda: task_manager(parameters=args, player=self.ui)
                )
                self.ui.refresh_tasks()
                result = r or "Done."

            elif name == "system_control":
                if not await loop.run_in_executor(None, lambda: self._check_permission("system_control")):
                    result = "Permission denied."
                else:
                    from actions.system_control import system_control
                    r = await loop.run_in_executor(
                        None, lambda: system_control(parameters=args, player=self.ui, speak=self.speak)
                    )
                    result = r or "Done."

            elif name == "slack_reader":
                if not await loop.run_in_executor(None, lambda: self._check_permission("slack_reader")):
                    result = "Permission denied."
                else:
                    from actions.slack_reader import slack_reader
                    r = await loop.run_in_executor(
                        None, lambda: slack_reader(parameters=args, player=self.ui, speak=self.speak)
                    )
                    result = r or "Done."

            elif name == "switch_persona":
                persona = args.get("persona", "tommy").lower().strip()
                if persona not in ("tommy", "gibbs", "jack"):
                    result = f"Unknown persona '{persona}'. Available: tommy, gibbs, jack."
                else:
                    _set_active_persona(persona)
                    self._switch_persona_name = persona
                    self.ui.write_log(f"SYS: Switching to [{persona.upper()}]…")
                    result = f"Switching to {persona} now."

            elif name == "mick":
                action = args.get("action", "summary")
                from agents.mick.mick_agent import get_mick
                mick = get_mick()
                if action == "summary":
                    r = await loop.run_in_executor(None, mick.inbox_summary)
                    result = r or "Mick has nothing to report, sir."
                elif action == "reply":
                    user_reply = args.get("user_reply", "")
                    r = await loop.run_in_executor(None, lambda: mick.handle_user_reply(user_reply))
                    result = r or "Done."
                elif action == "ignore_sender":
                    sender = args.get("sender", "")
                    from agents.mick.preferences import add_ignored
                    add_ignored(sender)
                    result = f"Mick will ignore emails from {sender} going forward, sir."
                elif action == "important_sender":
                    sender = args.get("sender", "")
                    from agents.mick.preferences import add_important
                    add_important(sender)
                    result = f"Mick will always flag emails from {sender}, sir."
                elif action == "ignore_keyword":
                    kw = args.get("keyword", "")
                    from agents.mick.preferences import add_keyword_ignore
                    add_keyword_ignore(kw)
                    result = f"Mick will auto-archive emails containing \"{kw}\", sir."
                elif action == "pending":
                    q = mick.get_pending_question()
                    result = q if q else "Mick has no open questions at the moment, sir."
                else:
                    result = f"Unknown Mick action: {action}"

            elif name == "claude_dev":
                if not await loop.run_in_executor(None, lambda: self._check_permission("claude_dev")):
                    result = "Permission denied."
                else:
                    from actions.claude_dev import claude_dev
                    r = await loop.run_in_executor(
                        None, lambda: claude_dev(parameters=args, player=self.ui, speak=self.speak)
                    )
                    result = r or "Done."

            elif name == "integration_setup":
                from actions.integration_setup import integration_setup
                r = await loop.run_in_executor(
                    None, lambda: integration_setup(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Done."

            else:
                result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            _log.error("Tool '%s' raised an exception", name, exc_info=True)
            self.speak_error(name, e)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[JARVIS] 📤 {name} → {str(result)[:80]}")
        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(media=msg)

    async def _listen_audio(self):
        print("[JARVIS] 🎤 Mic started")
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            with self._speaking_lock:
                jarvis_speaking = self._is_speaking
            if not jarvis_speaking and not self.ui.muted:
                data = indata.tobytes()
                loop.call_soon_threadsafe(
                    self.out_queue.put_nowait,
                    {"data": data, "mime_type": "audio/pcm"}
                )

        try:
            with sd.InputStream(
                samplerate=SEND_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                callback=callback,
            ):
                print("[JARVIS] 🎤 Mic stream open")
                while True:
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[JARVIS] ❌ Mic: {e}")
            raise

    async def _receive_audio(self):
        print("[JARVIS] 👂 Recv started")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():

                    if response.data:
                        if self._turn_done_event and self._turn_done_event.is_set():
                            self._turn_done_event.clear()
                        self.audio_in_queue.put_nowait(response.data)

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = _clean_transcript(sc.output_transcription.text)
                            if txt:
                                out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = _clean_transcript(sc.input_transcription.text)
                            if txt:
                                in_buf.append(txt)

                        if sc.turn_complete:
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"Captain Jack: {full_out}")
                            out_buf = []

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[JARVIS] 📞 {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )
                        # Persona switch requested — exit session to reconnect with new persona
                        if self._switch_persona_name:
                            raise _PersonaSwitchSignal(self._switch_persona_name)
        except _PersonaSwitchSignal:
            raise
        except Exception as e:
            _log.error("Recv loop error: %s", e, exc_info=True)
            raise

    async def _play_audio(self):
        print("[JARVIS] 🔊 Play started")

        stream = sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
        )
        stream.start()

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(),
                        timeout=0.1
                    )
                except asyncio.TimeoutError:
                    if (
                        self._turn_done_event
                        and self._turn_done_event.is_set()
                        and self.audio_in_queue.empty()
                    ):
                        self.set_speaking(False)
                        self._turn_done_event.clear()
                    continue
                self.set_speaking(True)
                await asyncio.to_thread(stream.write, chunk)
        except Exception as e:
            print(f"[JARVIS] ❌ Play: {e}")
            raise
        finally:
            self.set_speaking(False)
            stream.stop()
            stream.close()

    async def run(self):
        client = genai.Client(
            api_key=_get_api_key(),
            http_options={"api_version": "v1beta"}
        )

        while True:
            persona_switch = False
            try:
                persona    = _get_active_persona()
                print(f"[JARVIS] 🔌 Connecting as [{persona.upper()}]...")
                self.ui.set_state("THINKING")
                config = self._build_config()

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session              = session
                    self._loop               = asyncio.get_event_loop()
                    self.audio_in_queue      = asyncio.Queue()
                    self.out_queue           = asyncio.Queue(maxsize=10)
                    self._turn_done_event    = asyncio.Event()
                    self._switch_persona_name = None

                    print(f"[JARVIS] ✅ Connected as [{persona.upper()}].")
                    self.ui.set_state("LISTENING")
                    self.ui.write_log(f"SYS: [{persona.upper()}] online.")
                    self.ui.set_persona(persona)

                    # Wire background agent notifications through the active persona's voice
                    from agents.watchers.watcher_manager import get_watcher_manager
                    from agents.claude_dev.claude_dev_agent import get_claude_dev
                    get_watcher_manager().set_speak(self.speak)
                    get_claude_dev().set_speak(self.speak)

                    greeting = PERSONA_GREETINGS.get(persona,
                        "You just came online. Greet the user in one short sentence.")
                    await session.send_client_content(
                        turns={"parts": [{"text": greeting}]},
                        turn_complete=True,
                    )

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())

            except _PersonaSwitchSignal as sig:
                persona_switch = True
                print(f"[JARVIS] 🔄 Persona switch → [{sig.persona.upper()}]")
            except Exception as e:
                _log.error("Session error: %s", e, exc_info=True)

        self.set_speaking(False)
        self.ui.set_state("THINKING")
        get_permission_manager().reset()
        if persona_switch:
            print(f"[JARVIS] ⚡ Restarting immediately for persona switch...")
            await asyncio.sleep(0.5)
        else:
            print("[JARVIS] 🔄 Reconnecting in 3s...")
            await asyncio.sleep(3)

def main():
    ui = JarvisUI("")

    def runner():
        ui.wait_for_api_key()

        # Start background watcher agents
        from agents.watchers.watcher_manager import get_watcher_manager
        get_watcher_manager().start_all()

        jarvis = JarvisLive(ui)
        try:
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")
            get_watcher_manager().stop_all()

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()

if __name__ == "__main__":
    main()