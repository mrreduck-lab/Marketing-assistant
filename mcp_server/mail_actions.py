"""Private, bounded IMAP/SMTP operations and a metadata-only work journal."""

from __future__ import annotations

import email
import email.policy
import html
import imaplib
import json
import os
import re
import smtplib
import sqlite3
import ssl
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from email.header import decode_header
from email.message import EmailMessage
from email.utils import getaddresses, make_msgid
from html.parser import HTMLParser
from pathlib import Path

MAX_MESSAGE_BYTES = 2_000_000
MAX_REPLY_CHARS = 20_000
MAX_HTML_CHARS = 250_000
DRAFT_TTL = 24 * 3600


class _PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.hidden += 1
        elif tag in ("p", "div", "br", "li", "tr"):
            self.text.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self.hidden = max(0, self.hidden - 1)
        elif tag in ("p", "div", "li", "tr"):
            self.text.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.text.append(data)


def decode(value):
    pieces = []
    for part, charset in decode_header(value or ""):
        if isinstance(part, bytes):
            try:
                pieces.append(part.decode(charset or "utf-8", errors="replace"))
            except (LookupError, UnicodeError):
                pieces.append(part.decode("utf-8", errors="replace"))
        else:
            pieces.append(part)
    return "".join(pieces)


def _folder():
    path = Path(os.environ.get("MAIL_STATE_DIR", "/var/lib/raschini-mail-mcp"))
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_mode & 0o077:
        raise RuntimeError("MAIL_STATE_DIR must not be accessible to other users")
    return path


