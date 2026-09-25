"""Mail.ru IMAP Drafts: provider is the source of truth, not a local draft cache."""
from __future__ import annotations

import email
import email.policy
import imaplib
import os
import re
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import make_msgid

from mail_actions import _body, _uid, decode
from sent_actions import _connect, _folders

MAX_DRAFT_BYTES = 5_000_000
MAX_BODY = 40000


def _draft_folder(conn):
    explicit = os.environ.get("MAIL_DRAFTS_FOLDER", "").strip()
    if explicit:
        choices = [f["name"] for f in _folders(conn) if f["name"] == explicit]
    else:
        status, rows = conn.list()
        if status != "OK":
            raise RuntimeError("Cannot list folders")
        choices = []
        for row in rows or []:
            if not isinstance(row, bytes):
                continue
            match = re.match(rb'^\\((.*?)\\)\\s+(?:"[^"]*"|NIL)\\s+(.+)$', row)
            if match and b"\\\\drafts" in match.group(1).lower().split():
                name = match.group(2).decode("ascii", "replace").strip()
                choices.append(name[1:-1] if name.startswith('"') and name.endswith('"') else name)
    if len(choices) != 1:
        raise RuntimeError("Drafts folder not uniquely identified; set MAIL_DRAFTS_FOLDER")
    return choices[0]


def _open(conn, readonly=True):
    folder = _draft_folder(conn)
    status, _ = conn.select(folder, readonly=readonly)
    if status != "OK":
        raise RuntimeError("Cannot open Drafts")
    return folder


def _fetch(conn, uid):
    status, rows = conn.uid("fetch", _uid(uid), "(UID BODY.PEEK[])")
    if status != "OK":
        raise RuntimeError("Cannot read draft")
    raw = next((row[1] for row in rows or [] if isinstance(row, tuple) and isinstance(row[1], bytes)), None)
    if raw is None:
        raise ValueError("Draft not found; it may have been sent, moved or deleted")
    if len(raw) > MAX_DRAFT_BYTES:
        raise ValueError("Draft exceeds safe reading limit")
    return email.message_from_bytes(raw, policy=email.policy.default)


def _item(msg, uid, include_body=False):
    body, attachments = _body(msg)
    item = {"uid": str(uid), "from": decode(msg.get("From")),
            "to": decode(msg.get("To")), "subject": decode(msg.get("Subject")),
            "date": str(msg.get("Date", "")), "message_id": str(msg.get("Message-ID", "")),
            "project": decode(msg.get("X-RMail-Project")), "attachments": attachments}
    if include_body:
        item.update(body=body[:MAX_BODY], truncated=len(body) > MAX_BODY)
    return item


def list_drafts(project="", query="", limit=30):
    if not 1 <= limit <= 100 or len(project) > 120 or len(query) > 120:
        raise ValueError("Invalid draft search")
    conn = _connect()
    try:
        folder = _open(conn)
        status, rows = conn.uid("search", None, "ALL")
        if status != "OK":
            raise RuntimeError("Cannot search Drafts")
        found = []
        for uid in reversed((rows[0] or b"").split()):
            msg = _fetch(conn, uid.decode("ascii"))
            item = _item(msg, uid.decode("ascii"))
            searchable = " ".join((item["project"], item["to"], item["subject"]))
            if project and project.casefold() not in searchable.casefold():
                continue
            if query and query.casefold() not in searchable.casefold():
                continue
            found.append(item)
            if len(found) >= limit:
                break
        return {"folder": folder, "drafts": found, "source": "live Mail.ru Drafts",
                "note": "Project matches metadata, subject or recipient; untagged drafts may need review."}
    finally:
        conn.logout()


def read_draft(uid):
    conn = _connect()
    try:
        folder = _open(conn)
        return {"folder": folder, **_item(_fetch(conn, uid), _uid(uid), True)}
    finally:
        conn.logout()


def _message(to, subject, body, project="", reply_id="", references=""):
    if not to or "\r" in to or "\n" in to or len(to) > 320:
        raise ValueError("Invalid recipient")
    if not subject or len(subject) > 500 or "\r" in subject or "\n" in subject:
        raise ValueError("Invalid subject")
    if not body.strip() or len(body) > MAX_BODY:
        raise ValueError("Body must be 1-40000 characters")
    if len(project) > 120 or "\r" in project or "\n" in project:
        raise ValueError("Invalid project")
    msg = EmailMessage()
    msg["From"] = os.environ["MAIL_USER"]
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = email.utils.format_datetime(datetime.now(timezone.utc))
    msg["Message-ID"] = make_msgid(domain=os.environ["MAIL_USER"].split("@")[-1])
    if project:
        msg["X-RMail-Project"] = project
    if reply_id:
        msg["In-Reply-To"] = reply_id
    if references:
        msg["References"] = references
    msg.set_content(body)
    return msg


def _append(conn, folder, msg):
    status, rows = conn.append(folder, r"(\\Draft)", imaplib.Time2Internaldate(
        datetime.now(timezone.utc)), msg.as_bytes(policy=email.policy.SMTP))
    if status != "OK":
        raise RuntimeError("IMAP APPEND not confirmed; check Drafts before retrying")
    # Resolve UID by unique Message-ID; do not assume APPENDUID support.
    status, found = conn.uid("search", None, "HEADER", "Message-ID", str(msg["Message-ID"]))
    if status != "OK" or not found or not found[0]:
        raise RuntimeError("APPEND accepted but UID not verified; check Drafts before retrying")
    return found[0].split()[-1].decode("ascii")


def save_draft(to, subject, body, project=""):
    conn = _connect()
    try:
        folder = _open(conn, readonly=False)
        msg = _message(to, subject, body, project)
        uid = _append(conn, folder, msg)
        return {"saved": True, "uid": uid, "folder": folder, "project": project,
                "source": "Mail.ru Drafts", "note": "Not sent"}
    finally:
        conn.logout()


def update_draft(uid, to, subject, body, project=""):
    """Never overwrite a stale draft or destroy attachment-bearing drafts."""
    conn = _connect()
    try:
        folder = _open(conn, readonly=False)
        old = _fetch(conn, uid)
        _, attachments = _body(old)
        if attachments:
            raise ValueError("Draft has attachments; editing is blocked until attachment preservation is implemented")
        msg = _message(to, subject, body, project,
                       str(old.get("In-Reply-To", "")), str(old.get("References", "")))
        new_uid = _append(conn, folder, msg)
        # Only remove the old version after the replacement is verified.
        status, _ = conn.uid("store", _uid(uid), "+FLAGS.SILENT", r"(\\Deleted)")
        if status != "OK":
            return {"saved": True, "uid": new_uid, "old_uid": _uid(uid),
                    "warning": "Old draft may remain; remove duplicate manually"}
        # UID EXPUNGE is not universally supported. Do not EXPUNGE other users' deleted mail.
        return {"saved": True, "uid": new_uid, "old_uid": _uid(uid),
                "warning": "Old draft marked Deleted; may remain until folder expunge",
                "folder": folder, "project": project}
    finally:
        conn.logout()
