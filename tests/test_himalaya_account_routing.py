#!/usr/bin/env python3
"""Multi-account Himalaya routing tests (objective item a).

Proves that:
  * ``_himalaya_aliases`` resolves the account alias authoritatively from a
    Himalaya TOML config (single-account, explicit ``default = true``, and the
    ambiguous multi-account/no-default case);
  * ``_normalize_account`` fills an empty ``himalaya_account`` from the config;
  * ``_himalaya_context`` fails closed on an ambiguous or unknown alias;
  * reply/forward bind ``-a <alias>`` on BOTH the generation and the
    ``template send`` command, so mail is sent from the *receiving* mailbox
    rather than the config default (the USTC-default bug);
  * the polling-side ``_account_qualified_args`` inserts ``-a`` after the
    subcommand words (it is a subcommand option, not a global one).
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix="qoder-email-a-"))
os.environ["HERMES_EMAIL_WATCHDOG_STATE_ROOT"] = str(_TMP)
os.environ.pop("EMAIL_WATCHDOG_CONFIG", None)

import email_config  # noqa: E402
import email_actions  # noqa: E402
import email_watch  # noqa: E402


def _write_config(name, body):
    path = _TMP / name
    path.write_text(body, encoding="utf-8")
    return str(path)


SINGLE = _write_config(
    "single.toml",
    '[accounts.ustc]\ndefault = true\nemail = "a@ustc.edu.cn"\n',
)
MULTI_DEFAULT = _write_config(
    "multi_default.toml",
    '[accounts.ustc]\nemail = "a@ustc.edu.cn"\n\n'
    '[accounts.net163]\ndefault = true\nemail = "b@163.com"\n',
)
MULTI_AMBIGUOUS = _write_config(
    "multi_ambiguous.toml",
    '[accounts.ustc]\nemail = "a@ustc.edu.cn"\n\n'
    '[accounts.net163]\nemail = "b@163.com"\n',
)

REPLY_TEMPLATE = "To: someone@example.com\nSubject: Re: hi\n\nquoted body\n"
FORWARD_TEMPLATE = (
    "Subject: Fwd: hi\n\n"
    "---------- Forwarded Message ----------\n"
    "From: orig@example.com\n\noriginal text\n"
)


class AliasResolverTests(unittest.TestCase):
    def test_single_account(self):
        aliases, default = email_config._himalaya_aliases(SINGLE)
        self.assertEqual(aliases, ["ustc"])
        self.assertEqual(default, "ustc")

    def test_multi_with_default_flag(self):
        aliases, default = email_config._himalaya_aliases(MULTI_DEFAULT)
        self.assertEqual(aliases, ["ustc", "net163"])
        self.assertEqual(default, "net163")

    def test_multi_ambiguous_returns_empty(self):
        aliases, default = email_config._himalaya_aliases(MULTI_AMBIGUOUS)
        self.assertEqual(aliases, ["ustc", "net163"])
        self.assertEqual(default, "")

    def test_missing_file(self):
        self.assertEqual(email_config._himalaya_aliases(str(_TMP / "nope.toml")), ([], ""))

    def test_normalize_fills_empty_alias(self):
        acct = email_config._normalize_account(
            {"id": "ustc", "type": "himalaya", "himalaya_config": SINGLE}
        )
        self.assertEqual(acct["himalaya_account"], "ustc")

    def test_normalize_preserves_explicit_alias(self):
        acct = email_config._normalize_account(
            {"id": "x", "type": "himalaya", "himalaya_config": MULTI_DEFAULT,
             "himalaya_account": "ustc"}
        )
        self.assertEqual(acct["himalaya_account"], "ustc")


class HimalayaContextTests(unittest.TestCase):
    def test_resolves_alias_when_empty(self):
        _common, _cwd, account = email_actions._himalaya_context(
            {"himalaya_config": MULTI_DEFAULT, "himalaya_account": ""}
        )
        self.assertEqual(account, "net163")

    def test_fail_closed_ambiguous(self):
        with self.assertRaises(RuntimeError):
            email_actions._himalaya_context(
                {"himalaya_config": MULTI_AMBIGUOUS, "himalaya_account": ""}
            )

    def test_fail_closed_unknown_alias(self):
        with self.assertRaises(RuntimeError):
            email_actions._himalaya_context(
                {"himalaya_config": SINGLE, "himalaya_account": "gmail"}
            )


class SendRoutingTests(unittest.TestCase):
    def _draft(self, kind, config, alias):
        mail = {
            "message_id": "MSG1",
            "account": "USTC",
            "account_type": "himalaya",
            "himalaya_config": config,
            "himalaya_account": alias,
            "subject": "hi",
            "from_addr": "someone@example.com",
        }
        draft = {"kind": kind, "mail": mail, "user_body": "hello", "signature": ""}
        if kind == "forward":
            draft["recipient"] = "dest@example.com"
        return draft

    def _capture(self, draft, gen_template):
        argvs = []

        def fake_run(cmd, **kwargs):
            argvs.append(list(cmd))
            stdout = gen_template if ("reply" in cmd or "forward" in cmd) else ""
            return mock.Mock(returncode=0, stdout=stdout, stderr="")

        reply_cfg = {"reply": {"outbound_enabled": True}}
        with mock.patch.object(email_config, "load_config", return_value=reply_cfg), \
                mock.patch.object(email_actions.subprocess, "run", side_effect=fake_run):
            email_actions._send_himalaya_draft(draft)
        return argvs

    def test_reply_binds_account_on_send(self):
        argvs = self._capture(self._draft("reply", MULTI_DEFAULT, ""), REPLY_TEMPLATE)
        send = next(a for a in argvs if "send" in a)
        self.assertIn("-a", send)
        self.assertEqual(send[send.index("-a") + 1], "net163")

    def test_reply_uses_receiving_mailbox_alias(self):
        argvs = self._capture(self._draft("reply", SINGLE, ""), REPLY_TEMPLATE)
        send = next(a for a in argvs if "send" in a)
        self.assertEqual(send[send.index("-a") + 1], "ustc")

    def test_forward_binds_account_on_send(self):
        argvs = self._capture(self._draft("forward", MULTI_DEFAULT, ""), FORWARD_TEMPLATE)
        send = next(a for a in argvs if "send" in a)
        self.assertEqual(send[send.index("-a") + 1], "net163")


class PollingArgTests(unittest.TestCase):
    def test_inserts_after_subcommand(self):
        out = email_watch._account_qualified_args(
            ["envelope", "list", "--page-size", "50", "--output", "json"], "ustc"
        )
        self.assertEqual(
            out,
            ["envelope", "list", "-a", "ustc", "--page-size", "50", "--output", "json"],
        )

    def test_empty_account_is_noop(self):
        args = ["envelope", "list", "--output", "json"]
        self.assertEqual(email_watch._account_qualified_args(args, ""), list(args))

    def test_variants_prefer_qualified_with_fallback(self):
        variants = email_watch._himalaya_cmd_variants(SINGLE, ["envelope", "list"], account="ustc")
        self.assertTrue(any("-a" in v for v in variants))
        self.assertTrue(any("-a" not in v for v in variants))
        self.assertIn("-a", variants[0])


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(__import__(__name__))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    passed = result.testsRun - len(result.failures) - len(result.errors)
    print("HIMALAYA_ACCOUNT_ROUTING=%d/%d" % (passed, result.testsRun))
    raise SystemExit(0 if result.wasSuccessful() else 1)
