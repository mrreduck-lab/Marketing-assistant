import os
import imaplib
import email
from email.header import decode_header

HOST = os.getenv("MAIL_HOST", "imap.mail.ru")
PORT = int(os.getenv("MAIL_PORT", "993"))
USER = os.getenv("MAIL_USER")
PASSWORD = os.getenv("MAIL_PASSWORD")


def decode(value):
    if not value:
        return ""
    result = ""
    for part, enc in decode_header(value):
        if isinstance(part, bytes):
            result += part.decode(enc or "utf-8", errors="ignore")
        else:
            result += part
    return result


def get_headers(limit=20):
    mail = imaplib.IMAP4_SSL(HOST, PORT)
    mail.login(USER, PASSWORD)
    mail.select("INBOX", readonly=True)

    _, data = mail.search(None, "ALL")
    ids = data[0].split()[-limit:]

    result = []
    for msg_id in reversed(ids):
        _, msg_data = mail.fetch(msg_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
        msg = email.message_from_bytes(msg_data[0][1])
        result.append({
            "from": decode(msg.get("From")),
            "subject": decode(msg.get("Subject")),
            "date": msg.get("Date")
        })

    mail.logout()
    return result


if __name__ == "__main__":
    for item in get_headers():
        print(item)
