import os
import sys
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp_server"))
import draft_actions as drafts


class FakeIMAP:
    def __init__(self):
        self.messages = {}
        self.next_uid = 100
        self.calls = []
    def list(self):
        return "OK", [br'(\\HasNoChildren \\Drafts) "/" "Drafts"']
    def select(self, name, readonly=True):
        self.calls.append(("select", name, readonly))
        return "OK", [b"1"]
    def uid(self, command, *args):
        self.calls.append((command, args))
        if command == "search":
            if args[0] == "ALL":
                return "OK", [" ".join(self.messages).encode()]
            return "OK", [" ".join(uid for uid, msg in self.messages.items()
                                   if str(msg["Message-ID"]) == args[-1]).encode()]
        if command == "fetch":
            msg = self.messages.get(str(args[0]))
            return "OK", [(b"draft", msg.as_bytes())] if msg else []
        if command == "store":
            return "OK", [b"stored"]
        raise AssertionError(command)
    def append(self, folder, flags, date, data):
        self.next_uid += 1
        self.messages[str(self.next_uid)] = __import__("email").message_from_bytes(
            data, policy=__import__("email").policy.default)
        return "OK", [b"saved"]
    def logout(self):
        pass


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.conn = FakeIMAP()
        self.patch = patch.object(drafts, "_connect", return_value=self.conn)
        self.patch.start()
        self.env = patch.dict(os.environ, {"MAIL_USER": "marketing@raschini.com"})
        self.env.start()
    def tearDown(self):
        self.patch.stop()
        self.env.stop()
    def test_live_save_edit_and_project_search(self):
        first = drafts.save_draft("partner@example.org", "Forbes Club", "Original", "Partnerships / Forbes")
        uid = first["uid"]
        self.assertEqual(drafts.read_draft(uid)["body"].strip(), "Original")
        self.assertEqual(len(drafts.list_drafts(project="Forbes")["drafts"]), 1)
        with self.assertRaisesRegex(ValueError, "version mismatch"):
            drafts.update_draft(uid, "partner@example.org", "Forbes Club", "Edited", "Partnerships", "<stale>")
        msgid = drafts.read_draft(uid)["message_id"]
        updated = drafts.update_draft(uid, "partner@example.org", "Forbes Club", "Edited", "Partnerships", msgid)
        self.assertEqual(drafts.read_draft(updated["uid"])["body"].strip(), "Edited")
        self.assertIn(("store", (uid, "+FLAGS.SILENT", r"(\Deleted)")), self.conn.calls)
    def test_rejects_attachment_edit(self):
        msg = drafts._message("a@example.org", "Subject", "Text")
        msg.add_attachment(b"data", maintype="application", subtype="pdf", filename="contract.pdf")
        self.conn.messages["10"] = msg
        with self.assertRaisesRegex(ValueError, "attachments"):
            drafts.update_draft("10", "a@example.org", "Subject", "New", "", str(msg["Message-ID"]))


if __name__ == "__main__":
    unittest.main()
