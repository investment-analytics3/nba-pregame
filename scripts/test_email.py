#!/usr/bin/env python3
"""Send a test email and a phone alert, with plain-English setup checks."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nba import notify  # noqa: E402

problems = notify.check_email_setup()
if problems:
    for p in problems:
        print("SETUP PROBLEM:", p)
    sys.exit(1)
print("Email secrets look right (address has @, app password is 16 letters).")

html = """<div style="font-family:Arial,sans-serif;max-width:520px;margin:auto;padding:24px;
background:#111829;color:#E6EAF2;border-radius:16px">
<div style="font-size:12px;letter-spacing:2px;color:#8B95AB">NBA PREGAME BOARD</div>
<h1 style="margin:8px 0 12px;font-size:28px">Email delivery works</h1>
<p style="line-height:1.5;color:#C7CEDC">This is a test from your nba-pregame GitHub repo.
The real board will arrive at 12 PM on weekdays, 10 AM on weekends, and a final
version 90 minutes before the first tip.</p></div>"""
try:
    notify.send_email("NBA Pregame Board - test email", html, "Email delivery works.")
    print("Test email sent.")
except Exception as e:
    msg = str(e)
    if "Username and Password not accepted" in msg or "535" in msg:
        print("GMAIL REJECTED THE LOGIN: the app password or address doesn't match. "
              "Create a new app password and update the GMAIL_APP_PASSWORD secret.")
    else:
        print("EMAIL FAILED:", type(e).__name__, msg[:200])
    sys.exit(1)

notify.send_alert("NBA Pregame Board - email test", "Test email sent. Check your Gmail inbox.")
print("Phone alert sent.")
