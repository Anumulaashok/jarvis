
from __future__ import annotations

import asyncio
import concurrent.futures
import os
import platform
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Optional

from playwright.async_api import (
    async_playwright,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeout,
)
_OS = platform.system()   # "Windows" | "Darwin" | "Linux"


# ─────────────────────────────────────────────────────────────────────────────
# macOS Chrome helper — controls the ALREADY-RUNNING Chrome via AppleScript.
# Never opens a new Chrome window or touches any profile directory.
# ─────────────────────────────────────────────────────────────────────────────

def _osa(*lines: str, timeout: int = 15) -> str:
    """Run an AppleScript block and return stdout."""
    args = ["osascript"]
    for line in lines:
        args += ["-e", line]
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip()


def _osa_js(js: str, timeout: int = 15) -> str:
    """Execute JavaScript in the active Chrome tab via AppleScript."""
    safe = js.replace('"', '\\"')
    return _osa(
        'tell application "Google Chrome"',
        'tell active tab of front window',
        f'execute javascript "{safe}"',
        'end tell',
        'end tell',
        timeout=timeout,
    )


class MacOSChromeSession:
    """
    Drop-in replacement for _BrowserSession on macOS when Chrome is the target.
    All operations drive the existing running Chrome through AppleScript.
    """

    browser_name = "chrome"

    def start(self):
        pass  # nothing to start — Chrome is already running

    def close(self):
        pass  # don't close the user's Chrome

    def run(self, result):
        """Compatibility shim — MacOSChromeSession methods return values directly."""
        return result

    # ── navigation ──────────────────────────────────────────────────────────

    def go_to(self, url: str) -> str:
        url = _normalize_url(url)
        try:
            _osa(
                'tell application "Google Chrome"',
                'activate',
                f'open location "{url}"',
                'end tell',
            )
            import time; time.sleep(3)
            current = _osa(
                'tell application "Google Chrome"',
                'get URL of active tab of front window',
                'end tell',
            )
            # Always read the page so the AI knows the current state
            page_text = self.get_text()
            state     = _summarise_page_state(current or url, page_text)
            return f"Opened: {current or url}\nPage state: {state}"
        except Exception as e:
            return f"Could not open {url}: {e}"

    def search(self, query: str, engine: str = "google") -> str:
        engines = {
            "google":     "https://www.google.com/search?q=",
            "bing":       "https://www.bing.com/search?q=",
            "duckduckgo": "https://duckduckgo.com/?q=",
        }
        base = engines.get(engine.lower(), engines["google"])
        return self.go_to(base + query.replace(" ", "+"))

    def get_url(self) -> str:
        try:
            return _osa(
                'tell application "Google Chrome"',
                'get URL of active tab of front window',
                'end tell',
            )
        except Exception as e:
            return f"Error: {e}"

    # ── content ─────────────────────────────────────────────────────────────

    def get_text(self) -> str:
        try:
            # Use clipboard (select-all + copy) — works without JS Apple Events permission
            result = _osa(
                'tell application "Google Chrome"',
                'activate',
                'end tell',
                'delay 0.4',
                'tell application "System Events"',
                'keystroke "a" using {command down}',
                'end tell',
                'delay 0.3',
                'tell application "System Events"',
                'keystroke "c" using {command down}',
                'end tell',
                'delay 0.3',
                'return (do shell script "pbpaste")',
                timeout=15,
            )
            return result[:6000] if result else "No text found."
        except Exception as e:
            return f"Error reading page: {e}"

    # ── interaction ─────────────────────────────────────────────────────────

    def click(self, selector: str = None, text: str = None) -> str:
        try:
            if text:
                js = (
                    f"var els=document.querySelectorAll('a,button,[role=button],[role=link]');"
                    f"for(var i=0;i<els.length;i++){{"
                    f"if(els[i].innerText.toLowerCase().includes('{text.lower()}'))"
                    f"{{els[i].click();break;}}}}"
                )
            elif selector:
                js = f"document.querySelector('{selector}').click();"
            else:
                return "No selector or text provided."
            _osa_js(js)
            return f"Clicked: {text or selector}"
        except Exception as e:
            return f"Click error: {e}"

    def smart_click(self, description: str) -> str:
        result = self.click(text=description)
        import time; time.sleep(1.5)
        # Re-read page so AI knows what happened after the click
        page_text = self.get_text()
        url       = self.get_url()
        state     = _summarise_page_state(url, page_text)
        return f"{result} → Page now: {state}"

    def type_text(self, selector: str = None, text: str = "", clear_first: bool = True) -> str:
        try:
            clear = f"document.querySelector('{selector}').value='';" if clear_first and selector else ""
            target = f"document.querySelector('{selector}')" if selector else "document.activeElement"
            js = f"{clear}{target}.focus();{target}.value={repr(text)};"
            _osa_js(js)
            return "Text typed."
        except Exception as e:
            return f"Type error: {e}"

    def smart_type(self, description: str, text: str) -> str:
        js = (
            f"var inp=document.querySelector('input[placeholder*=\"{description}\" i],"
            f"input[aria-label*=\"{description}\" i],textarea[placeholder*=\"{description}\" i]');"
            f"if(inp){{inp.focus();inp.value={repr(text)};}}"
        )
        try:
            _osa_js(js)
            return f"Typed into: {description}"
        except Exception as e:
            return f"Smart type error: {e}"

    def scroll(self, direction: str = "down", amount: int = 500) -> str:
        y = amount if direction == "down" else -amount
        x = amount if direction == "right" else (-amount if direction == "left" else 0)
        try:
            _osa_js(f"window.scrollBy({x},{y});")
            return f"Scrolled {direction}."
        except Exception as e:
            return f"Scroll error: {e}"

    def press(self, key: str) -> str:
        key_map = {"Enter": "return", "Escape": "escape", "Tab": "tab",
                   "ArrowUp": "up arrow", "ArrowDown": "down arrow",
                   "ArrowLeft": "left arrow", "ArrowRight": "right arrow",
                   "Backspace": "delete"}
        osa_key = key_map.get(key, key.lower())
        try:
            _osa(
                'tell application "System Events"',
                f'key code 0',   # placeholder — use keystroke for letters
                'end tell',
            )
            _osa(
                'tell application "System Events"',
                f'keystroke "{osa_key}"',
                'end tell',
            ) if len(osa_key) == 1 else _osa(
                'tell application "Google Chrome"',
                'activate',
                'end tell',
            )
            return f"Pressed: {key}"
        except Exception as e:
            return f"Press error: {e}"

    # ── tabs ────────────────────────────────────────────────────────────────

    def new_tab(self, url: str = "") -> str:
        try:
            _osa(
                'tell application "Google Chrome"',
                'tell front window',
                'make new tab',
                'end tell',
                'end tell',
            )
            import time; time.sleep(0.5)
            if url:
                return self.go_to(url)
            return "New tab opened."
        except Exception as e:
            return f"New tab error: {e}"

    def close_tab(self) -> str:
        try:
            _osa(
                'tell application "Google Chrome"',
                'tell front window',
                'close active tab',
                'end tell',
                'end tell',
            )
            return "Tab closed."
        except Exception as e:
            return f"Close tab error: {e}"

    def back(self) -> str:
        try:
            _osa_js("history.back();")
            return "Navigated back."
        except Exception as e:
            return f"Back error: {e}"

    def forward(self) -> str:
        try:
            _osa_js("history.forward();")
            return "Navigated forward."
        except Exception as e:
            return f"Forward error: {e}"

    def reload(self) -> str:
        try:
            _osa_js("location.reload();")
            return "Page reloaded."
        except Exception as e:
            return f"Reload error: {e}"

    def close_browser(self) -> str:
        return "Cannot close your Chrome, sir. Use 'close tab' instead."

    def screenshot(self, path: str = None) -> str:
        save = path or str(Path.home() / "Desktop" / "jarvis_screenshot.png")
        try:
            subprocess.run(["screencapture", "-w", save], timeout=15)
            return f"Screenshot saved: {save}"
        except Exception as e:
            return f"Screenshot error: {e}"

    def fill_form(self, fields: dict) -> str:
        results = []
        for selector, value in fields.items():
            r = self.type_text(selector, str(value))
            results.append(f"✓ {selector}" if "typed" in r.lower() else f"✗ {selector}")
        return "Form filled: " + ", ".join(results)

    # ── profile switching ────────────────────────────────────────────────────

    def list_profiles(self) -> str:
        profiles = _get_chrome_profiles()
        if not profiles:
            return "No Chrome profiles found."
        lines = [f"  {k}: {v['name']}" + (f" ({v['email']})" if v.get("email") else "")
                 for k, v in profiles.items()]
        return "Chrome profiles:\n" + "\n".join(lines)

    def switch_profile(self, name_or_email: str) -> str:
        profiles = _get_chrome_profiles()
        target_key = None
        q = name_or_email.lower().strip()
        for key, info in profiles.items():
            if q in info.get("name", "").lower() or q in info.get("email", "").lower():
                target_key = key
                break
        if not target_key:
            names = [f"{v['name']}" + (f"/{v['email']}" if v.get("email") else "") for v in profiles.values()]
            return f"Profile '{name_or_email}' not found. Available: {', '.join(names)}"
        import subprocess
        subprocess.Popen(["open", "-a", "Google Chrome", "--args",
                          f"--profile-directory={target_key}"])
        import time; time.sleep(2)
        return f"Switched to Chrome profile: {profiles[target_key]['name']}"

    # ── self-healing ─────────────────────────────────────────────────────────

    def heal(self, goal: str, speak=None, context: str = "") -> str:
        """
        When stuck, take a screenshot, analyse with Gemini Vision, try to fix,
        and remember the solution for next time.
        """
        try:
            from agents.browser_agent.self_healer import heal as _heal
            return _heal(goal=goal, session=self, speak=speak, context=context)
        except Exception as e:
            return f"Healer error: {e}"

    def list_sessions(self) -> str:
        return "Using existing Chrome window (macOS AppleScript mode)."


