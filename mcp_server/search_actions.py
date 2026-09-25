"""Server-side IMAP SEARCH across selected folders; only matching headers are fetched."""
import email
import email.policy
import os
import re
from datetime import datetime

from sent_actions import _connect, _folders
from mail_actions import decode

HEADERS = "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID X-RMAIL-PROJECT)])"


def _date(value, name):
    if not value:
        return ""
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%d-%b-%Y")
    except ValueError:
        raise ValueError(name + " must be YYYY-MM-DD") from None


def _folder_names(conn, scopes):
    folders = _folders(conn)
    configured = {"sent": os.environ.get("MAIL_SENT_FOLDER", "").strip(),
                  "drafts": os.environ.get("MAIL_DRAFTS_FOLDER", "").strip()}
    names = []
    for scope in scopes:
        if scope == "inbox":
            names.append(("inbox", "INBOX"))
            continue
        if configured.get(scope):
            candidates = [f["name"] for f in folders if f["name"] == configured[scope]]
        elif scope == "sent":
            candidates = [f["name"] for f in folders if f["sent"]]
        else:
            # RFC 6154 special-use folder; never guess from localized names.
            candidates = [f["name"] for f in folders if f.get("drafts")]
        if len(candidates) != 1:
            raise RuntimeError(scope + " folder not uniquely identified; set MAIL_" + scope.upper() + "_FOLDER")
        names.append((scope, candidates[0]))
    return names


def search_all_mail(sender="", recipient="", subject="", since="", before="",
                    text="", project="", folders="inbox,sent,drafts", limit=20):
    """IMAP SEARCH runs at Mail.ru, never downloads the entire mailbox."""
    if not 1 <= limit <= 50:
        raise ValueError("limit must be 1-50")
    params = (sender, recipient, subject, text, project)
    if any(len(x) > 200 or "\r" in x or "\n" in x for x in params):
        raise ValueError("Invalid search input")
    scopes = [x.strip().lower() for x in folders.split(",")]
    if not scopes or len(scopes) != len(set(scopes)) or any(
            x not in ("inbox", "sent", "drafts") for x in scopes):
        raise ValueError("folders must be inbox,sent,drafts or a subset")
    criteria = []
    for key, val in (("FROM", sender), ("TO", recipient), ("SUBJECT", subject),
                     ("TEXT", text), ("HEADER", project)):
        if val.strip():
            criteria += [key, "X-RMail-Project", val.strip()] if key == "HEADER" else [key, val.strip()]
    if since:
        criteria += ["SINCE", _date(since, "since")]
    if before:
        criteria += ["BEFORE", _date(before, "before")]
    if not criteria:
        raise ValueError("Provide a sender, recipient, subject, date, text or project filter")
    conn = _connect()
    try:
        names = _folder_names(conn, scopes)
        found = []
        for scope, folder in names:
            status, _ = conn.select(folder, readonly=True)
            if status != "OK":
                raise RuntimeError("Cannot select " + scope)
            status, data = conn.uid("search", None, *criteria)
            if status != "OK":
                raise RuntimeError("Cannot search " + scope)
            # Fetch at most limit matched headers PER folder; never bodies/attachments.
            uids = (data[0].split() if data and data[0] else [])[-limit:]
            for uid in reversed(uids):
                status, rows = conn.uid("fetch", uid, HEADERS)
                if status != "OK":
                    continue
                raw = next((p[1] for p in rows or [] if isinstance(p, tuple) and isinstance(p[1], bytes)), None)
                if raw is None:
                    continue
                msg = email.message_from_bytes(raw, policy=email.policy.default)
                found.append({"uid": uid.decode("ascii"), "folder": folder, "scope": scope,
                              "from": decode(msg.get("From")), "to": decode(msg.get("To")),
                              "subject": decode(msg.get("Subject")), "date": str(msg.get("Date", "")),
                              "message_id": str(msg.get("Message-ID", "")),
                              "project": decode(msg.get("X-RMail-Project"))})
        def timestamp(item):
            try:
                from email.utils import parsedate_to_datetime
                return parsedate_to_datetime(item["date"]).timestamp()
            except (ValueError, TypeError, OverflowError):
                return 0
        found.sort(key=timestamp, reverse=True)
        return {"messages": found[:limit], "folders_searched": [name for _, name in names],
                "source": "Mail.ru IMAP server-side SEARCH",
                "note": "UID is specific to its folder; read with the corresponding folder tool."}
    finally:
        conn.logout()
