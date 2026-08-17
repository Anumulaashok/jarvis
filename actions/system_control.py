import platform
import subprocess
import time
import sys


_OS = platform.system()


def system_control(parameters: dict, player=None, speak=None) -> str:
    action = parameters.get("action", "")
    delay  = int(parameters.get("delay", 0))

    if not action:
        return "Please specify an action: shutdown, restart, sleep, or lock."

    if delay > 0:
        msg = f"System will {action} in {delay} seconds, sir."
        if speak:
            speak(msg)
        if player:
            player.write_log(f"SYS: {msg}")
        time.sleep(delay)

    if action == "shutdown":
        if speak:
            speak("Shutting down the system, sir. Goodbye.")
        _do_shutdown()
        return "Shutdown initiated, sir."

    elif action == "restart":
        if speak:
            speak("Restarting the system, sir.")
        _do_restart()
        return "Restart initiated, sir."

    elif action == "sleep":
        if speak:
            speak("Putting the system to sleep, sir.")
        _do_sleep()
        return "Sleep initiated, sir."

    elif action == "lock":
        if speak:
            speak("Locking the screen, sir.")
        _do_lock()
        return "Screen locked, sir."

    return f"Unknown system action: {action}"


def _do_shutdown():
    if _OS == "Windows":
        subprocess.Popen(["shutdown", "/s", "/t", "5"])
    elif _OS == "Darwin":
        subprocess.Popen(
            ["osascript", "-e", 'tell application "System Events" to shut down']
        )
    else:
        subprocess.Popen(["shutdown", "-h", "+0"])


def _do_restart():
    if _OS == "Windows":
        subprocess.Popen(["shutdown", "/r", "/t", "5"])
    elif _OS == "Darwin":
        subprocess.Popen(
            ["osascript", "-e", 'tell application "System Events" to restart']
        )
    else:
        subprocess.Popen(["shutdown", "-r", "+0"])


def _do_sleep():
    if _OS == "Windows":
        subprocess.Popen(
            ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]
        )
    elif _OS == "Darwin":
        subprocess.Popen(["pmset", "sleepnow"])
    else:
        subprocess.Popen(["systemctl", "suspend"])


def _do_lock():
    if _OS == "Windows":
        subprocess.Popen(["rundll32.exe", "user32.dll,LockWorkStation"])
    elif _OS == "Darwin":
        subprocess.Popen(
            ["osascript", "-e",
             'tell application "System Events" to keystroke "q" '
             'using {control down, command down}']
        )
    else:
        for cmd in [["xdg-screensaver", "lock"], ["gnome-screensaver-command", "-l"]]:
            try:
                subprocess.Popen(cmd)
                return
            except FileNotFoundError:
                pass
