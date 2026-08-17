"""
Slack integration — supports both classic xoxb- tokens and rotatable xoxe.xoxb- tokens.
Rotatable tokens auto-refresh using the stored refresh_token + client credentials.
"""
import time
from typing import Optional

from integrations.manager import get_credential, load_token, save_token

SERVICE = "slack"


def _refresh_token_if_needed() -> str:
    """Return a valid access token, refreshing via OAuth if it's expired."""
    token_data = load_token(SERVICE)

    if token_data:
        expires_at = token_data.get("expires_at", 0)
        # Refresh 5 minutes before expiry
        if time.time() < expires_at - 300:
            return token_data["access_token"]

        # Token expired — refresh it
        refresh_token  = token_data.get("refresh_token")
        client_id      = get_credential(SERVICE, "slack_client_id")
        client_secret  = get_credential(SERVICE, "slack_client_secret")

        if refresh_token and client_id and client_secret:
            try:
                import urllib.request, urllib.parse, json as _json
                data = urllib.parse.urlencode({
                    "grant_type":    "refresh_token",
                    "client_id":     client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                }).encode()
                req = urllib.request.Request(
                    "https://slack.com/api/oauth.v2.exchange",
                    data=data,
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = _json.loads(resp.read())

                if result.get("ok"):
                    new_data = {
                        **token_data,
                        "access_token":  result["access_token"],
                        "refresh_token": result.get("refresh_token", refresh_token),
                        "expires_at":    time.time() + result.get("expires_in", 43200),
                    }
                    save_token(SERVICE, new_data)
                    # Also update the stored credential
                    from integrations.manager import set_credential
                    set_credential(SERVICE, "slack_bot_token", result["access_token"])
                    print("[Slack] Token refreshed successfully")
                    return result["access_token"]
            except Exception as e:
                print(f"[Slack] Token refresh failed: {e}")

    # Fall back to stored credential
    token = get_credential(SERVICE, "slack_bot_token")
    if not token:
        raise RuntimeError("Slack not configured. Add slack_bot_token first.")
    return token


def _client():
    from slack_sdk import WebClient
    token = _refresh_token_if_needed()
    return WebClient(token=token)


def is_ready() -> bool:
    try:
        token = get_credential(SERVICE, "slack_bot_token")
        # Accept both classic xoxb- and rotatable xoxe.xoxb- tokens
        return bool(token and ("xoxb-" in token or "xoxe." in token))
    except Exception:
        return False


def test_connection() -> str:
    try:
        r = _client().auth_test()
        return f"Connected to Slack as @{r['user']} in workspace {r['team']}."
    except Exception as e:
        return f"Slack connection failed: {e}"


def get_channels(limit: int = 50) -> list[dict]:
    try:
        r = _client().conversations_list(
            types="public_channel,private_channel",
            limit=limit,
            exclude_archived=True,
        )
        return [
            {"id": c["id"], "name": c["name"], "unread": c.get("unread_count", 0)}
            for c in r.get("channels", [])
            if c.get("is_member")
        ]
    except Exception as e:
        print(f"[Slack] get_channels error: {e}")
        return []


def get_recent_messages(channel_id: str, limit: int = 10, since_ts: Optional[float] = None) -> list[dict]:
    try:
        kwargs: dict = {"channel": channel_id, "limit": limit}
        if since_ts:
            kwargs["oldest"] = str(since_ts)
        r = _client().conversations_history(**kwargs)
        return [
            {
                "ts":      m["ts"],
                "user":    m.get("user", ""),
                "text":    m.get("text", ""),
                "channel": channel_id,
            }
            for m in r.get("messages", [])
            if m.get("type") == "message" and not m.get("subtype")
        ]
    except Exception as e:
        print(f"[Slack] get_recent_messages error: {e}")
        return []


def get_unread_summary(since_minutes: int = 30) -> list[dict]:
    since_ts = time.time() - (since_minutes * 60)
    channels = get_channels()
    updates  = []
    for ch in channels:
        msgs = get_recent_messages(ch["id"], limit=20, since_ts=since_ts)
        if msgs:
            updates.append({
                "channel": ch["name"],
                "count":   len(msgs),
                "latest":  msgs[0]["text"][:120],
            })
    return updates


def resolve_user(user_id: str) -> str:
    try:
        r = _client().users_info(user=user_id)
        return r["user"].get("display_name") or r["user"].get("real_name", user_id)
    except Exception:
        return user_id


def send_message(channel: str, text: str) -> str:
    """Post a message to a Slack channel (name like #general or channel ID)."""
    try:
        ch = channel.lstrip("#")
        # Resolve channel name → ID if needed
        if not ch.startswith("C"):
            for c in get_channels():
                if c["name"] == ch:
                    ch = c["id"]
                    break
        r = _client().chat_postMessage(channel=ch, text=text)
        return f"Message sent to #{channel.lstrip('#')}."
    except Exception as e:
        return f"Slack send failed: {e}"
