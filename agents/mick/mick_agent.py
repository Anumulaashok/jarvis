"""
Mick — Gmail agent.
Monitors inbox, classifies emails, archives junk, auto-replies to simple ones,
and routes ALL updates/questions through the orchestrator via speak().

Never speaks directly to the user. Always prefixes with "Sir, Mick..." so the
active persona (Tommy/Gibbs/Jack) delivers the message in character.
"""
import threading
import time
from typing import Callable, Optional


class MickAgent:
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
        self._pending: list[dict] = []   # questions waiting for user response
        self._plock = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

    def set_speak(self, fn: Callable) -> None:
        self._speak = fn

    def process_new_emails(self, emails: list) -> None:
        """Called by GmailWatcher with a list of new unseen emails."""
        from agents.mick.preferences import should_ignore, is_important
        from agents.mick.classifier import classify_email

        for email in emails:
            try:
                self._handle_one(email, should_ignore, is_important, classify_email)
            except Exception as e:
                print(f"[Mick] process error: {e}")

    def handle_user_reply(self, user_text: str) -> str:
        """
        Called when the orchestrator routes a user response back to Mick.
        Returns a confirmation message for the orchestrator to relay.
        """
        with self._plock:
            if not self._pending:
                return "Mick has no open questions at the moment, sir."
            item = self._pending.pop(0)

        return self._resolve(user_text, item)

    def get_pending_question(self) -> Optional[str]:
        """Returns the oldest pending question, or None."""
        with self._plock:
            if self._pending:
                return self._pending[0]["question"]
        return None

    def inbox_summary(self) -> str:
        """On-demand summary of recent inbox for the orchestrator to read aloud."""
        from integrations.gmail import is_ready, get_unread_emails
        if not is_ready():
            return "Mick says Gmail is not yet authorized, sir."
        emails = get_unread_emails(max_results=10)
        if not emails:
            return "Mick says the inbox is clear, sir."

        from agents.mick.preferences import should_ignore
        important = [e for e in emails if not should_ignore(e)]
        ignored   = len(emails) - len(important)

        lines = [f"Mick reports {len(important)} emails needing attention"
                 + (f" and {ignored} auto-archived, sir." if ignored else ", sir.")]
        for e in important[:5]:
            sender = e["from"].split("<")[0].strip().strip('"')
            lines.append(f"• {sender}: \"{e['subject'][:60]}\"")
        return "\n".join(lines)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _handle_one(self, email, should_ignore_fn, is_important_fn, classify_fn):
        from integrations.gmail import archive_email

        if should_ignore_fn(email):
            _safe(archive_email, email["id"])
            print(f"[Mick] auto-archived (prefs): {email['subject'][:50]}")
            return

        if is_important_fn(email):
            self._report(email, "flagged as important by your preferences")
            return

        action, reasoning, suggested_reply = classify_fn(email)

        if action == "auto_archive":
            _safe(archive_email, email["id"])
            print(f"[Mick] auto-archived (AI): {email['subject'][:50]} — {reasoning}")

        elif action == "report":
            self._report(email, reasoning)

        elif action == "auto_reply":
            reply = suggested_reply or "Acknowledged. The principal will follow up shortly."
            ok = _safe_reply(email["id"], reply)
            sender = email["from"].split("<")[0].strip().strip('"')
            if ok:
                self._say(
                    f"I replied to {sender}'s email about \"{email['subject'][:50]}\" "
                    f"with a brief acknowledgement, sir."
                )
            else:
                self._report(email, reasoning)

        elif action == "needs_clarity":
            sender = email["from"].split("<")[0].strip().strip('"')
            question = (
                f"Mick needs your guidance on an email from {sender}: "
                f"\"{email['subject'][:60]}\". {reasoning} "
                f"Should Mick archive it, reply, or flag it for you?"
            )
            with self._plock:
                self._pending.append({
                    "question": question,
                    "email":    email,
                    "action":   action,
                })
            self._say(f"Mick needs some clarity, sir. {question}")

    def _report(self, email: dict, reason: str) -> None:
        sender  = email["from"].split("<")[0].strip().strip('"')
        snippet = email["snippet"][:100].rstrip()
        self._say(
            f"Mick has an update. New email from {sender}: "
            f"\"{email['subject'][:60]}\". {snippet}."
        )

    def _say(self, message: str) -> None:
        if self._speak:
            self._speak(message)
        else:
            print(f"[Mick → orchestrator] {message}")

    def _resolve(self, user_text: str, item: dict) -> str:
        from agents.mick.preferences import add_ignored, add_important, learn_label
        from integrations.gmail import archive_email, reply_to_email

        email  = item.get("email", {})
        r      = user_text.lower()
        sender = email.get("from", "")
        eid    = email.get("id", "")

        if any(w in r for w in ["ignore", "archive", "delete", "junk", "spam", "unsubscribe"]):
            add_ignored(sender)
            if eid:
                _safe(archive_email, eid)
            return "Mick has archived it and will ignore that sender going forward, sir."

        elif any(w in r for w in ["important", "always", "flag", "report", "keep"]):
            add_important(sender)
            return "Mick will always flag emails from that sender, sir."

        elif any(w in r for w in ["reply", "respond", "send"]):
            reply_body = _extract_reply_text(user_text)
            if eid and reply_body:
                ok = _safe_reply(eid, reply_body)
                if ok:
                    return f"Mick has sent the reply, sir."
            return "Mick couldn't parse a reply. Please be more specific, sir."

        else:
            return "Mick has noted your preference and will apply it to similar emails, sir."


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception as e:
        print(f"[Mick] {fn.__name__} error: {e}")
        return False


def _safe_reply(email_id: str, body: str) -> bool:
    try:
        from integrations.gmail import reply_to_email
        reply_to_email(email_id, body)
        return True
    except Exception as e:
        print(f"[Mick] reply error: {e}")
        return False


def _extract_reply_text(user_text: str) -> str:
    for marker in ["reply", "respond", "say", "tell them", "write"]:
        idx = user_text.lower().find(marker)
        if idx != -1:
            candidate = user_text[idx + len(marker):].strip(' "\'')
            if len(candidate) > 5:
                return candidate
    return ""


def get_mick() -> MickAgent:
    return MickAgent()
