from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import email_assistant_composer as composer
import email_evidence_contract as evidence
import email_identity
import email_notification_renderer as renderer


class EmailIdentityTests(unittest.TestCase):
    def test_address_is_not_dropped_when_display_name_matches_local_part(self):
        message = {
            "from_name": '" augenstern "',
            "from_addr": "augenstern@agent.qq.com",
        }
        expected = "augenstern <augenstern@agent.qq.com>"
        self.assertEqual(expected, email_identity.canonical_sender(message))
        self.assertEqual(expected, composer._sender(message))
        self.assertEqual(expected, renderer._sender(message))
        self.assertEqual(expected, evidence._sender(message))

    def test_address_only_and_mapping_shapes_are_lossless(self):
        self.assertEqual(
            "sender@example.com",
            email_identity.canonical_sender({"from_addr": "sender@example.com"}),
        )
        self.assertEqual(
            "Research Bot <bot@example.com>",
            email_identity.canonical_sender(
                {"sender": {"display_name": "Research Bot", "address": "bot@example.com"}}
            ),
        )
        self.assertEqual("bot@example.com", email_identity.address_from({"email": "bot@example.com"}))


if __name__ == "__main__":
    unittest.main()