def _summarise_page_state(url: str, page_text: str) -> str:
    """
    Quickly classify what's on screen so the AI knows exactly where it is
    and what to do next — no screenshot needed, works from page text alone.
    """
    text  = page_text.lower()[:3000]
    url_l = url.lower()

    # ── Known page patterns → instant classification ──────────────────────
    if any(x in text for x in ["sign in", "log in", "enter your password", "enter your email"]):
        return "LOGIN PAGE — user must sign in before proceeding."

    if any(x in text for x in ["verify it's you", "two-step verification", "enter the code", "verification code"]):
        return "2FA/OTP PAGE — waiting for verification code from user."

    if any(x in text for x in ["add a payment method", "billing account", "add credit or debit card",
                                 "card number", "expiry date", "cvv"]):
        return "PAYMENT/BILLING FORM — visible fields for card/billing details. Ask user for each field."

    if "captcha" in text or "i'm not a robot" in text or "verify you are human" in text:
        return "CAPTCHA PAGE — user must solve captcha manually."

    if any(x in text for x in ["404", "page not found", "this page doesn't exist"]):
        return "ERROR 404 — page not found. Try a different URL."

    if any(x in text for x in ["access denied", "403 forbidden", "you don't have permission"]):
        return "ACCESS DENIED — no permission to view this page."

    if any(x in text for x in ["enable billing", "upgrade your account", "free trial", "choose a plan"]):
        return "BILLING UPGRADE PAGE — option to enable/upgrade billing is visible."

    if "console.cloud.google.com" in url_l:
        if "billing" in url_l:
            return "GOOGLE CLOUD BILLING PAGE — " + _extract_visible_buttons(page_text)
        if "apis" in url_l:
            return "GOOGLE CLOUD APIS PAGE — " + _extract_visible_buttons(page_text)
        return "GOOGLE CLOUD CONSOLE — " + _extract_visible_buttons(page_text)

    if "accounts.google.com" in url_l:
        return "GOOGLE ACCOUNT PAGE — likely sign-in or OAuth consent."

    if "mail.google.com" in url_l:
        return "GMAIL INBOX — " + _extract_visible_buttons(page_text)

    if "slack.com" in url_l:
        return "SLACK — " + _extract_visible_buttons(page_text)

    # ── Generic fallback: extract what's visible ───────────────────────────
    buttons = _extract_visible_buttons(page_text)
    heading = _first_heading(page_text)
    return f"{heading}. Visible actions: {buttons}"


