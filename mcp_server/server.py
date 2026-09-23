"""Read-only MCP tools for the corporate INBOX. No mail is persisted."""

import email
import imaplib
import os
from email.header import decode_header
from email.utils import parsedate_to_datetime

from mcp.server.fastmcp import FastMCP

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


if __name__ == "__main__":
    mcp.run(transport="stdio")