@contextmanager
def database():
    path = _folder() / "work.sqlite3"
    connection = sqlite3.connect(path, timeout=10)
    os.chmod(path, 0o600)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS drafts (
                id TEXT PRIMARY KEY, uid TEXT NOT NULL, recipient TEXT NOT NULL,
                subject TEXT NOT NULL, body TEXT NOT NULL, reply_id TEXT NOT NULL,
                references_header TEXT NOT NULL, created INTEGER NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending');
            CREATE TABLE IF NOT EXISTS work (
                id INTEGER PRIMARY KEY, at TEXT NOT NULL, action TEXT NOT NULL,
                uid TEXT, party TEXT, category TEXT, note TEXT);
        """)
        connection.execute("DELETE FROM drafts WHERE state='pending' AND created < ?", (int(time.time()) - DRAFT_TTL,))
        connection.commit()
        yield connection
        connection.commit()
    finally:
        connection.close()


def _event(db, action, uid="", party="", category="", note=""):
    db.execute("INSERT INTO work(at,action,uid,party,category,note) VALUES(?,?,?,?,?,?)",
               (datetime.now(timezone.utc).isoformat(), action, uid, party[:200], category[:80], note[:500]))


@contextmanager
def inbox():
    username = os.environ.get("MAIL_USER")
    password = os.environ.get("MAIL_PASSWORD")
    if not username or not password:
        raise RuntimeError("MAIL_USER and MAIL_PASSWORD are required")
    conn = imaplib.IMAP4_SSL(os.environ.get("MAIL_HOST", "imap.mail.ru"),
                             int(os.environ.get("MAIL_PORT", "993")), timeout=20)
    try:
        conn.login(username, password)
        status, _ = conn.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError("Cannot select INBOX")
        yield conn
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _uid(uid):
    if not re.fullmatch(r"[1-9][0-9]{0,15}", str(uid)):
        raise ValueError("UID must be a positive decimal IMAP UID")
    return str(uid)


def _fetch(conn, uid):
    status, parts = conn.uid("fetch", _uid(uid), "(UID BODY.PEEK[])")
    if status != "OK":
        raise RuntimeError("IMAP fetch failed")
    raw = next((p[1] for p in parts or [] if isinstance(p, tuple) and isinstance(p[1], bytes)), None)
    if raw is None:
        raise ValueError("Message not found in INBOX")
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ValueError("Message exceeds safe read size; open it in Mail.ru")
    return email.message_from_bytes(raw, policy=email.policy.default)


def _body(msg):
    text = None
    rich = None
    attachments = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        if part.get_content_disposition() == "attachment" or filename:
            attachments.append(decode(filename)[:200] if filename else "(unnamed attachment)")
            continue
        if part.get_content_type() not in ("text/plain", "text/html"):
            continue
        data = part.get_payload(decode=True) or b""
        content = data.decode(part.get_content_charset() or "utf-8", errors="replace")
        if part.get_content_type() == "text/plain" and text is None:
            text = content
        elif part.get_content_type() == "text/html" and rich is None:
            rich = content[:MAX_HTML_CHARS]
    if text is None and rich is not None:
        parser = _PlainHTML()
        parser.feed(html.unescape(rich))
        text = "".join(parser.text)
    return text or "", attachments


def read_message(uid, max_chars=20000):
    if not 1000 <= max_chars <= 50000:
        raise ValueError("max_chars must be 1000-50000")
    with inbox() as conn:
        msg = _fetch(conn, uid)
    content, attachments = _body(msg)
    with database() as db:
        _event(db, "read", _uid(uid), decode(msg.get("From")))
    return {
        "uid": _uid(uid), "from": decode(msg.get("From")),
        "to": decode(msg.get("To")), "cc": decode(msg.get("Cc")),
        "reply_to": decode(msg.get("Reply-To")),
        "subject": decode(msg.get("Subject")), "date": str(msg.get("Date", "")),
        "message_id": str(msg.get("Message-ID", "")),
        "body": content[:max_chars], "truncated": len(content) > max_chars,
        "attachments": attachments, "attachment_contents_read": False,
    }


def read_thread(uid, scan_limit=200, max_messages=10):
    """Read related INBOX messages by Message-ID and References, without changing flags."""
    if not 1 <= scan_limit <= 500 or not 1 <= max_messages <= 20:
        raise ValueError("scan_limit must be 1-500 and max_messages 1-20")
    with inbox() as conn:
        target = _fetch(conn, uid)
        identifiers = set(re.findall(r"<[^<>\s]+>", str(target.get("References", "")) + " " + str(target.get("Message-ID", "")) + " " + str(target.get("In-Reply-To", ""))))
        if not identifiers:
            return {"messages": [read_message(uid)], "scope": "INBOX only; no Message-ID available"}
        status, data = conn.uid("search", None, "ALL")
        if status != "OK":
            raise RuntimeError("Cannot search INBOX")
        uids = data[0].split()[-scan_limit:]
        matches = []
        for candidate in uids:
            status, parts = conn.uid("fetch", candidate, "(UID BODY.PEEK[HEADER.FIELDS (MESSAGE-ID REFERENCES IN-REPLY-TO)])")
            if status != "OK":
                continue
            raw = next((p[1] for p in parts or [] if isinstance(p, tuple) and isinstance(p[1], bytes)), b"")
            header = email.message_from_bytes(raw)
            links = set(re.findall(r"<[^<>\s]+>", str(header.get("Message-ID", "")) + " " + str(header.get("References", "")) + " " + str(header.get("In-Reply-To", ""))))
            if links & identifiers or candidate.decode("ascii") == _uid(uid):
                matches.append(candidate.decode("ascii"))
        if _uid(uid) not in matches:
            matches.append(_uid(uid))
        chosen = matches[-max_messages:]
        messages = []
        for item in chosen:
            msg = target if item == _uid(uid) else _fetch(conn, item)
            body, files = _body(msg)
            messages.append({"uid": item, "from": decode(msg.get("From")), "to": decode(msg.get("To")),
                             "subject": decode(msg.get("Subject")), "date": str(msg.get("Date", "")),
                             "body": body[:20000], "truncated": len(body) > 20000,
                             "attachments": files})
    with database() as db:
        _event(db, "thread_read", _uid(uid), note=f"{len(messages)} messages")
    return {"messages": messages, "scope": "INBOX only; sent mail is not included",
            "scan_limit": scan_limit, "truncated_thread": len(matches) > max_messages}


def prepare_reply(uid, body):
    if not isinstance(body, str) or not body.strip() or len(body) > MAX_REPLY_CHARS:
        raise ValueError("Reply must have 1-20000 characters")
    with inbox() as conn:
        original = _fetch(conn, uid)
    sender = original.get("Reply-To") or original.get("From") or ""
    addresses = [address for _, address in getaddresses([str(sender)]) if address]
    if len(addresses) != 1 or "\n" in addresses[0] or "\r" in addresses[0]:
        raise ValueError("Expected exactly one reply address")
    subject = decode(original.get("Subject"))
    if not subject.casefold().startswith("re:"):
        subject = "Re: " + subject
    reply_id = str(original.get("Message-ID", ""))
    references = str(original.get("References", ""))
    draft_id = uuid.uuid4().hex[:12]
    with database() as db:
        db.execute("INSERT INTO drafts(id,uid,recipient,subject,body,reply_id,references_header,created) VALUES(?,?,?,?,?,?,?,?)",
                   (draft_id, _uid(uid), addresses[0], subject[:500], body, reply_id[:500], references[:1000], int(time.time())))
        _event(db, "draft_prepared", _uid(uid), addresses[0])
    return {"draft_id": draft_id, "to": addresses[0], "subject": subject,
            "body": body, "source_uid": _uid(uid),
            "next_step": "User must explicitly approve this exact recipient, subject and body before send_reply."}


def send_reply(draft_id, approval):
    if not re.fullmatch(r"[0-9a-f]{12}", draft_id):
        raise ValueError("Invalid draft ID")
    if approval != "SEND " + draft_id:
        raise ValueError("Explicit approval for this draft is required: SEND <draft_id>")
    smtp_user = os.environ.get("SMTP_USER") or os.environ.get("MAIL_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD") or os.environ.get("MAIL_PASSWORD")
    if not smtp_user or not smtp_password:
        raise RuntimeError("SMTP_USER and SMTP_PASSWORD are not configured")
    if smtp_user.casefold() != os.environ.get("MAIL_USER", "").casefold():
        raise RuntimeError("SMTP_USER must match the authenticated mailbox")
    with database() as db:
        row = db.execute("SELECT uid,recipient,subject,body,reply_id,references_header,created,state FROM drafts WHERE id=?", (draft_id,)).fetchone()
        if row is None or row[7] != "pending" or row[6] < time.time() - DRAFT_TTL:
            raise ValueError("Draft missing, expired or already sent/attempted")
        uid, recipient, subject, body, reply_id, references, _, _ = row
        # Commit before SMTP: a timeout is ambiguous and must never trigger automatic retry.
        db.execute("UPDATE drafts SET state='sending' WHERE id=?", (draft_id,))
        _event(db, "send_attempt", uid, recipient)
    msg = EmailMessage()
    msg["From"] = smtp_user
    msg["To"] = recipient
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=smtp_user.rsplit("@", 1)[-1])
    if reply_id:
        msg["In-Reply-To"] = reply_id
        msg["References"] = (references + " " + reply_id).strip()
    msg.set_content(body)
    try:
        with smtplib.SMTP_SSL(os.environ.get("SMTP_HOST", "smtp.mail.ru"),
                              int(os.environ.get("SMTP_PORT", "465")), context=ssl.create_default_context(), timeout=30) as smtp:
            smtp.login(smtp_user, smtp_password)
            smtp.send_message(msg, from_addr=smtp_user, to_addrs=[recipient])
    except Exception:
        with database() as db:
            _event(db, "send_uncertain", uid, recipient, note="Check Sent folder; no automatic retry")
        raise RuntimeError("SMTP outcome uncertain. Check Sent folder before attempting a new draft") from None
    with database() as db:
        db.execute("UPDATE drafts SET state='sent',body='' WHERE id=?", (draft_id,))
        _event(db, "sent", uid, recipient)
    return {"status": "sent", "to": recipient, "subject": subject, "message_id": msg["Message-ID"]}


def log_work(uid, action, category="", note=""):
    if action not in ("reviewed", "follow_up", "resolved", "opportunity", "risk"):
        raise ValueError("Unsupported work action")
    if len(note) > 500 or len(category) > 80:
        raise ValueError("Note or category too long")
    with database() as db:
        _event(db, action, _uid(uid), category=category, note=note)
    return {"logged": True, "uid": _uid(uid), "action": action}


def work_report(days=7):
    if not 1 <= days <= 90:
        raise ValueError("days must be 1-90")
    since = datetime.fromtimestamp(time.time() - days * 86400, timezone.utc).isoformat()
    with database() as db:
        rows = db.execute("SELECT at,action,uid,party,category,note FROM work WHERE at>=? ORDER BY id DESC LIMIT 500", (since,)).fetchall()
    events = [dict(zip(("at", "action", "uid", "party", "category", "note"), row)) for row in rows]
    counts = {}
    for item in events:
        counts[item["action"]] = counts.get(item["action"], 0) + 1
    return {"period_days": days, "counts": counts, "events": events,
            "scope": "Actions recorded by R Mail only; older email and work outside R Mail are not included."}


def _rules_table(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS auto_rules (
            id TEXT PRIMARY KEY, category TEXT NOT NULL, sender TEXT NOT NULL,
            subject_text TEXT NOT NULL, response TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0, uid_floor INTEGER NOT NULL DEFAULT 0,
            daily_limit INTEGER NOT NULL DEFAULT 5);
        CREATE TABLE IF NOT EXISTS auto_attempts (
            rule_id TEXT NOT NULL, uid TEXT NOT NULL, at TEXT NOT NULL,
            state TEXT NOT NULL, PRIMARY KEY(rule_id,uid));
    """)