def _extract_visible_buttons(text: str) -> str:
    """Pull out likely button/link labels from page text."""
    import re
    # Look for short capitalised phrases that look like buttons
    candidates = re.findall(r'\b([A-Z][A-Za-z ]{2,30})\b', text[:2000])
    seen, result = set(), []
    for c in candidates:
        c = c.strip()
        if c not in seen and len(c) > 3:
            seen.add(c)
            result.append(c)
        if len(result) >= 6:
            break
    return ", ".join(result) if result else "no clear buttons detected"


def _first_heading(text: str) -> str:
    """Return first non-trivial line as a page title."""
    for line in text.splitlines():
        line = line.strip()
        if len(line) > 8 and len(line) < 80:
            return line
    return "page loaded"


def _get_chrome_profiles() -> dict:
    """Read Chrome Local State and return {dir_key: {name, email}} for all profiles."""
    import json
    local_state = Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "Local State"
    try:
        data = json.loads(local_state.read_text(encoding="utf-8"))
        info_cache = data.get("profile", {}).get("info_cache", {})
        return {
            key: {
                "name":  info.get("name", key),
                "email": info.get("user_name", ""),
            }
            for key, info in info_cache.items()
        }
    except Exception as e:
        print(f"[Browser] profile read error: {e}")
        return {}


def _normalize_url(url: str) -> str:
    """
    Bare words like "instagram" → "https://instagram.com"
    Domains like "instagram.com" → "https://instagram.com"
    Full URLs pass through unchanged.
    """
    url = url.strip()
    if not url:
        return "about:blank"
    if "://" in url:
        return url
    # No dot at all → assume .com  (e.g. "instagram" → "instagram.com")
    if "." not in url:
        url = url + ".com"
    return "https://" + url


