"""Read bounded text attachments in memory; no permanent file copies."""
import email
import email.policy
import imaplib
import io
import os
import re
import zipfile
from email.header import decode_header
from xml.etree import ElementTree as ET

MAX_MESSAGE = 12_000_000
MAX_FILE = 5_000_000
MAX_TEXT = 40000
ALLOWED = {".pdf", ".docx", ".xlsx", ".txt", ".csv"}

def _uid(value):
    if not re.fullmatch(r"[1-9][0-9]{0,15}", str(value)):
        raise ValueError("Invalid INBOX UID")
    return str(value)

def _filename(value):
    parts = []
    for chunk, charset in decode_header(value or ""):
        if isinstance(chunk, bytes):
            try:
                parts.append(chunk.decode(charset or "utf-8", errors="replace"))
            except (LookupError, UnicodeError):
                parts.append(chunk.decode("utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts)[:200]

def _attachments(uid):
    user, password = os.environ.get("MAIL_USER"), os.environ.get("MAIL_PASSWORD")
    if not user or not password:
        raise RuntimeError("Mail credentials not configured")
    conn = imaplib.IMAP4_SSL(os.environ.get("MAIL_HOST", "imap.mail.ru"),
                             int(os.environ.get("MAIL_PORT", "993")), timeout=25)
    try:
        conn.login(user, password)
        status, _ = conn.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError("Cannot select INBOX")
        status, response = conn.uid("fetch", _uid(uid), "(UID BODY.PEEK[])")
        if status != "OK":
            raise RuntimeError("Cannot fetch mail")
        raw = next((p[1] for p in response or [] if isinstance(p, tuple) and isinstance(p[1], bytes)), None)
        if raw is None:
            raise ValueError("Message not found")
        if len(raw) > MAX_MESSAGE:
            raise ValueError("Message exceeds 12 MB limit")
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        return [(p, _filename(p.get_filename()) or "(unnamed)")
                for p in msg.walk() if not p.is_multipart() and
                (p.get_content_disposition() == "attachment" or p.get_filename())]
    finally:
        try:
            conn.logout()
        except Exception:
            pass

def list_attachments(uid):
    items = []
    for number, (part, filename) in enumerate(_attachments(uid), 1):
        data = part.get_payload(decode=True) or b""
        ext = os.path.splitext(filename)[1].lower()
        items.append({"index": number, "filename": filename, "mime": part.get_content_type(),
                      "size_bytes": len(data), "readable": ext in ALLOWED and len(data) <= MAX_FILE})
    return {"uid": _uid(uid), "attachments": items, "contents_read": False}

def _office(data, ext):
    chunks = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if len(archive.namelist()) > 300:
            raise ValueError("Office file has too many parts")
        if ext == ".docx":
            names = [n for n in archive.namelist() if n == "word/document.xml"]
        else:
            names = [n for n in archive.namelist() if n == "xl/sharedStrings.xml" or
                     re.fullmatch(r"xl/worksheets/sheet[0-9]+\.xml", n)]
        if not names:
            raise ValueError("No readable document text")
        for name in names[:30]:
            if archive.getinfo(name).file_size > 2_000_000:
                raise ValueError("Office XML part exceeds 2 MB")
            root = ET.fromstring(archive.read(name))
            tags = ("t",) if ext == ".docx" else ("t", "v")
            chunks.append(" ".join(node.text for node in root.iter()
                                  if node.tag.rsplit("}", 1)[-1] in tags and node.text))
    return "\n".join(chunks)

def read_attachment(uid, index):
    if type(index) is not int or index < 1:
        raise ValueError("index must be a positive integer")
    parts = _attachments(uid)
    if index > len(parts):
        raise ValueError("Attachment not found")
    part, filename = parts[index - 1]
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED:
        return {"status": "unsupported", "filename": filename,
                "reason": "Images and scans need a separate personal-data visual filter"}
    data = part.get_payload(decode=True) or b""
    if len(data) > MAX_FILE:
        return {"status": "too_large", "filename": filename, "limit_bytes": MAX_FILE}
    if ext in (".txt", ".csv"):
        text = data.decode("utf-8-sig", errors="replace")
    elif ext in (".docx", ".xlsx"):
        try:
            text = _office(data, ext)
        except (zipfile.BadZipFile, ET.ParseError, ValueError) as exc:
            return {"status": "unreadable", "filename": filename, "reason": str(exc)[:120]}
    else:
        try:
            from pypdf import PdfReader
        except ImportError:
            return {"status": "unavailable", "reason": "pypdf not installed on server"}
        try:
            pdf = PdfReader(io.BytesIO(data), strict=False)
            if len(pdf.pages) > 80:
                return {"status": "too_many_pages", "limit": 80}
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        except Exception:
            return {"status": "unreadable", "reason": "PDF text extraction failed"}
    if not text.strip():
        return {"status": "unreadable", "reason": "No extractable text; scanned files need OCR"}
    return {"status": "ok", "uid": _uid(uid), "index": index, "filename": filename,
            "text": text[:MAX_TEXT], "truncated": len(text) > MAX_TEXT,
            "personal_data_filter": "not_enabled",
            "notice": "Text may contain personal data; full PD filtering is planned separately."}
