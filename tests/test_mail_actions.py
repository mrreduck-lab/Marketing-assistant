import os
import sys
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp_server"))
import mail_actions as mail


class FakeIMAP:
    commands = []
    uids = [7, 8, 9]
    sender_for_10 = "Partner <partner@example.org>"
    draft_ids = set()
    sent_ids = set()
    append_fails = False

    def __init__(self, *args, **kwargs):
        self.msg = EmailMessage()
        self.msg["From"] = "Partner <partner@example.org>"
        self.msg["To"] = "marketing@raschini.com"
        self.msg["Subject"] = "Partnership"
        self.msg["Message-ID"] = "<original@example.org>"
        self.msg.set_content("Hello, let's discuss a partnership.")
        self.msg.add_attachment(b"secret-file-content", maintype="application", subtype="pdf", filename="proposal.pdf")

    def login(self, *args):
        return "OK", []

    def select(self, *args, **kwargs):
        self.selected = args[0]
        self.commands.append(("select", args, kwargs))
        return "OK", []

    def list(self):
        return "OK", [b"(\\\\HasNoChildren \\\\Drafts) \"/\" \"Drafts\"", b"(\\\\HasNoChildren \\\\Sent) \"/\" \"Sent\""]

    def append(self, folder, flags, when, data):
        self.commands.append(("append", (folder, flags)))
        if self.append_fails:
            return "NO", []
        msg = __import__("email").message_from_bytes(data)
        if "Drafts" in folder:
            self.draft_ids.add(msg["Message-ID"])
        elif "Sent" in folder:
            self.sent_ids.add(msg["Message-ID"])
        return "OK", []

    def uid(self, command, *args):
        self.commands.append((command, args))
        if command == "search" and args[0] is None and args[1] == "HEADER":
            ids = self.draft_ids if "Drafts" in self.selected else self.sent_ids
            return "OK", [b"11" if args[-1].strip('"') in ids else b""]
        if command == "search":
            return "OK", [" ".join(str(i) for i in self.uids).encode()]
        if args[0] not in ("9", "10"):
            return "OK", []
        if args[0] == "10":
            self.msg.replace_header("From", self.sender_for_10)
        return "OK", [(b"header", self.msg.as_bytes()), b")"]

    def logout(self):
        pass