def _user_agent() -> str:
    if _OS == "Windows":
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    if _OS == "Darwin":
        return (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    return (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )


def _real_profile_dir(browser: str) -> str:
    home  = Path.home()
    local = os.environ.get("LOCALAPPDATA", "")
    roam  = os.environ.get("APPDATA", "")

    candidates: list[Path] = []

    if _OS == "Windows":
        m = {
            "chrome":   [Path(local) / "Google"          / "Chrome"          / "User Data"],
            "edge":     [Path(local) / "Microsoft"        / "Edge"            / "User Data"],
            "brave":    [Path(local) / "BraveSoftware"    / "Brave-Browser"   / "User Data"],
            "vivaldi":  [Path(local) / "Vivaldi"          / "User Data"],
            "opera":    [Path(roam)  / "Opera Software"   / "Opera Stable",
                         Path(local) / "Opera Software"   / "Opera Stable"],
            "operagx":  [Path(roam)  / "Opera Software"   / "Opera GX Stable",
                         Path(local) / "Opera Software"   / "Opera GX Stable"],
        }
        candidates = m.get(browser, [])

    elif _OS == "Darwin":
        lib = home / "Library" / "Application Support"
        m = {
            "chrome":   [lib / "Google"             / "Chrome"],
            "edge":     [lib / "Microsoft Edge"],
            "brave":    [lib / "BraveSoftware"       / "Brave-Browser"],
            "vivaldi":  [lib / "Vivaldi"],
            "opera":    [lib / "com.operasoftware.Opera"],
            "operagx":  [lib / "com.operasoftware.OperaGX"],
        }
        candidates = m.get(browser, [])

    elif _OS == "Linux":
        cfg = home / ".config"
        m = {
            "chrome":   [cfg / "google-chrome", cfg / "chromium"],
            "edge":     [cfg / "microsoft-edge"],
            "brave":    [cfg / "BraveSoftware" / "Brave-Browser"],
            "vivaldi":  [cfg / "vivaldi"],
            "opera":    [cfg / "opera"],
            "operagx":  [cfg / "opera-gx"],
        }
        candidates = m.get(browser, [])

    for p in candidates:
        if p.exists():
            print(f"[Browser] ✅ Real profile found for {browser}: {p}")
            return str(p)

    fallback = home / ".jarvis_profiles" / browser
    fallback.mkdir(parents=True, exist_ok=True)
    print(f"[Browser] ⚠️  Real profile not found for {browser}, using: {fallback}")
    return str(fallback)

def _firefox_profile_dir() -> Optional[str]:
    home = Path.home()

    if _OS == "Windows":
        base = Path(os.environ.get("APPDATA", "")) / "Mozilla" / "Firefox"
    elif _OS == "Darwin":
        base = home / "Library" / "Application Support" / "Firefox"
    else:
        base = home / ".mozilla" / "firefox"

    ini = base / "profiles.ini"
    if not ini.exists():
        return None

    current: dict[str, str] = {}
    default_path: Optional[str] = None

    for line in ini.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line.startswith("["):
            p = current.get("Path", "")
            if p and current.get("Default") == "1":
                is_rel = current.get("IsRelative", "1") == "1"
                default_path = str(base / p) if is_rel else p
            current = {}
        elif "=" in line:
            k, _, v = line.partition("=")
            current[k.strip()] = v.strip()

    p = current.get("Path", "")
    if p and current.get("Default") == "1":
        is_rel = current.get("IsRelative", "1") == "1"
        default_path = str(base / p) if is_rel else p

    if default_path and Path(default_path).exists():
        print(f"[Browser] Firefox real profile: {default_path}")
        return default_path
    return None

def _find_opera_windows() -> Optional[str]:
    local  = os.environ.get("LOCALAPPDATA", "")
    prog   = os.environ.get("PROGRAMFILES", "")
    prog86 = os.environ.get("PROGRAMFILES(X86)", "")

    candidates = [
        Path(local)  / "Programs" / "Opera"    / "opera.exe",
        Path(local)  / "Programs" / "Opera GX" / "opera.exe",
        Path(prog)   / "Opera"    / "opera.exe",
        Path(prog86) / "Opera"    / "opera.exe",
    ]
    for p in candidates:
        if p.exists():
            print(f"[Browser] Opera found at: {p}")
            return str(p)

    try:
        import winreg
        keys = [
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\opera.exe",
            r"SOFTWARE\Clients\StartMenuInternet\OperaStable\shell\open\command",
            r"SOFTWARE\Clients\StartMenuInternet\OperaGXStable\shell\open\command",
            r"SOFTWARE\Clients\StartMenuInternet\opera\shell\open\command",
        ]
        for key_path in keys:
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    k   = winreg.OpenKey(hive, key_path)
                    val = winreg.QueryValue(k, None)
                    winreg.CloseKey(k)
                    exe = val.strip().strip('"').split('"')[0].split(" --")[0].strip()
                    if exe and Path(exe).exists():
                        print(f"[Browser] Opera found via registry: {exe}")
                        return exe
                except Exception:
                    continue
    except Exception:
        pass

    return shutil.which("opera") or None

def _find_exe_windows(prog_name: str) -> Optional[str]:
    try:
        import winreg
        paths_to_try = [
            rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{prog_name}.exe",
            rf"SOFTWARE\Clients\StartMenuInternet\{prog_name}\shell\open\command",
        ]
        for key_path in paths_to_try:
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    k   = winreg.OpenKey(hive, key_path)
                    val = winreg.QueryValue(k, None)
                    winreg.CloseKey(k)
                    exe = val.strip().strip('"').split('"')[0].split(" --")[0].strip()
                    if exe and Path(exe).exists():
                        return exe
                except Exception:
                    continue
    except Exception:
        pass
    return None

_BROWSER_SPECS: dict[str, dict] = {
    "Windows": {
        "chrome":   {"engine": "chromium", "channel": "chrome",  "bins": []},
        "edge":     {"engine": "chromium", "channel": "msedge",  "bins": []},
        "firefox":  {"engine": "firefox",  "channel": None,      "bins": ["firefox.exe"]},
        "opera":    {"engine": "chromium", "channel": None,      "bins": ["opera.exe"],  "special": "opera_windows"},
        "operagx":  {"engine": "chromium", "channel": None,      "bins": [],             "special": "opera_windows"},
        "brave":    {"engine": "chromium", "channel": None,      "bins": ["brave.exe"]},
        "vivaldi":  {"engine": "chromium", "channel": None,      "bins": ["vivaldi.exe"]},
        "safari":   None,
    },
    "Darwin": {
        "chrome":   {"engine": "chromium", "channel": "chrome",  "bins": []},
        "edge":     {"engine": "chromium", "channel": "msedge",  "bins": ["microsoft-edge"]},
        "firefox":  {"engine": "firefox",  "channel": None,      "bins": ["firefox"]},
        "opera":    {"engine": "chromium", "channel": None,      "bins": ["opera"]},
        "operagx":  {"engine": "chromium", "channel": None,      "bins": ["opera"]},
        "brave":    {"engine": "chromium", "channel": None,      "bins": ["brave browser", "brave"]},
        "vivaldi":  {"engine": "chromium", "channel": None,      "bins": ["vivaldi"]},
        "safari":   {"engine": "webkit",   "channel": None,      "bins": []},
    },
    "Linux": {
        "chrome":   {"engine": "chromium", "channel": None,
                     "bins": ["google-chrome", "google-chrome-stable", "chromium-browser", "chromium"]},
        "edge":     {"engine": "chromium", "channel": None,
                     "bins": ["microsoft-edge", "microsoft-edge-stable"]},
        "firefox":  {"engine": "firefox",  "channel": None, "bins": ["firefox"]},
        "opera":    {"engine": "chromium", "channel": None, "bins": ["opera", "opera-stable"]},
        "operagx":  {"engine": "chromium", "channel": None, "bins": ["opera", "opera-stable"]},
        "brave":    {"engine": "chromium", "channel": None, "bins": ["brave-browser", "brave"]},
        "vivaldi":  {"engine": "chromium", "channel": None, "bins": ["vivaldi-stable", "vivaldi"]},
        "safari":   None,
    },
}

_ALIASES: dict[str, str] = {
    "google chrome":   "chrome",
    "google-chrome":   "chrome",
    "microsoft edge":  "edge",
    "ms edge":         "edge",
    "msedge":          "edge",
    "mozilla firefox": "firefox",
    "opera gx":        "operagx",
    "opera_gx":        "operagx",
}


def _resolve_browser(name: str) -> dict | None:
    name   = _ALIASES.get(name.lower().strip(), name.lower().strip())
    os_map = _BROWSER_SPECS.get(_OS, {})
    spec   = os_map.get(name)
    if spec is None:
        return None

    engine  = spec["engine"]
    channel = spec.get("channel")
    bins    = spec.get("bins", [])
    exe     = None

    if spec.get("special") == "opera_windows":
        exe = _find_opera_windows()
        if not exe:
            print(f"[Browser] ⚠️  Opera executable not found on Windows.")
        return {"engine": engine, "exe": exe, "channel": channel}

    for b in bins:
        found = shutil.which(b)
        if found:
            exe = found
            break

    if not exe and _OS == "Darwin":
        app_names = {
            "chrome":  ["Google Chrome.app"],
            "edge":    ["Microsoft Edge.app"],
            "firefox": ["Firefox.app"],
            "opera":   ["Opera.app", "Opera GX.app"],
            "brave":   ["Brave Browser.app"],
            "vivaldi": ["Vivaldi.app"],
        }
        for app in app_names.get(name, []):
            app_dir = Path("/Applications") / app / "Contents" / "MacOS"
            if app_dir.exists():
                found_bins = list(app_dir.iterdir())
                if found_bins:
                    exe = str(found_bins[0])
                    break

    if not exe and _OS == "Windows" and not channel:
        exe = _find_exe_windows(name)

    return {"engine": engine, "exe": exe, "channel": channel}


def _detect_default_browser() -> str:
    try:
        if _OS == "Windows":
            import winreg
            k = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\Shell\Associations"
                r"\UrlAssociations\http\UserChoice",
            )
            prog_id = winreg.QueryValueEx(k, "ProgId")[0].lower()
            winreg.CloseKey(k)
            for kw in ("edge", "firefox", "opera", "brave", "vivaldi", "chrome"):
                if kw in prog_id:
                    return kw
        elif _OS == "Darwin":
            out = subprocess.run(
                ["defaults", "read",
                 "com.apple.LaunchServices/com.apple.launchservices.secure",
                 "LSHandlers"],
                capture_output=True, text=True, timeout=5,
            ).stdout.lower()
            for kw in ("firefox", "opera", "brave", "vivaldi", "safari", "chrome", "edge"):
                if kw in out:
                    return kw
        elif _OS == "Linux":
            out = subprocess.run(
                ["xdg-settings", "get", "default-web-browser"],
                capture_output=True, text=True, timeout=5,
            ).stdout.lower()
            for kw in ("firefox", "opera", "brave", "vivaldi", "chrome", "edge"):
                if kw in out:
                    return kw
    except Exception:
        pass
    return "chrome"


