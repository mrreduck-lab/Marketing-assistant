"""Read-only Sent mail access and non-secret SMTP diagnostics."""

import email
import email.policy
import imaplib
import os
import re
import time
from datetime import datetime, timezone

from mail_actions import _body, _fetch, _uid, decode


def _connect():
    user, password = os.environ.get("MAIL_USER"), os.environ.get("MAIL_PASSWORD")
    if not user or not password:
        raise RuntimeError("MAIL_USER and MAIL_PASSWORD are required")
    conn = imaplib.IMAP4_SSL(os.environ.get("MAIL_HOST", "imap.mail.ru"),
                             int(os.environ.get("MAIL_PORT", "993")), timeout=20)
    try:
        conn.login(user, password)
        return conn
    except Exception:
        conn.logout()
        raise


def _folders(conn):
    status, lines = conn.list()
    if status != "OK":
        raise RuntimeError("Cannot list IMAP folders")
    folders = []
    for raw in lines or []:
        if not isinstance(raw, bytes):
            continue
        match = re.match(rb'^\((.*?)\)\s+(?:"[^"]*"|NIL)\s+(.+)$', raw)
        if not match:
            continue
        flags = match.group(1).decode("ascii", "replace").lower()
        name = match.group(2).decode("ascii", "replace").strip()
        if name.startswith('"') and name.endswith('"'):
            name = name[1:-1]
        if r'\noselect' not in flags:
            folders.append({"name": name, "sent": r'\sent' in flags})
    return folders


def list_folders():
    conn = _connect()
    try:
        return {"folders": _folders(conn), "configured_sent_folder": os.environ.get("MAIL_SENT_FOLDER", "")}
    finally:
        conn.logout()


def _select_sent(conn):
    folders = _folders(conn)
    explicit = os.environ.get("MAIL_SENT_FOLDER", "").strip()
    matches = [f["name"] for f in folders if f["name"] == explicit] if explicit else [
        f["name"] for f in folders if f["sent"]]
    if len(matches) != 1:
        raise RuntimeError("Sent folder not uniquely identified; inspect list_mail_folders and set MAIL_SENT_FOLDER")
    status, _ = conn.select(matches[0], readonly=True)
    if status != "OK":
        raise RuntimeError("Cannot select Sent folder")
    return matches[0]


def list_sent(limit=20, query=""):
    if not 1 <= limit <= 50 or len(query) > 120:
        raise ValueError("limit must be 1-50; query at most 120 characters")
    conn = _connect()
    try:
        folder = _select_sent(conn)
        status, data = conn.uid("search", None, "ALL")
        if status != "OK":
            raise RuntimeError("Cannot search Sent folder")
        found = []
        for uid in reversed((data[0] or b"").split()[-500:]):
            status, parts = conn.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID)])")
            if status != "OK":
                continue
            raw = next((p[1] for p in parts or [] if isinstance(p, tuple) and isinstance(p[1], bytes)), None)
            if raw is None:
                continue
            msg = email.message_from_bytes(raw, policy=email.policy.default)
            item = {"uid": uid.decode("ascii"), "folder": folder, "from": decode(msg.get("From")),
                    "to": decode(msg.get("To")), "subject": decode(msg.get("Subject")),
                    "date": str(msg.get("Date", "")), "message_id": str(msg.get("Message-ID", ""))}
            if query and query.casefold() not in " ".join((item["from"], item["to"], item["subject"])).casefold():
                continue
            found.append(item)
            if len(found) >= limit:
                break
        return {"messages": found, "folder": folder, "scan_limit": 500}
    finally:
        conn.logout()


def read_sent(uid, max_chars=20000):
    if not 1000 <= max_chars <= 50000:
        raise ValueError("max_chars must be 1000-50000")
    conn = _connect()
    try:
        folder = _select_sent(conn)
        msg = _fetch(conn, _uid(uid))
        body, attachments = _body(msg)
        return {"uid": _uid(uid), "folder": folder, "from": decode(msg.get("From")),
                "to": decode(msg.get("To")), "subject": decode(msg.get("Subject")),
                "date": str(msg.get("Date", "")), "message_id": str(msg.get("Message-ID", "")),
                "body": body[:max_chars], "truncated": len(body) > max_chars,
                "attachments": attachments, "attachment_contents_read": False}
    finally:
        conn.logout()



def _message_exists(conn, message_id):
    """Search exact Message-ID in selected Sent folder; no message flags changed."""
    status, data = conn.uid("search", None, "HEADER", "Message-ID", message_id)
    if status != "OK":
        raise RuntimeError("Cannot verify Sent by Message-ID")
    return bool(data and data[0] and data[0].strip())


def save_sent_copy(message, delay_seconds=2):
    """Persist exact SMTP-accepted MIME message only if Sent lacks its Message-ID.

    Never retry APPEND after a transport error: its outcome may be ambiguous.
    """
    message_id = str(message.get("Message-ID", ""))
    if not re.fullmatch(r"<[^<>\\r\\n\\s]+>", message_id):
        raise ValueError("Outgoing message requires a valid Message-ID")
    conn = _connect()
    try:
        folder = _select_sent(conn)
        if _message_exists(conn, message_id):
            return {"saved": True, "method": "provider", "folder": folder}
        # Allow a provider that asynchronously files SMTP messages a short grace period.
        if delay_seconds:
            time.sleep(delay_seconds)
            if _message_exists(conn, message_id):
                return {"saved": True, "method": "provider", "folder": folder}
        status, _ = conn.append(folder, r"(\\Seen)", imaplib.Time2Internaldate(
            datetime.now(timezone.utc)), message.as_bytes(policy=email.policy.SMTP))
        if status != "OK":
            raise RuntimeError("IMAP APPEND did not confirm success; do not retry blindly")
        if not _message_exists(conn, message_id):
            raise RuntimeError("IMAP APPEND acknowledged but Sent lookup did not find the message")
        return {"saved": True, "method": "imap_append", "folder": folder}
    finally:
        conn.logout()


def smtp_status():
    mail_user = os.environ.get("MAIL_USER", "")
    smtp_user = os.environ.get("SMTP_USER") or mail_user
    password_present = bool(os.environ.get("SMTP_PASSWORD") or os.environ.get("MAIL_PASSWORD"))
    matches = bool(mail_user and smtp_user and mail_user.casefold() == smtp_user.casefold())
    return {"smtp_user_configured": bool(smtp_user),
            "smtp_password_configured": password_present,
            "credential_source": "explicit_smtp" if os.environ.get("SMTP_PASSWORD") else
                                 "existing_mail_app_password" if os.environ.get("MAIL_PASSWORD") else "missing",
            "smtp_user_matches_mail_user": matches,
            "smtp_host": os.environ.get("SMTP_HOST", "smtp.mail.ru"),
            "smtp_port": os.environ.get("SMTP_PORT", "465"),
            "ready_to_attempt_send": bool(matches and password_present),
            "note": "Configuration check only: SMTP login and delivery were not tested."}
