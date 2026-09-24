"""R Mail MCP: private mail workflow and read-only calendar tools."""

import email
import imaplib
import os
from email.header import decode_header
from email.utils import parsedate_to_datetime

from mcp.server.fastmcp import FastMCP
from calendar_actions import list_calendars, list_events
from sent_actions import list_folders, list_sent, read_sent, smtp_status

from mail_actions import (create_auto_rule, list_auto_rules, log_work,
                          prepare_reply, read_message, read_thread, run_auto_replies,
                          send_reply, set_auto_rule, work_report)

mcp = FastMCP("Raschini Mail Reader")


def _decode(value: str | None) -> str:
    if not value:
        return ""
    result = []
    for part, charset in decode_header(value):
        if isinstance(part, bytes):
            try:
                result.append(part.decode(charset or "utf-8", errors="replace"))
            except LookupError:
                result.append(part.decode("utf-8", errors="replace"))
        else:
            result.append(part)
    return "".join(result)


def _mail():
    username = os.environ.get("MAIL_USER")
    password = os.environ.get("MAIL_PASSWORD")
    if not username or not password:
        raise RuntimeError("Mail credentials are not configured")
    host = os.environ.get("MAIL_HOST", "imap.mail.ru")
    port = int(os.environ.get("MAIL_PORT", "993"))
    connection = imaplib.IMAP4_SSL(host, port, timeout=20)
    try:
        connection.login(username, password)
        status, _ = connection.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError("Cannot open INBOX")
        return connection
    except Exception:
        try:
            connection.logout()
        finally:
            raise


def _headers(limit: int) -> list[dict[str, str]]:
    connection = _mail()
    try:
        status, data = connection.uid("search", None, "ALL")
        if status != "OK":
            raise RuntimeError("Cannot search INBOX")
        uids = data[0].split()[-limit:]
        messages = []
        for uid in reversed(uids):
            status, parts = connection.uid(
                "fetch", uid,
                "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID)])",
            )
            if status != "OK":
                continue
            raw = next(
                (part[1] for part in parts if isinstance(part, tuple) and isinstance(part[1], bytes)),
                None,
            )
            if raw is None:
                continue
            message = email.message_from_bytes(raw)
            messages.append({
                "uid": uid.decode("ascii"),
                "from": _decode(message.get("From")),
                "to": _decode(message.get("To")),
                "subject": _decode(message.get("Subject")),
                "date": message.get("Date", ""),
                "message_id": message.get("Message-ID", ""),
            })
        return messages
    finally:
        try:
            connection.logout()
        except Exception:
            pass


@mcp.tool()
def list_recent_mail(limit: int = 20) -> list[dict[str, str]]:
    """Return recent INBOX headers without marking mail read. Maximum 50."""
    if not 1 <= limit <= 50:
        raise ValueError("limit must be from 1 to 50")
    return _headers(limit)


@mcp.tool()
def find_mail(query: str, scan_limit: int = 100) -> list[dict[str, str]]:
    """Find words in sender and subject of recent INBOX headers, without reading bodies."""
    needle = query.strip().casefold()
    if not needle or len(needle) > 120:
        raise ValueError("query must be 1-120 characters")
    if not 1 <= scan_limit <= 200:
        raise ValueError("scan_limit must be from 1 to 200")
    return [
        item for item in _headers(scan_limit)
        if needle in item["from"].casefold() or needle in item["subject"].casefold()
    ]


@mcp.tool()
def list_mail_calendars() -> list[dict]:
    """Read available Mail.ru CalDAV calendars; requires separate CALDAV credentials."""
    return list_calendars()


@mcp.tool()
def list_mail_calendar_events(start_date: str, end_date: str, calendar_id: str = "", limit: int = 100) -> dict:
    """Read events in [start_date, end_date) as YYYY-MM-DD; default timezone Europe/Moscow.

    No calendar changes occur. Recurring series may appear as rules rather than expanded instances.
    """
    return list_events(start_date, end_date, calendar_id, limit)


@mcp.tool()
def read_mail(uid: str, max_chars: int = 20000) -> dict:
    """Read one INBOX message body by UID without setting Seen; attachments are listed, not opened."""
    return read_message(uid, max_chars)


@mcp.tool()
def read_mail_thread(uid: str, scan_limit: int = 200, max_messages: int = 10) -> dict:
    """Read related INBOX messages by UID and Message-ID; Sent folder is not included."""
    return read_thread(uid, scan_limit, max_messages)


@mcp.tool()
def draft_mail_reply(uid: str, body: str) -> dict:
    """Prepare an unsent reply to one sender. Show full preview and obtain user's explicit approval."""
    return prepare_reply(uid, body)


@mcp.tool()
def send_mail_reply(draft_id: str, approval: str) -> dict:
    """Send a previously previewed exact draft ONLY after user approved its recipient, subject and body.

    approval must be 'SEND <draft_id>'. Never invent user approval.
    """
    return send_reply(draft_id, approval)


@mcp.tool()
def record_mail_work(uid: str, action: str, category: str = "", note: str = "") -> dict:
    """Record a reviewed mail action or opportunity, with metadata only, for future work reports."""
    return log_work(uid, action, category, note)


@mcp.tool()
def get_mail_work_report(days: int = 7) -> dict:
    """Return work performed through R Mail; does not claim unlogged or historical work."""
    return work_report(days)


@mcp.tool()
def add_auto_reply_rule(category: str, exact_sender: str, subject_contains: str,
                        fixed_response: str, daily_limit: int = 5) -> dict:
    """Save a DISABLED rule for review. Automatic replies require exact sender and fixed text."""
    return create_auto_rule(category, exact_sender, subject_contains, fixed_response, daily_limit)


@mcp.tool()
def get_auto_reply_rules() -> list[dict]:
    """Inspect all auto-reply rules, including enabled status, exact sender and response."""
    return list_auto_rules()


@mcp.tool()
def change_auto_reply_rule(rule_id: str, enabled: bool, approval: str) -> dict:
    """Enable a reviewed rule only after explicit user approval of its exact sender, subject and response.

    approval is ENABLE <rule_id> or DISABLE <rule_id>.
    """
    return set_auto_rule(rule_id, enabled, approval)


@mcp.tool()
def execute_auto_reply_check() -> dict:
    """Check enabled rules once. A private timer can invoke the same function unattended."""
    return run_auto_replies()


@mcp.tool()
def list_mail_folders() -> dict:
    """Discover IMAP folders and the Sent special-use flag without changing mail."""
    return list_folders()


@mcp.tool()
def list_sent_mail(limit: int = 20, query: str = "") -> dict:
    """Search up to 500 recent Sent headers by recipient, sender or subject."""
    return list_sent(limit, query)


@mcp.tool()
def read_sent_mail(uid: str, max_chars: int = 20000) -> dict:
    """Read a message by UID from Sent; Sent and INBOX UIDs are independent."""
    return read_sent(uid, max_chars)


@mcp.tool()
def check_smtp_configuration() -> dict:
    """Check whether SMTP credentials are configured; never reveal secrets or send mail."""
    return smtp_status()


if __name__ == "__main__":
    mcp.run(transport="stdio")
