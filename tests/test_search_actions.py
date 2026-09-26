import os
import sys
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp_server"))
import search_actions as search


class FakeIMAP:
    def __init__(self):
        self.selected = ""
        self.calls = []
    def select(self, folder, readonly=True):
        self.selected = folder
        self.calls.append(("select", folder, readonly))
        return "OK", [b"3"]
    def uid(self, command, *args):
        self.calls.append((command, self.selected, args))
        if command == "search":
            return "OK", [b"1 2"]
        if command == "fetch":
            msg = EmailMessage()
            msg["From"] = "test@example.org"
            msg["To"] = "marketing@raschini.com"
            msg["Subject"] = "Partnership"
            msg["Date"] = "Fri, 25 Sep 2026 12:00:00 +0300"
            return "OK", [(b"header", msg.as_bytes())]
        raise AssertionError(command)
    def logout(self):
        pass


class SearchTests(unittest.TestCase):
    def test_server_side_search_all_folders_only_headers(self):
        conn = FakeIMAP()
        folders = [{"name": "INBOX", "sent": False, "drafts": False},
                   {"name": "Sent", "sent": True, "drafts": False},
                   {"name": "Drafts", "sent": False, "drafts": True}]
        with patch.object(search, "_connect", return_value=conn), patch.object(search, "_folders", return_value=folders), patch.dict(os.environ, {}, clear=True):
            result = search.search_all_mail(subject="Partnership", since="2026-09-01", limit=2)
        self.assertEqual(len(result["folders_searched"]), 3)
        self.assertEqual(len(result["messages"]), 2)
        for call in conn.calls:
            if call[0] == "search":
                self.assertEqual(call[2], (None, "SUBJECT", "Partnership", "SINCE", "01-Sep-2026"))
            if call[0] == "fetch":
                self.assertIn("HEADER.FIELDS", call[2][1])
                self.assertNotIn("BODY.PEEK[]", call[2][1])
    def test_rejects_unfiltered_search(self):
        with self.assertRaisesRegex(ValueError, "Provide"):
            search.search_all_mail()
    def test_rejects_invalid_date(self):
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            search.search_all_mail(since="yesterday")


if __name__ == "__main__":
    unittest.main()