class _BrowserSession:
    """
    Bir tarayıcı örneği için tam oturum.
    Tüm tarayıcılar launch_persistent_context ile gerçek profil üzerinde açılır.
    """

    def __init__(self, browser_name: str):
        self.browser_name = browser_name
        self._spec        = _resolve_browser(browser_name)

        self._loop:    asyncio.AbstractEventLoop | None = None
        self._thread:  threading.Thread | None          = None
        self._ready    = threading.Event()

        self._pw:      Playwright     | None = None
        self._context: BrowserContext | None = None
        self._page:    Page           | None = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name=f"BrowserThread-{self.browser_name}",
        )
        self._thread.start()
        self._ready.wait(timeout=20)

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._async_init())
        self._ready.set()
        self._loop.run_forever()

    async def _async_init(self):
        self._pw = await async_playwright().start()

    def run(self, coro, timeout: int = 60) -> str:
        if not self._loop:
            raise RuntimeError(f"Session for '{self.browser_name}' not started.")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def close(self):
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._async_close(), self._loop).result(10)

    async def _async_close(self):
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass
        self._context = self._page = None

    async def _launch(self):
        """
        Tarayıcıyı gerçek kullanıcı profiliyle başlatır.
        Context zaten açıksa hiçbir şey yapmaz.
        """
        if self._context is not None:
            return

        if self._spec is None:
            raise RuntimeError(
                f"'{self.browser_name}' bu platformda ({_OS}) desteklenmiyor."
            )

        engine_name = self._spec["engine"]
        exe         = self._spec["exe"]
        channel     = self._spec["channel"]
        engine_obj  = getattr(self._pw, engine_name)

        if engine_name == "firefox":
            profile = _firefox_profile_dir() or str(
                Path.home() / ".jarvis_profiles" / "firefox"
            )
            kwargs: dict = {
                "headless":    False,
                "slow_mo":     0,
                "viewport":    None,
                "no_viewport": True,
            }
            if exe:
                kwargs["executable_path"] = exe
            try:
                self._context = await engine_obj.launch_persistent_context(profile, **kwargs)
            except Exception as e:
                print(f"[Browser] Firefox real profile failed ({e}), using JARVIS profile")
                jarvis = str(Path.home() / ".jarvis_profiles" / "firefox_jarvis")
                Path(jarvis).mkdir(parents=True, exist_ok=True)
                self._context = await engine_obj.launch_persistent_context(jarvis, **kwargs)

            await asyncio.sleep(0.5)  
            self._page = await self._context.new_page()
            print(f"[Browser] ✅ Firefox launched")
            return

        if engine_name == "webkit":
            safari_profile = str(Path.home() / ".jarvis_profiles" / "safari")
            Path(safari_profile).mkdir(parents=True, exist_ok=True)
            kwargs = {
                "headless":    False,
                "slow_mo":     0,
                "viewport":    None,
                "no_viewport": True,
            }
            self._context = await engine_obj.launch_persistent_context(safari_profile, **kwargs)
            await asyncio.sleep(0.5)
            self._page = await self._context.new_page()
            print(f"[Browser] ✅ Safari launched")
            return

        profile = _real_profile_dir(self.browser_name)

        kwargs = {
            "headless":    False,
            "slow_mo":     0,
            "viewport":    None,
            "no_viewport": True,
            "args": [
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--disable-default-apps",
                "--no-default-browser-check",
            ],
        }

        if exe:
            kwargs["executable_path"] = exe
        elif channel:
            kwargs["channel"] = channel

        label = (
            f"{self.browser_name}"
            + (f"/{channel}" if channel else "")
            + (f" @ {exe}" if exe else "")
        )

        try:
            self._context = await engine_obj.launch_persistent_context(profile, **kwargs)
            await asyncio.sleep(0.5) 
            self._page = await self._context.new_page()
            print(f"[Browser] ✅ Launched [{label}] profile={profile}")
            return
        except Exception as e:
            print(f"[Browser] ⚠️  Real profile failed for {label}: {e}")

        jarvis_profile = str(Path.home() / ".jarvis_profiles" / self.browser_name)
        Path(jarvis_profile).mkdir(parents=True, exist_ok=True)
        print(f"[Browser] Retrying with JARVIS profile: {jarvis_profile}")

        try:
            self._context = await engine_obj.launch_persistent_context(jarvis_profile, **kwargs)
            await asyncio.sleep(0.5)
            self._page = await self._context.new_page()
            print(f"[Browser] ✅ Launched [{label}] with JARVIS profile")
        except Exception as e2:
            raise RuntimeError(f"Could not launch {self.browser_name}: {e2}") from e2


    async def _get_page(self) -> Page:
        await self._launch()
        # If somehow page got closed, open a fresh one
        if self._page is None or self._page.is_closed():
            self._page = await self._context.new_page()
            await asyncio.sleep(0.2)
        return self._page

    async def go_to(self, url: str) -> str:

        url      = _normalize_url(url)
        page     = await self._get_page()
        prev_url = page.url

        async def _do_goto(p: Page) -> str:
            """Attempt navigation and return the resulting URL (may still be blank)."""
            try:
                await p.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(0.3)
            except PlaywrightTimeout:
                pass   # page may have partially loaded — check URL below
            except Exception as e:
                print(f"[Browser] goto exception (non-fatal): {e}")
            return p.url

        result_url = await _do_goto(page)

        if result_url in ("about:blank", "", None, prev_url) and prev_url in ("about:blank", "", None):
            print(f"[Browser] Still blank after goto — retrying on new tab: {url}")
            try:
                new_page   = await self._context.new_page()
                self._page = new_page
                result_url = await _do_goto(new_page)
            except Exception as e:
                print(f"[Browser] New-tab retry failed: {e}")

        if result_url and result_url not in ("about:blank", "", None):
            return f"Opened: {result_url}"
        return f"Could not open: {url}"

    async def search(self, query: str, engine: str = "google") -> str:
        _engines = {
            "google":     "https://www.google.com/search?q=",
            "bing":       "https://www.bing.com/search?q=",
            "duckduckgo": "https://duckduckgo.com/?q=",
            "yandex":     "https://yandex.com/search/?text=",
        }
        base = _engines.get(engine.lower(), _engines["google"])
        return await self.go_to(base + query.replace(" ", "+"))

    async def click(self, selector: str = None, text: str = None) -> str:
        page = await self._get_page()
        try:
            if text:
                await page.get_by_text(text, exact=False).first.click(timeout=8_000)
                return f"Clicked text: '{text}'"
            if selector:
                await page.click(selector, timeout=8_000)
                return f"Clicked selector: {selector}"
            return "No selector or text provided."
        except PlaywrightTimeout:
            return "Element not found (timeout)."
        except Exception as e:
            return f"Click error: {e}"

    async def type_text(self, selector: str = None, text: str = "",
                        clear_first: bool = True) -> str:
        page = await self._get_page()
        try:
            el = page.locator(selector).first if selector else page.locator(":focus")
            if clear_first:
                await el.clear()
            await el.type(text, delay=50)
            return "Text typed."
        except Exception as e:
            return f"Type error: {e}"

    async def scroll(self, direction: str = "down", amount: int = 500) -> str:
        page = await self._get_page()
        try:
            y = amount if direction == "down" else -amount
            await page.mouse.wheel(0, y)
            return f"Scrolled {direction}."
        except Exception as e:
            return f"Scroll error: {e}"

    async def press(self, key: str) -> str:
        page = await self._get_page()
        try:
            await page.keyboard.press(key)
            return f"Pressed: {key}"
        except Exception as e:
            return f"Key error: {e}"

    async def get_text(self) -> str:
        page = await self._get_page()
        try:
            text = await page.inner_text("body")
            return text[:4_000]
        except Exception as e:
            return f"Could not get page text: {e}"

    async def get_url(self) -> str:
        page = await self._get_page()
        return page.url

    async def fill_form(self, fields: dict) -> str:
        page    = await self._get_page()
        results = []
        for selector, value in fields.items():
            try:
                el = page.locator(selector).first
                await el.clear()
                await el.type(str(value), delay=40)
                results.append(f"✓ {selector}")
            except Exception as e:
                results.append(f"✗ {selector}: {e}")
        return "Form filled: " + ", ".join(results)

    async def smart_click(self, description: str) -> str:
        page = await self._get_page()
        for role in ("button", "link", "searchbox", "textbox", "menuitem", "tab"):
            try:
                loc = page.get_by_role(role, name=description)
                if await loc.count() > 0:
                    await loc.first.click(timeout=5_000)
                    return f"Clicked ({role}): '{description}'"
            except Exception:
                pass
        for attempt in (
            lambda: page.get_by_text(description, exact=False).first.click(timeout=5_000),
            lambda: page.get_by_placeholder(description, exact=False).first.click(timeout=5_000),
            lambda: page.locator(
                f'[alt*="{description}" i],[title*="{description}" i],'
                f'[aria-label*="{description}" i]'
            ).first.click(timeout=5_000),
        ):
            try:
                await attempt()
                return f"Clicked: '{description}'"
            except Exception:
                pass
        return f"Could not find element: '{description}'"

    async def smart_type(self, description: str, text: str) -> str:
        page = await self._get_page()
        candidates = [
            ("placeholder", page.get_by_placeholder(description, exact=False)),
            ("label",       page.get_by_label(description, exact=False)),
            ("role",        page.get_by_role("textbox", name=description)),
            ("searchbox",   page.get_by_role("searchbox")),
            ("combobox",    page.get_by_role("combobox", name=description)),
        ]
        for method, loc in candidates:
            try:
                el = loc.first
                if await el.count() == 0:
                    continue
                await el.clear()
                await el.type(text, delay=50)
                return f"Typed into ({method}): '{description}'"
            except Exception:
                continue
        return f"Could not find input: '{description}'"

    async def new_tab(self, url: str = "") -> str:
        page = await self._get_page()
        ctx  = page.context
        new  = await ctx.new_page()
        self._page = new
        if url:
            return await self.go_to(url)
        return "New tab opened."

    async def close_tab(self) -> str:
        page = self._page
        if page and not page.is_closed():
            ctx   = page.context
            await page.close()
            pages = ctx.pages
            self._page = pages[-1] if pages else None
            return "Tab closed."
        return "No active tab to close."

    async def screenshot(self, path: str = None) -> str:
        page = await self._get_page()
        try:
            save_path = path or str(Path.home() / "Desktop" / "jarvis_screenshot.png")
            await page.screenshot(path=save_path, full_page=False)
            return f"Screenshot saved: {save_path}"
        except Exception as e:
            return f"Screenshot error: {e}"

    async def back(self) -> str:
        page = await self._get_page()
        try:
            await page.go_back(timeout=10_000)
            return f"Navigated back: {page.url}"
        except Exception as e:
            return f"Back error: {e}"

    async def forward(self) -> str:
        page = await self._get_page()
        try:
            await page.go_forward(timeout=10_000)
            return f"Navigated forward: {page.url}"
        except Exception as e:
            return f"Forward error: {e}"

    async def reload(self) -> str:
        page = await self._get_page()
        try:
            await page.reload(timeout=15_000)
            return f"Page reloaded: {page.url}"
        except Exception as e:
            return f"Reload error: {e}"

    async def close_browser(self) -> str:
        await self._async_close()
        return f"{self.browser_name} closed."