def create_auto_rule(category, sender, subject_contains, response, daily_limit=5):
    """Create a disabled, deterministic rule; the user can inspect before enabling."""
    sender = sender.strip().lower()
    if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", sender):
        raise ValueError("An exact sender email is required")
    if not 3 <= len(subject_contains.strip()) <= 100 or not 1 <= len(response) <= 2000:
        raise ValueError("Rule requires a subject phrase and a 1-2000 character fixed response")
    if not 1 <= daily_limit <= 20 or not category.strip() or len(category) > 80:
        raise ValueError("Invalid category or daily_limit")
    rule_id = uuid.uuid4().hex[:12]
    with database() as db:
        _rules_table(db)
        db.execute("INSERT INTO auto_rules(id,category,sender,subject_text,response,daily_limit) VALUES(?,?,?,?,?,?)",
                   (rule_id, category.strip(), sender, subject_contains.strip(), response, daily_limit))
    return {"rule_id": rule_id, "enabled": False, "category": category.strip(),
            "exact_sender": sender, "subject_contains": subject_contains.strip(),
            "response": response, "daily_limit": daily_limit,
            "warning": "No automatic mail is sent until this exact rule is explicitly enabled and the worker is scheduled."}


def set_auto_rule(rule_id, enabled, approval):
    if not re.fullmatch(r"[0-9a-f]{12}", rule_id):
        raise ValueError("Invalid rule ID")
    expected = ("ENABLE " if enabled else "DISABLE ") + rule_id
    if approval != expected:
        raise ValueError("Explicit approval for this rule is required: " + expected)
    floor = 0
    if enabled:
        if not (os.environ.get("SMTP_USER") or os.environ.get("MAIL_USER")) or not (os.environ.get("SMTP_PASSWORD") or os.environ.get("MAIL_PASSWORD")):
            raise RuntimeError("SMTP is not configured")
        with inbox() as conn:
            status, data = conn.uid("search", None, "ALL")
            if status != "OK":
                raise RuntimeError("Cannot establish auto-reply starting point")
            floor = max((int(x) for x in data[0].split()), default=0)
    with database() as db:
        _rules_table(db)
        row = db.execute("SELECT category,sender,subject_text,response,daily_limit FROM auto_rules WHERE id=?", (rule_id,)).fetchone()
        if row is None:
            raise ValueError("Rule not found")
        db.execute("UPDATE auto_rules SET enabled=?,uid_floor=? WHERE id=?",
                   (int(enabled), floor if enabled else 0, rule_id))
        _event(db, "auto_rule_enabled" if enabled else "auto_rule_disabled", category=row[0], party=row[1], note=rule_id)
    return {"rule_id": rule_id, "enabled": bool(enabled), "uid_floor": floor,
            "category": row[0], "exact_sender": row[1], "subject_contains": row[2],
            "response": row[3], "daily_limit": row[4]}


