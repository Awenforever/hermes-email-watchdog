#!/usr/bin/env python3
"""Same-domain mailbox disambiguation.

The card header keeps the friendly alias (``Gmail``); an explicit 收件邮箱 line
is added ONLY when two or more enabled accounts share a mail domain, so the
common single-mailbox-per-domain case stays uncluttered. Synthetic addresses
only; no private mail, no network, no prod writes.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import email_assistant_composer as comp
import email_watch


def _chrome(email, account=None):
    return "\n".join(comp._card_chrome(email, {}, account or {}))


class AmbiguousDomainDetectionTests(unittest.TestCase):
    def test_shared_domain_flagged_unique_not(self):
        accounts = [
            {"email": "alice@example.com", "label": "Gmail"},
            {"email": "bob@example.com", "label": "Work"},
            {"email": "carol@other.org", "label": "Other"},
        ]
        with mock.patch.object(email_watch.email_config, "get_accounts", return_value=accounts):
            shared = email_watch._ambiguous_mail_domains()
        self.assertEqual(shared, {"example.com"})

    def test_no_accounts_no_shared(self):
        with mock.patch.object(email_watch.email_config, "get_accounts", return_value=[]):
            self.assertEqual(email_watch._ambiguous_mail_domains(), set())

    def test_addresses_without_email_are_ignored(self):
        accounts = [{"label": "A"}, {"email": "", "label": "B"}, {"email": "x@y.com"}]
        with mock.patch.object(email_watch.email_config, "get_accounts", return_value=accounts):
            self.assertEqual(email_watch._ambiguous_mail_domains(), set())


class CardChromeMailboxLineTests(unittest.TestCase):
    def test_header_alias_unchanged_and_no_mailbox_line_when_unique(self):
        email = {"account": "Gmail", "subject": "Hi",
                 "account_email": "alice@example.com", "account_domain_shared": False}
        text = _chrome(email)
        self.assertIn("### 📬 新邮件｜Gmail", text)
        self.assertNotIn("收件邮箱", text)

    def test_mailbox_line_appears_when_domain_shared(self):
        email = {"account": "Gmail", "subject": "Hi",
                 "account_email": "alice@example.com", "account_domain_shared": True}
        text = _chrome(email)
        # Header alias is unchanged…
        self.assertIn("### 📬 新邮件｜Gmail", text)
        # …and the receiving mailbox is disambiguated below it.
        self.assertIn("**收件邮箱** `alice@example.com`", text)

    def test_mailbox_resolution_order(self):
        # account.email wins over to_addr; account_email (message) wins over both.
        base = {"account": "Gmail", "subject": "Hi", "account_domain_shared": True,
                "to_addr": "alias+tag@example.com"}
        self.assertIn("**收件邮箱** `bob@example.com`", _chrome(base, {"email": "bob@example.com"}))
        with_msg = dict(base, account_email="carol@example.com")
        self.assertIn("**收件邮箱** `carol@example.com`",
                      _chrome(with_msg, {"email": "bob@example.com"}))

    def test_shared_flag_without_any_address_adds_nothing(self):
        email = {"account": "Gmail", "subject": "Hi", "account_domain_shared": True}
        self.assertNotIn("收件邮箱", _chrome(email))


if __name__ == "__main__":
    unittest.main()
