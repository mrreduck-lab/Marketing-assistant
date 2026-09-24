"""Provider-neutral IMAP folder discovery and bounded mailbox operations.

No provider-specific folder names are assumed. The module can move to a separate
backend without importing the legacy R Mail SQLite state or MCP runtime.
"""
from __future__ import annotations

import email
import email.policy
import imaplib
import os
import re
from contextlib import contextmanager
from email.header import decode_header
from email.message import EmailMessage
from email.utils import getaddresses
from typing import Iterator

MAX_FETCH = 2_000_000
FOLDER_LIMIT = 100
_SENT_HINTS = ("\\Sent", "sent", "отправленные", "отправлено", "sent items", "sent messages")
_DRAFT_HINTS = ("\\Drafts", "drafts", "черновики")


def _decode(raw: str | None) -> str:
    parts = []
    for part, charset in decode_header(raw or ""):
        if isinstance(part, bytes):
            try:
                parts.append(part.decode(charset or "utf-8", errors="replace"))
            except (LookupError, UnicodeError):
                parts.append(part.decode("utf-8", errors="replace"))
        else:
            parts.append(part)
    return "".join(parts)


@contextmanager
def mailbox(write: bool = False) -> Iterator[imaplib.IMAP4_SSL]:
    """Authenticate for this operation only; caller chooses a verified folder."""
    user, password = os.environ.get("MAIL_USER"), os.environ.get("MAIL_PASSWORD")
    if not user or not password:
        raise RuntimeError("MAIL_USER and MAIL_PASSWORD are required")
    conn = imaplib.IMAP4_SSL(os.environ.get("MAIL_HOST", "imap.mail.ru"),
                            int(os.environ.get("MAIL_PORT", "993")), timeout=20)
    try:
        status, _ = conn.login(user, password)
        if status != "OK":
            raise RuntimeError("IMAP login failed")
        yield conn
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def folders(conn) -> list[dict[str, str]]:
    status, response = conn.list()
    if status != "OK":
        raise RuntimeError("IMAP LIST failed")
    result = []
    for line in response or []:
        if not line:
            continue
        # LIST response: (<flags>) <delimiter> <mailbox>; quoted name can have spaces.
        match = re.match(rb'^\(([^)]*)\)\s+(?:"[^"]*"|NIL)\s+(.+)$', line)
        if not match:
            continue
        flags = match.group(1).decode("ascii", errors="replace").split()
        raw_name = match.group(2).strip()
        if raw_name.startswith(b'"') and raw_name.endswith(b'"'):
            raw_name = raw_name[1:-1].replace(b'\\\"', b'"').replace(b'\\\\', b'\\')
        try:
            name = imaplib.IMAP4._decode_utf7(raw_name)  # only on runtimes where available
        except (AttributeError, UnicodeError):
            # IMAP UTF-7 is decoded by imaplib's ASCII transport in some servers;
            # non-ASCII names are handled by explicit configuration if required.
            try:
                name = raw_name.decode("utf-8")
            except UnicodeError:
                name = raw_name.decode("ascii", errors="replace")
        result.append({"name": name, "wire_name": raw_name.decode("ascii", errors="surrogateescape"),
                       "flags": " ".join(flags)})
        if len(result) >= FOLDER_LIMIT:
            break
    return result


def resolve_folder(conn, purpose: str) -> str:
    """Find a folder from LIST: special-use flag first, human name second.

    Config values must match a folder returned by IMAP LIST (not arbitrary paths).
    """
    if purpose not in ("sent", "drafts"):
        raise ValueError("Unknown mailbox purpose")
    available = folders(conn)
    configured = os.environ.get("MAIL_SENT_FOLDER" if purpose == "sent" else "MAIL_DRAFTS_FOLDER")
    if configured:
        matches = [x for x in available if x["name"] == configured]
        if len(matches) != 1:
            raise RuntimeError("Configured " + purpose + " folder is absent in IMAP LIST")
        return matches[0]["wire_name"]
    flag = "\\Sent" if purpose == "sent" else "\\Drafts"
    marked = [x for x in available if flag.casefold() in x["flags"].casefold().split()]
    if len(marked) == 1:
        return marked[0]["wire_name"]
    hints = _SENT_HINTS if purpose == "sent" else _DRAFT_HINTS
    named = [x for x in available if x["name"].casefold().split("/")[-1] in hints]
    if len(named) == 1:
        return named[0]["wire_name"]
    raise RuntimeError("Cannot identify " + purpose + " folder safely. Configure MAIL_" +
                       ("SENT_FOLDER" if purpose == "sent" else "DRAFTS_FOLDER") +
                       " after inspecting list_mail_folders.")


