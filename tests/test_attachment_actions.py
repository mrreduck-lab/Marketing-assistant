import io
import os
import sys
import unittest
import zipfile
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp_server"))
import attachment_actions as a


class FakeIMAP:
    def __init__(self, *args, **kwargs):
        self.message = EmailMessage()
        self.message.set_content("Hello")
        self.message.add_attachment(b"Name,Value\\nA,1", maintype="text", subtype="csv", filename="data.csv")
    def login(self, *args):
        return "OK", []
    def select(self, *args, **kwargs):
        return "OK", []
    def uid(self, command, uid, query):
        if query != "(UID BODY.PEEK[])":
            raise AssertionError("Must use PEEK")
        return "OK", [(b"msg", self.message.as_bytes())]
    def logout(self):
        pass


class AttachmentTests(unittest.TestCase):
    def test_uid_validation(self):
        for uid in ("", "0", "1:*", "-1"):
            with self.assertRaises(ValueError):
                a._uid(uid)

    def test_list_and_read_csv(self):
        with patch.dict(os.environ, {"MAIL_USER": "test@example.org", "MAIL_PASSWORD": "dummy"}), patch.object(a.imaplib, "IMAP4_SSL", FakeIMAP):
            listing = a.list_attachments("9")
            self.assertEqual(listing["attachments"][0]["filename"], "data.csv")
            self.assertTrue(listing["attachments"][0]["readable"])
            data = a.read_attachment("9", 1)
            self.assertEqual(data["status"], "ok")
            self.assertIn("Name,Value", data["text"])
            self.assertEqual(data["personal_data_filter"], "not_enabled")

    def test_docx_text(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("word/document.xml", '<w:document xmlns:w="urn:w"><w:t>Contract</w:t></w:document>')
        self.assertEqual(a._office(stream.getvalue(), ".docx"), "Contract")


if __name__ == "__main__":
    unittest.main()
