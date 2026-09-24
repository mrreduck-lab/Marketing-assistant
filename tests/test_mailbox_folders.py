"""Isolated provider-portability checks. No production mailbox access."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp_server"))
import mailbox_folders as box


class IMAPFoldersTests(unittest.TestCase):
    def test_russian_folder_modified_utf7(self):
        # Wire form of "Отправленные" uses IMAP modified UTF-7.
        import base64
        word = "Отправленные"
        wire = "&" + base64.b64encode(word.encode("utf-16-be")).decode().rstrip("=").replace("/", ",") + "-"
        self.assertEqual(box._decode_imap_utf7(wire.encode()), word)

    def test_special_use_flag_beats_localized_label(self):
        class Conn:
            def list(self):
                return "OK", [b'(\\\\HasNoChildren \\\\Sent) "/" "Sent Items"',
                              b'(\\\\HasNoChildren \\\\Drafts) "/" "Drafts"']
        with patch.dict(os.environ, {"MAIL_SENT_FOLDER": "", "MAIL_DRAFTS_FOLDER": ""}):
            self.assertEqual(box.resolve_folder(Conn(), "sent"), "Sent Items")
            self.assertEqual(box.resolve_folder(Conn(), "drafts"), "Drafts")

    def test_missing_folder_fails_closed(self):
        class Conn:
            def list(self):
                return "OK", [b'(\\\\HasNoChildren) "/" "INBOX"']
        with patch.dict(os.environ, {"MAIL_SENT_FOLDER": ""}):
            with self.assertRaises(RuntimeError):
                box.resolve_folder(Conn(), "sent")

    def test_configured_folder_must_exist(self):
        class Conn:
            def list(self):
                return "OK", [b'(\\\\HasNoChildren \\\\Sent) "/" "Sent"']
        with patch.dict(os.environ, {"MAIL_SENT_FOLDER": "Different"}):
            with self.assertRaises(RuntimeError):
                box.resolve_folder(Conn(), "sent")

    def test_sent_uid_read_validation(self):
        for bad in ("", "1:99", "0", "-1", "hello"):
            with self.assertRaises(ValueError):
                box.sent_message(bad)


if __name__ == "__main__":
    unittest.main()