class _SessionRegistry:
    """Tüm aktif tarayıcı oturumlarını yönetir."""

    def __init__(self):
        self._sessions:       dict[str, _BrowserSession] = {}
        self._active_browser: str                        = ""
        self._lock            = threading.Lock()

    def _get_or_create(self, browser_name: str) -> _BrowserSession:
        with self._lock:
            if browser_name not in self._sessions:
                sess = _BrowserSession(browser_name)
                sess.start()
                self._sessions[browser_name] = sess
                print(f"[Registry] New session: {browser_name}")
            return self._sessions[browser_name]

    def get(self, browser_name: str | None = None):
        if not browser_name:
            browser_name = self._active_browser or _detect_default_browser()
        browser_name = _ALIASES.get(browser_name.lower().strip(), browser_name.lower().strip())

        # On macOS, always use the existing Chrome via AppleScript — never Playwright
        if _OS == "Darwin" and browser_name == "chrome":
            key = "__macos_chrome__"
            with self._lock:
                if key not in self._sessions:
                    self._sessions[key] = MacOSChromeSession()
                self._active_browser = browser_name
                return self._sessions[key]

        sess = self._get_or_create(browser_name)
        self._active_browser = browser_name
        return sess

    def switch(self, browser_name: str) -> str:
        browser_name = _ALIASES.get(browser_name.lower().strip(), browser_name.lower().strip())
        self._get_or_create(browser_name)
        self._active_browser = browser_name
        return f"Active browser → {browser_name}"

    def close_one(self, browser_name: str) -> str:
        with self._lock:
            sess = self._sessions.pop(browser_name, None)
        if sess:
            sess.close()
            if self._active_browser == browser_name:
                self._active_browser = ""
            return f"{browser_name} closed."
        return f"No active session for: {browser_name}"

    def close_all(self) -> str:
        with self._lock:
            names    = list(self._sessions.keys())
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._active_browser = ""
        for s in sessions:
            try:
                s.close()
            except Exception:
                pass
        return "All browsers closed: " + (", ".join(names) if names else "none")

    def list_sessions(self) -> str:
        with self._lock:
            if not self._sessions:
                return "No active browser sessions."
            lines = []
            for name in self._sessions:
                marker = " ◀ active" if name == self._active_browser else ""
                lines.append(f"  • {name}{marker}")
            return "Open browsers:\n" + "\n".join(lines)


