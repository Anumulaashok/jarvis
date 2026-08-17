"""
Gmail integration using Google OAuth 2.0 and the Gmail REST API.
Setup: set google_client_id and google_client_secret via the integration manager,
then call auth() to run the OAuth consent flow (opens browser once).
"""
import json
import sys
from pathlib import Path
from typing import Optional

from integrations.manager import (
    get_credential, is_configured, is_authed,
    save_token, load_token, remove_token, get_service_creds,
)

SERVICE   = "gmail"
SCOPES    = ["https://www.googleapis.com/auth/gmail.readonly"]


def _creds_json() -> dict:
    client_id     = get_credential(SERVICE, "google_client_id")
    client_secret = get_credential(SERVICE, "google_client_secret")
    return {
        "installed": {
            "client_id":                  client_id,
            "client_secret":              client_secret,
            "redirect_uris":              ["http://localhost:8080/"],
            "auth_uri":                   "https://accounts.google.com/o/oauth2/auth",
            "token_uri":                  "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url":"https://www.googleapis.com/oauth2/v1/certs",
        }
    }


def auth() -> str:
    """Run OAuth consent flow. Opens browser. Saves token. Returns status message."""
    if not is_configured(SERVICE):
        return "Gmail not configured. Add google_client_id and google_client_secret first."

    from google_auth_oauthlib.flow import InstalledAppFlow
    import tempfile, json

    creds_data = _creds_json()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(creds_data, f)
        tmp = f.name

    try:
        flow = InstalledAppFlow.from_client_secrets_file(tmp, SCOPES)
        creds = flow.run_local_server(port=8080, open_browser=True)
        save_token(SERVICE, json.loads(creds.to_json()))
        return "Gmail authenticated successfully. Inbox monitoring is now active."
    except Exception as e:
        return f"Gmail auth failed: {e}"
    finally:
        Path(tmp).unlink(missing_ok=True)


def _get_service():
    """Build an authenticated Gmail API service object."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    token_data = load_token(SERVICE)
    if not token_data:
        raise RuntimeError("Gmail not authenticated. Run auth() first.")

    creds = Credentials.from_authorized_user_info(token_data, SCOPES)

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        save_token(SERVICE, json.loads(creds.to_json()))

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def get_unread_emails(max_results: int = 10) -> list[dict]:
    """Returns list of unread emails: {id, from, subject, snippet, date}."""
    svc = _get_service()
    resp = svc.users().messages().list(
        userId="me",
        q="is:unread in:inbox",
        maxResults=max_results,
    ).execute()

    messages = resp.get("messages", [])
    emails   = []

    for msg in messages:
        full = svc.users().messages().get(
            userId="me", id=msg["id"], format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()

        headers = {h["name"]: h["value"] for h in full.get("payload", {}).get("headers", [])}
        emails.append({
            "id":      msg["id"],
            "from":    headers.get("From", "Unknown"),
            "subject": headers.get("Subject", "(no subject)"),
            "snippet": full.get("snippet", "")[:120],
            "date":    headers.get("Date", ""),
        })

    return emails


def get_unread_count() -> int:
    svc = _get_service()
    resp = svc.users().messages().list(
        userId="me", q="is:unread in:inbox", maxResults=1,
    ).execute()
    return resp.get("resultSizeEstimate", 0)


def mark_as_seen(msg_id: str) -> None:
    svc = _get_service()
    svc.users().messages().modify(
        userId="me", id=msg_id,
        body={"removeLabelIds": ["UNREAD"]},
    ).execute()


def archive_email(msg_id: str) -> None:
    """Remove from inbox (archive) without deleting."""
    svc = _get_service()
    svc.users().messages().modify(
        userId="me", id=msg_id,
        body={"removeLabelIds": ["INBOX", "UNREAD"]},
    ).execute()


def delete_email(msg_id: str) -> None:
    """Move to trash."""
    svc = _get_service()
    svc.users().messages().trash(userId="me", id=msg_id).execute()


def reply_to_email(msg_id: str, body: str) -> None:
    """Send a reply to an existing message."""
    import base64
    from email.mime.text import MIMEText

    svc  = _get_service()
    orig = svc.users().messages().get(
        userId="me", id=msg_id, format="metadata",
        metadataHeaders=["From", "Subject", "Message-ID", "To"],
    ).execute()

    headers   = {h["name"]: h["value"] for h in orig.get("payload", {}).get("headers", [])}
    thread_id = orig.get("threadId", "")
    to_addr   = headers.get("Reply-To") or headers.get("From", "")
    subject   = headers.get("Subject", "")
    if not subject.lower().startswith("re:"):
        subject = "Re: " + subject
    msg_id_hdr = headers.get("Message-ID", "")

    mime = MIMEText(body)
    mime["To"]         = to_addr
    mime["Subject"]    = subject
    mime["In-Reply-To"] = msg_id_hdr
    mime["References"] = msg_id_hdr

    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
    svc.users().messages().send(
        userId="me",
        body={"raw": raw, "threadId": thread_id},
    ).execute()


def is_ready() -> bool:
    return is_configured(SERVICE) and is_authed(SERVICE)
