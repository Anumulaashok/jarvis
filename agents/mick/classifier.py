"""
Classifies an email into one of:
  report        — tell the orchestrator, needs human awareness
  auto_archive  — marketing/newsletter/junk, silently archive
  needs_clarity — ambiguous, ask the user how to handle
  auto_reply    — simple ack/confirmation, Mick can reply automatically

Uses Gemini when available; falls back to rule-based classification when quota is exhausted.
"""
import json
import re
from pathlib import Path


# ── Rule-based domains/senders that are always junk ──────────────────────────
_JUNK_DOMAINS = {
    "linkedin.com", "indeed.com", "naukri.com", "glassdoor.com",
    "monster.com", "shine.com", "timesjobs.com",
    "amazon.in", "amazon.com", "flipkart.com", "myntra.com",
    "zepto.com", "swiggy.com", "zomato.com", "blinkit.com",
    "motilaloswalmf.com", "mutualfund", "groww.in", "zerodha.com",
    "zoom.us", "webex.com",
    "medium.com", "substack.com", "mailchimp.com", "constantcontact.com",
}

_JUNK_SUBJECT_KEYWORDS = [
    "unsubscribe", "newsletter", "job alert", "apply now", "top jobs",
    "promotional", "offer", "discount", "sale", "% off", "free trial",
    "registration confirmed", "payment successful", "order confirmed",
    "market outlook", "weekly digest", "monthly report", "breaking news",
    "your account statement", "e-statement",
]

_IMPORTANT_SUBJECT_KEYWORDS = [
    "urgent", "asap", "deadline", "critical", "immediate", "action required",
    "meeting", "interview", "offer letter", "invoice", "contract",
    "password", "security alert", "verification", "otp",
]


def _rule_classify(email: dict) -> tuple[str, str, None]:
    from_str = email.get("from", "").lower()
    subject  = email.get("subject", "").lower()
    snippet  = email.get("snippet", "").lower()

    # Check for important keywords first
    for kw in _IMPORTANT_SUBJECT_KEYWORDS:
        if kw in subject or kw in snippet:
            return "report", f"contains important keyword: '{kw}'", None

    # Check junk domains
    m = re.search(r"@([\w.\-]+)", from_str)
    domain = m.group(1) if m else ""
    for jd in _JUNK_DOMAINS:
        if jd in domain:
            return "auto_archive", f"automated email from {domain}", None

    # Check noreply / automated senders
    if any(x in from_str for x in ["noreply", "no-reply", "donotreply", "notifications@", "alerts@", "updates@"]):
        return "auto_archive", "automated/noreply sender", None

    # Check junk subject keywords
    for kw in _JUNK_SUBJECT_KEYWORDS:
        if kw in subject or kw in snippet:
            return "auto_archive", f"matches junk keyword: '{kw}'", None

    # Default: report to user
    return "report", "no junk signals — flagging for user", None


def _api_key() -> str:
    try:
        cfg = Path(__file__).resolve().parent.parent.parent / "config" / "api_keys.json"
        return json.loads(cfg.read_text(encoding="utf-8")).get("gemini_api_key", "")
    except Exception:
        return ""


def classify_email(email: dict) -> tuple[str, str, str | None]:
    """Returns (action, reasoning, suggested_reply). Falls back to rules if API unavailable."""
    api_key = _api_key()
    if not api_key:
        return _rule_classify(email)

    prompt = f"""You are Mick, a smart email assistant. Classify this email and decide what to do.

Email:
From: {email.get('from', '')}
Subject: {email.get('subject', '')}
Snippet: {email.get('snippet', '')}

Choose ONE action:
- auto_archive: newsletters, marketing, promotions, job alerts, social notifications, receipts for small amounts, automated system emails
- report: anything that needs the user's attention — work emails, personal messages, urgent items, large transactions, calendar invites from real people
- auto_reply: simple emails where a polite "Acknowledged, will respond shortly" is appropriate (rare)
- needs_clarity: genuinely ambiguous — you're not sure if the user cares

Respond in JSON only:
{{"action": "report|auto_archive|auto_reply|needs_clarity", "reasoning": "one sentence why", "suggested_reply": "reply text or null"}}"""

    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=prompt,
        )
        text = resp.text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text.strip())
        return (
            data.get("action", "report"),
            data.get("reasoning", ""),
            data.get("suggested_reply"),
        )
    except Exception as e:
        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
            print("[Mick/classifier] API quota exhausted — using rule-based fallback")
        else:
            print(f"[Mick/classifier] API error: {e}")
        return _rule_classify(email)