_registry = _SessionRegistry()

def browser_control(
    parameters:    dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params  = parameters or {}
    action  = params.get("action", "").lower().strip()
    browser = params.get("browser", "").lower().strip() or None
    result  = "Unknown action."

    if action == "switch":
        target = browser or params.get("target", "").lower().strip()
        result = _registry.switch(target) if target else "Please specify a browser."
        _log(player, result)
        return result

    if action == "list_browsers":
        result = _registry.list_sessions()
        _log(player, result)
        return result

    if action == "close_all":
        result = _registry.close_all()
        _log(player, result)
        return result

    try:
        sess = _registry.get(browser)
    except Exception as e:
        result = f"Could not start browser session: {e}"
        _log(player, result)
        return result

    try:
        if action == "go_to":
            result = sess.run(sess.go_to(params.get("url", "")))
        elif action == "search":
            result = sess.run(sess.search(params.get("query", ""), params.get("engine", "google")))
        elif action == "click":
            result = sess.run(sess.click(params.get("selector"), params.get("text")))
        elif action == "type":
            result = sess.run(sess.type_text(
                params.get("selector"), params.get("text", ""), params.get("clear_first", True)))
        elif action == "scroll":
            result = sess.run(sess.scroll(params.get("direction", "down"), int(params.get("amount", 500))))
        elif action == "fill_form":
            result = sess.run(sess.fill_form(params.get("fields", {})))
        elif action == "smart_click":
            result = sess.run(sess.smart_click(params.get("description", "")))
        elif action == "smart_type":
            result = sess.run(sess.smart_type(params.get("description", ""), params.get("text", "")))
        elif action == "get_text":
            result = sess.run(sess.get_text())
        elif action == "get_url":
            result = sess.run(sess.get_url())
        elif action == "press":
            result = sess.run(sess.press(params.get("key", "Enter")))
        elif action == "new_tab":
            result = sess.run(sess.new_tab(params.get("url", "")))
        elif action == "close_tab":
            result = sess.run(sess.close_tab())
        elif action == "screenshot":
            result = sess.run(sess.screenshot(params.get("path")))
        elif action == "back":
            result = sess.run(sess.back())
        elif action == "forward":
            result = sess.run(sess.forward())
        elif action == "reload":
            result = sess.run(sess.reload())
        elif action == "close":
            target = browser or _registry._active_browser
            result = _registry.close_one(target) if target else "No browser specified."

        # ── new actions ──────────────────────────────────────────────────────
        elif action in ("read_page", "get_text"):
            result = sess.get_text()

        elif action == "list_profiles":
            if hasattr(sess, "list_profiles"):
                result = sess.list_profiles()
            else:
                result = "Profile listing only supported in Chrome on macOS."

        elif action == "switch_profile":
            name = params.get("text") or params.get("profile") or ""
            if not name:
                if hasattr(sess, "list_profiles"):
                    result = sess.list_profiles()
                else:
                    result = "Please specify a profile name."
            elif hasattr(sess, "switch_profile"):
                result = sess.switch_profile(name)
            else:
                result = "Profile switching only supported in Chrome on macOS."

        elif action == "heal":
            goal    = params.get("description") or params.get("goal") or "complete the current browser task"
            context = params.get("context") or ""
            speak   = getattr(player, "speak", None) if player else None
            if hasattr(sess, "heal"):
                result = sess.heal(goal=goal, speak=speak, context=context)
            else:
                result = "Self-healing only supported in Chrome on macOS."

        else:
            result = f"Unknown browser action: '{action}'"

    except concurrent.futures.TimeoutError:
        result = f"Browser action '{action}' timed out (60s)."
    except Exception as e:
        result = f"Browser error ({action}): {e}"

    _log(player, result)
    return result


def _log(player, text: str):
    short = str(text)[:80]
    print(f"[Browser] {short}")
    if player:
        player.write_log(f"[browser] {short[:60]}")