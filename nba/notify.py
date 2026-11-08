"""Delivery: email through Gmail (app password) and phone alerts through ntfy.

Secrets come from environment variables set by GitHub Actions:
  GMAIL_ADDRESS, GMAIL_APP_PASSWORD, optional EMAIL_TO (defaults to GMAIL_ADDRESS),
  NTFY_TOPIC.
"""
from __future__ import annotations

import os
import smtplib
import ssl
import urllib.request
from email.message import EmailMessage


class SetupError(RuntimeError):
    pass


def check_email_setup() -> list[str]:
    """Plain-English problems with the email secrets (never prints their values)."""
    problems = []
    addr = os.environ.get("GMAIL_ADDRESS", "").strip()
    pw = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    if not addr:
        problems.append("GMAIL_ADDRESS secret is missing or empty.")
    elif "@" not in addr:
        problems.append("GMAIL_ADDRESS doesn't look like an email address (no @).")
    if not pw:
        problems.append("GMAIL_APP_PASSWORD secret is missing or empty.")
    elif pw.upper() in ("GMAIL_APP_PASSWORD", "GMAIL_ADDRESS"):
        problems.append("GMAIL_APP_PASSWORD holds the secret's name instead of the password.")
    elif len(pw) != 16:
        problems.append(f"GMAIL_APP_PASSWORD should be 16 letters; it has {len(pw)} characters.")
    return problems


def send_email(subject: str, html: str, text: str = "", attachments: list[tuple[str, bytes, str]] = ()):
    problems = check_email_setup()
    if problems:
        raise SetupError(" ".join(problems))
    addr = os.environ["GMAIL_ADDRESS"].strip()
    pw = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "").strip()
    to = os.environ.get("EMAIL_TO", "").strip() or addr
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"NBA Pregame Board <{addr}>"
    msg["To"] = to
    msg.set_content(text or "Open this email in an app that shows HTML to see the board.")
    msg.add_alternative(html, subtype="html")
    for name, data, mime in attachments:
        maintype, subtype = mime.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=30) as s:
        s.login(addr, pw)
        s.send_message(msg)


def send_alert(title: str, message: str, click: str | None = None, priority: str = "default",
               tags: str = "basketball"):
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        raise SetupError("NTFY_TOPIC secret is missing.")
    headers = {"Title": title.encode("ascii", "replace").decode(), "Tags": tags, "Priority": priority}
    if click:
        headers["Click"] = click
    req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=message.encode("utf-8"),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()
