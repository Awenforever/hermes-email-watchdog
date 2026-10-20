#!/usr/bin/env python3
"""Regression: attachment download must address Himalaya by the RAW per-mailbox
UID, not the account-scoped storage key (``account:uid``).

The #66 storage-key scoping set ``email["msg_id"] = storage_id`` (scoped) while
the raw UID lives in ``email["uid"]``. The download path used ``msg_id``, so
every attachment fetch after scoping failed with himalaya
``invalid digit found in string`` and rendered "下载失败，请在邮箱查看".
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="qoder-email-attuid-"))
os.environ["HERMES_EMAIL_WATCHDOG_STATE_ROOT"] = str(_TMP)
os.environ.pop("EMAIL_WATCHDOG_CONFIG", None)

import email_delivery  # noqa: E402
from unittest.mock import patch  # noqa: E402


class TransportUidTests(unittest.TestCase):
    def test_prefers_explicit_raw_uid(self):
        self.assertEqual(
            "987654321",
            email_delivery._transport_uid({"uid": "987654321", "msg_id": "USTC:987654321"}),
        )

    def test_strips_account_prefix_from_msg_id_when_uid_absent(self):
        self.assertEqual("42", email_delivery._transport_uid({"msg_id": "163:42"}))
        self.assertEqual("42", email_delivery._transport_uid({"id": "gmail:42"}))

    def test_bare_legacy_id_passes_through(self):
        self.assertEqual("777", email_delivery._transport_uid({"msg_id": "777"}))

    def test_empty_when_no_identifier(self):
        self.assertEqual("", email_delivery._transport_uid({}))


class DownloadAttachmentsUidTests(unittest.TestCase):
    def _email(self):
        return {
            "id": "USTC:987654321",
            "msg_id": "USTC:987654321",
            "uid": "987654321",
            "account": "USTC",
            "has_attachments": True,
            "attachments": [{"filename": "form.pdf"}],
        }

    def test_himalaya_download_receives_raw_uid_not_scoped_id(self):
        analysis = {"attachment_handling": {"policy": "download_safe"}}
        account = {"type": "himalaya", "himalaya_config": "/cfg/config_ustc.toml"}
        with patch.object(email_delivery, "_save_root", return_value=str(_TMP / "att")), \
             patch.object(email_delivery, "_policy_after_domain_guard", side_effect=lambda p, e: p), \
             patch.object(email_delivery, "_persist_attachment", return_value=None), \
             patch("time.sleep", return_value=None), \
             patch.object(email_delivery, "_download_himalaya", return_value=[]) as dl:
            email_delivery.download_attachments(self._email(), analysis, account)
        self.assertTrue(dl.called)
        # positional args: (config_path, msg_id, save_dir)
        self.assertEqual(dl.call_args.args[1], "987654321")
        self.assertNotIn("USTC:", str(dl.call_args.args[1]))

    def test_uid_recovered_from_scoped_id_when_uid_field_missing(self):
        email = self._email()
        email.pop("uid")
        analysis = {"attachment_handling": {"policy": "download_safe"}}
        account = {"type": "himalaya", "himalaya_config": "/cfg/config_ustc.toml"}
        with patch.object(email_delivery, "_save_root", return_value=str(_TMP / "att")), \
             patch.object(email_delivery, "_policy_after_domain_guard", side_effect=lambda p, e: p), \
             patch.object(email_delivery, "_persist_attachment", return_value=None), \
             patch("time.sleep", return_value=None), \
             patch.object(email_delivery, "_download_himalaya", return_value=[]) as dl:
            email_delivery.download_attachments(email, analysis, account)
        self.assertEqual(dl.call_args.args[1], "987654321")


if __name__ == "__main__":
    unittest.main(verbosity=2)