def list_auto_rules():
    with database() as db:
        _rules_table(db)
        rows = db.execute("SELECT id,category,sender,subject_text,response,enabled,uid_floor,daily_limit FROM auto_rules ORDER BY rowid DESC").fetchall()
    return [dict(zip(("rule_id", "category", "exact_sender", "subject_contains", "response", "enabled", "uid_floor", "daily_limit"), row)) for row in rows]


def run_auto_replies():
    """Run once from a private timer. Only fixed text and exact sender matches."""
    rules = [r for r in list_auto_rules() if r["enabled"]]
    if not rules:
        return {"status": "no_enabled_rules", "attempted": 0}
    with inbox() as conn:
        status, data = conn.uid("search", None, "ALL")
        if status != "OK":
            raise RuntimeError("Cannot scan INBOX")
        newest = [int(x) for x in data[0].split()[-100:]]
        candidates = [(uid, _fetch(conn, str(uid))) for uid in newest if any(uid > r["uid_floor"] for r in rules)]
    attempted = 0
    for rule in rules:
        for uid, msg in candidates:
            if uid <= rule["uid_floor"]:
                continue
            sender = [address.lower() for _, address in getaddresses([str(msg.get("From", ""))])]
            reply_to = [address.lower() for _, address in getaddresses([str(msg.get("Reply-To") or msg.get("From", ""))])]
            if sender != [rule["exact_sender"]] or reply_to != sender:
                continue
            if rule["subject_contains"].casefold() not in decode(msg.get("Subject")).casefold():
                continue
            if msg.get("Auto-Submitted", "no").lower() != "no" or msg.get("List-Id") or msg.get("Precedence", "").lower() in ("bulk", "list", "junk"):
                continue
            if any(token in sender[0] for token in ("noreply", "no-reply", "mailer-daemon")):
                continue
            with database() as db:
                _rules_table(db)
                current = db.execute("SELECT enabled FROM auto_rules WHERE id=?", (rule["rule_id"],)).fetchone()
                if not current or not current[0]:
                    break
                today = datetime.now(timezone.utc).date().isoformat()
                count = db.execute("SELECT count(*) FROM auto_attempts WHERE rule_id=? AND at>=?", (rule["rule_id"], today)).fetchone()[0]
                if count >= rule["daily_limit"]:
                    break
                try:
                    db.execute("INSERT INTO auto_attempts(rule_id,uid,at,state) VALUES(?,?,?,?)",
                               (rule["rule_id"], str(uid), datetime.now(timezone.utc).isoformat(), "reserved"))
                except sqlite3.IntegrityError:
                    continue
            # A reserved UID is never retried automatically after a transport error.
            attempted += 1
            try:
                draft = prepare_reply(str(uid), rule["response"])
                send_reply(draft["draft_id"], "SEND " + draft["draft_id"])
                with database() as db:
                    db.execute("UPDATE auto_attempts SET state='sent' WHERE rule_id=? AND uid=?", (rule["rule_id"], str(uid)))
                    _event(db, "auto_sent", str(uid), sender[0], rule["category"], rule["rule_id"])
            except Exception:
                with database() as db:
                    db.execute("UPDATE auto_attempts SET state='uncertain' WHERE rule_id=? AND uid=?", (rule["rule_id"], str(uid)))
                    _event(db, "auto_send_uncertain", str(uid), sender[0], rule["category"], "Inspect Sent folder; no retry")
    return {"status": "completed", "attempted": attempted}
