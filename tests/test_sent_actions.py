"""Regression checks for reusing the existing Mail.ru application password."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp_server"))
from sent_actions import smtp_status, save_sent_copy
from email.message import EmailMessage
from unittest.mock import MagicMock


class SMTPConfigurationTests(unittest.TestCase):
    def test_existing_imap_app_password_is_usable_as_smtp_configuration(self):
        with patch.dict(os.environ, {"MAIL_USER": "mail@example.com", "MAIL_PASSWORD": "secret"},
                        clear=True):
            result = smtp_status()
            self.assertTrue(result["ready_to_attempt_send"])
            self.assertEqual(result["credential_source"], "existing_mail_app_password")

    def test_explicit_smtp_credentials_take_precedence(self):
        with patch.dict(os.environ, {"MAIL_USER": "mail@example.com", "MAIL_PASSWORD": "old",
                                     "SMTP_USER": "mail@example.com", "SMTP_PASSWORD": "new"}, clear=True):
            result = smtp_status()
            self.assertTrue(result["ready_to_attempt_send"])
            self.assertEqual(result["credential_source"], "explicit_smtp")

    def test_wrong_smtp_account_blocks_sending(self):
        with patch.dict(os.environ, {"MAIL_USER": "mail@example.com", "MAIL_PASSWORD": "secret",
                                     "SMTP_USER": "other@example.com"}, clear=True):
            self.assertFalse(smtp_status()["ready_to_attempt_send"])

    def test_sent_already_present_does_not_append(self):
        msg = EmailMessage()
        msg["Message-ID"] = "<unique@example.com>"
        conn = MagicMock()
        conn.list.return_value = ("OK", [b'(\\Sent) "/" "Sent"'])
        conn.select.return_value = ("OK", [])
        conn.uid.return_value = ("OK", [b"42"])
        with patch("sent_actions._connect", return_value=conn):
            result = save_sent_copy(msg, delay_seconds=0)
        self.assertEqual(result["method"], "provider")
        conn.append.assert_not_called()

    def test_missing_sent_copy_is_appended_once(self):
        msg = EmailMessage()
        msg["Message-ID"] = "<unique@example.com>"
        msg.set_content("Hello")
        conn = MagicMock()
        conn.list.return_value = ("OK", [b'(\\Sent) "/" "Sent"'])
        conn.select.return_value = ("OK", [])
        conn.uid.side_effect = [("OK", [b""]), ("OK", [b"42"])]
        conn.append.return_value = ("OK", [b"APPENDUID"])
        with patch("sent_actions._connect", return_value=conn):
            result = save_sent_copy(msg, delay_seconds=0)
        self.assertEqual(result["method"], "imap_append")
        conn.append.assert_called_once()

    def test_no_credentials_blocks_sending(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(smtp_status()["ready_to_attempt_send"])


if __name__ == "__main__":
    unittest.main()
