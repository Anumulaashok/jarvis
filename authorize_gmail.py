#!/usr/bin/env python3
"""
Run this once to authorize Gmail OAuth.
Steps:
  1. Add anumulaashok85@gmail.com as a test user in Google Cloud Console
     (APIs & Services → OAuth consent screen → Test users → + ADD USERS)
  2. Run:  python authorize_gmail.py
  3. Browser will open → sign in with anumulaashok85@gmail.com → Allow
  4. Token saved to config/tokens/gmail.json — Jarvis will use it automatically.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from integrations.gmail import auth, is_ready

print("=" * 60)
print("  Gmail OAuth Authorization")
print("=" * 60)

if is_ready():
    print("\n✓ Gmail already authorized — Jarvis can read your inbox.")
    print("  Delete config/tokens/gmail.json to re-authorize.\n")
    sys.exit(0)

print("\nOpening browser for Google sign-in...")
print("Sign in with: anumulaashok85@gmail.com\n")

result = auth()
print(f"\n{result}")

if is_ready():
    print("\n✓ Success! Restart Jarvis to activate inbox monitoring.\n")
else:
    print("\n✗ Authorization failed. Check the error above.\n")
