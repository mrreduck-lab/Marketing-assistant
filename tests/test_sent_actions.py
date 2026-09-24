"""Regression checks for reusing the existing Mail.ru application password."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp_server"))
from sent_actions import smtp_status


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

    def test_no_credentials_blocks_sending(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(smtp_status()["ready_to_attempt_send"])


if __name__ == "__main__":
    unittest.main()