class FakeSMTP:
    sent = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def login(self, *args):
        pass

    def send_message(self, message, **kwargs):
        self.sent.append((message, kwargs))


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.chmod(self.tmp.name, 0o700)
        self.environ = patch.dict(os.environ, {
            "MAIL_STATE_DIR": self.tmp.name, "MAIL_USER": "marketing@raschini.com",
            "MAIL_PASSWORD": "not-real", "SMTP_USER": "marketing@raschini.com",
            "SMTP_PASSWORD": "not-real",
        })
        self.environ.start()
        self.imap = patch.object(mail.imaplib, "IMAP4_SSL", FakeIMAP)
        self.imap.start()
        FakeIMAP.commands.clear()
        FakeIMAP.uids = [7, 8, 9]
        FakeIMAP.sender_for_10 = "Partner <partner@example.org>"
        FakeSMTP.sent.clear()
        FakeIMAP.draft_ids.clear()
        FakeIMAP.sent_ids.clear()
        FakeIMAP.append_fails = False

    def tearDown(self):
        self.imap.stop()
        self.environ.stop()
        self.tmp.cleanup()

    def test_read_uses_peek_and_omits_attachment_bytes(self):
        data = mail.read_message("9")
        self.assertIn("partnership", data["body"])
        self.assertEqual(data["attachments"], ["proposal.pdf"])
        self.assertNotIn("secret-file-content", str(data))
        self.assertIn(("select", ("INBOX",), {"readonly": True}), FakeIMAP.commands)
        self.assertIn(("fetch", ("9", "(UID BODY.PEEK[])")), FakeIMAP.commands)
        self.assertEqual(mail.work_report()["counts"]["read"], 1)

    def test_exact_approval_and_no_duplicate_send(self):
        draft = mail.prepare_reply("9", "Thank you. I will review this.")
        with patch.object(mail.smtplib, "SMTP_SSL", FakeSMTP):
            with self.assertRaises(ValueError):
                mail.send_reply(draft["draft_id"], "")
            sent = mail.send_reply(draft["draft_id"], "SEND " + draft["draft_id"])
            self.assertEqual(sent["status"], "sent")
            self.assertEqual(FakeSMTP.sent[0][1]["to_addrs"], ["partner@example.org"])
            self.assertEqual(FakeSMTP.sent[0][0]["In-Reply-To"], "<original@example.org>")
            with self.assertRaises(ValueError):
                mail.send_reply(draft["draft_id"], "SEND " + draft["draft_id"])

    def test_draft_is_really_saved_before_success(self):
        draft = mail.prepare_reply("9", "Please review the proposal.")
        self.assertTrue(draft["mailbox_draft_verified"])
        self.assertIn(draft["message_id"], FakeIMAP.draft_ids)
        self.assertTrue(any(action == "append" and args[0] == '"Drafts"' for action, args in FakeIMAP.commands if action == "append"))

    def test_imap_draft_failure_does_not_create_sendable_draft(self):
        FakeIMAP.append_fails = True
        with self.assertRaises(RuntimeError):
            mail.prepare_reply("9", "A failed draft")
        with mail.database() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM drafts").fetchone()[0], 0)

    def test_sent_already_on_provider_is_not_appended_again(self):
        draft = mail.prepare_reply("9", "No duplicate sent entry.")
        original_send = FakeSMTP.send_message
        def auto_saved(smtp, msg, **kwargs):
            FakeIMAP.sent_ids.add(msg["Message-ID"])
            original_send(smtp, msg, **kwargs)
        with patch.object(FakeSMTP, "send_message", auto_saved), patch.object(mail.smtplib, "SMTP_SSL", FakeSMTP):
            result = mail.send_reply(draft["draft_id"], "SEND " + draft["draft_id"])
        self.assertTrue(result["sent_folder_verified"])
        self.assertEqual(len([x for action, args in FakeIMAP.commands if action == "append" and args[0] == '"Sent"']), 0)

    def test_imap_sent_failure_does_not_resend_smtp(self):
        draft = mail.prepare_reply("9", "SMTP accepted but Sent unavailable.")
        FakeIMAP.append_fails = True
        with patch.object(mail.smtplib, "SMTP_SSL", FakeSMTP):
            result = mail.send_reply(draft["draft_id"], "SEND " + draft["draft_id"])
            self.assertEqual(result["status"], "smtp_accepted_sent_unverified")
            self.assertEqual(len(FakeSMTP.sent), 1)
            with self.assertRaises(ValueError):
                mail.send_reply(draft["draft_id"], "SEND " + draft["draft_id"])
        self.assertEqual(len(FakeSMTP.sent), 1)

    def test_auto_rule_stays_disabled_and_skips_existing_mail(self):
        rule = mail.create_auto_rule("partnerships", "partner@example.org", "Partnership", "Thank you")
        self.assertFalse(rule["enabled"])
        self.assertEqual(mail.run_auto_replies()["attempted"], 0)
        active = mail.set_auto_rule(rule["rule_id"], True, "ENABLE " + rule["rule_id"])
        self.assertEqual(active["uid_floor"], 9)
        self.assertEqual(mail.run_auto_replies()["attempted"], 0)
        self.assertEqual(FakeSMTP.sent, [])

    def test_auto_rule_only_sends_matching_new_message_once(self):
        rule = mail.create_auto_rule("partnerships", "partner@example.org", "Partnership", "We received your request", daily_limit=1)
        mail.set_auto_rule(rule["rule_id"], True, "ENABLE " + rule["rule_id"])
        FakeIMAP.uids = [7, 8, 9, 10]
        FakeIMAP.sender_for_10 = "Other <other@example.org>"
        with patch.object(mail.smtplib, "SMTP_SSL", FakeSMTP):
            self.assertEqual(mail.run_auto_replies()["attempted"], 0)
            FakeIMAP.sender_for_10 = "Partner <partner@example.org>"
            self.assertEqual(mail.run_auto_replies()["attempted"], 1)
            self.assertEqual(mail.run_auto_replies()["attempted"], 0)
        self.assertEqual(len(FakeSMTP.sent), 1)
        self.assertEqual(FakeSMTP.sent[0][0].get_content().strip(), "We received your request")


if __name__ == "__main__":
    unittest.main()