def _select(conn, folder: str, readonly=True):
    # Folder is selected only after checking LIST; never accept arbitrary tool input here.
    status, _ = conn.select('"' + folder.replace('\\', '\\\\').replace('"', '\\"') + '"', readonly=readonly)
    if status != "OK":
        raise RuntimeError("Cannot select mailbox folder")


def list_folders() -> list[dict[str, str]]:
    with mailbox() as conn:
        return [{"name": x["name"], "flags": x["flags"]} for x in folders(conn)]


def sent_headers(limit=20, query="") -> list[dict]:
    if not 1 <= limit <= 50:
        raise ValueError("limit must be 1-50")
    if len(query) > 120:
        raise ValueError("query must be <=120 characters")
    with mailbox() as conn:
        folder = resolve_folder(conn, "sent")
        _select(conn, folder, readonly=True)
        status, values = conn.uid("search", None, "ALL")
        if status != "OK":
            raise RuntimeError("Cannot search Sent")
        found = []
        # bounded scan; a large mailbox must not be exhaustively exported to the model.
        for uid in reversed((values[0] or b"").split()[-500:]):
            status, chunks = conn.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID)])")
            if status != "OK":
                continue
            raw = next((x[1] for x in chunks if isinstance(x, tuple) and isinstance(x[1], bytes)), None)
            if raw is None:
                continue
            msg = email.message_from_bytes(raw, policy=email.policy.default)
            item = {"uid": uid.decode("ascii"), "to": _decode(msg.get("To")),
                    "from": _decode(msg.get("From")), "subject": _decode(msg.get("Subject")),
                    "date": str(msg.get("Date", "")), "message_id": str(msg.get("Message-ID", ""))}
            if query and query.casefold() not in (item["to"] + " " + item["subject"]).casefold():
                continue
            found.append(item)
            if len(found) == limit:
                break
        return found


def sent_message(uid: str, max_chars=20000) -> dict:
    if not re.fullmatch(r"[1-9][0-9]{0,15}", str(uid)):
        raise ValueError("Invalid IMAP UID")
    if not 1000 <= max_chars <= 50000:
        raise ValueError("max_chars must be 1000-50000")
    from mail_actions import _body
    with mailbox() as conn:
        folder = resolve_folder(conn, "sent")
        _select(conn, folder, readonly=True)
        status, chunks = conn.uid("fetch", uid, "(BODY.PEEK[])")
        if status != "OK":
            raise RuntimeError("Cannot fetch Sent message")
        raw = next((x[1] for x in chunks or [] if isinstance(x, tuple) and isinstance(x[1], bytes)), None)
        if raw is None or len(raw) > MAX_FETCH:
            raise ValueError("Sent message not found or too large")
        msg = email.message_from_bytes(raw, policy=email.policy.default)
    body, attachments = _body(msg)
    return {"uid": uid, "folder": "sent", "from": _decode(msg.get("From")),
            "to": _decode(msg.get("To")), "subject": _decode(msg.get("Subject")),
            "date": str(msg.get("Date", "")), "message_id": str(msg.get("Message-ID", "")),
            "body": body[:max_chars], "truncated": len(body) > max_chars,
            "attachments": attachments, "attachment_contents_read": False}


def has_message_id(conn, purpose: str, message_id: str) -> bool:
    if not re.fullmatch(r"<[^<>\r\n]{1,250}>", message_id):
        raise ValueError("Invalid Message-ID")
    folder = resolve_folder(conn, purpose)
    _select(conn, folder, readonly=True)
    # Quoted IMAP SEARCH parameter; generated Message-ID, never untrusted freeform.
    status, result = conn.uid("search", None, "HEADER", "Message-ID", '"' + message_id + '"')
    if status != "OK":
        raise RuntimeError("Cannot verify Message-ID in " + purpose)
    return bool(result and result[0] and result[0].strip())


def append_message(conn, purpose: str, msg: EmailMessage, flags: str = "") -> None:
    folder = resolve_folder(conn, purpose)
    raw = msg.as_bytes(policy=email.policy.SMTP)
    if len(raw) > MAX_FETCH:
        raise ValueError("Message exceeds safe APPEND limit")
    status, _ = conn.append('"' + folder.replace('\\', '\\\\').replace('"', '\\"') + '"',
                            flags or None, imaplib.Time2Internaldate(__import__("time").time()), raw)
    if status != "OK":
        raise RuntimeError("IMAP APPEND failed: " + purpose)
