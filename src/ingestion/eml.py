from __future__ import annotations

import email
import email.policy
from pathlib import Path


def read_eml(path: Path) -> tuple[str, dict]:
    try:
        raw = path.read_bytes()
        msg = email.message_from_bytes(raw, policy=email.policy.default)
    except Exception as e:
        return f"(error reading {path.name}: {e})", {}

    parts = []
    headers = {
        "from": msg.get("From", ""),
        "to": msg.get("To", ""),
        "cc": msg.get("Cc", ""),
        "subject": msg.get("Subject", ""),
        "date": msg.get("Date", ""),
    }
    parts.append(f"From: {headers['from']}")
    parts.append(f"To: {headers['to']}")
    if headers["cc"]:
        parts.append(f"CC: {headers['cc']}")
    parts.append(f"Subject: {headers['subject']}")
    parts.append(f"Date: {headers['date']}")
    parts.append("")

    body = msg.get_body(preferencelist=("plain", "html"))
    if body:
        content = body.get_content()
        if isinstance(content, str):
            parts.append(content)

    attachments = []
    for part in msg.walk():
        if part.get_content_disposition() == "attachment":
            attachments.append(part.get_filename() or "unnamed")
    if attachments:
        parts.append(f"\nAttachments: {', '.join(attachments)}")

    return "\n".join(parts), {"headers": headers, "attachments": attachments}
