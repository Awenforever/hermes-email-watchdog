#!/usr/bin/env python3
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import email_onboarding


class ConfigMigrationTests(unittest.TestCase):
    def test_known_v1_defaults_move_to_production_backend(self):
        old = {
            "version": 1,
            "semantic_engine": {
                "provider": "ollama",
                "endpoint": "http://127.0.0.1:11434",
                "model": "qwen2.5:3b",
            },
            "notification": {
                "renderer": "adaptive_v1e",
                "mode": "shadow",
                "production_route_enabled": False,
                "original_policy": "always",
            },
        }
        cfg = email_onboarding._sanitize_existing_config(old)
        self.assertEqual(cfg["version"], 5)
        self.assertEqual(cfg["semantic_engine"]["provider"], "hermes_openai")
        self.assertEqual(cfg["semantic_engine"]["provider_name"], "USTC")
        self.assertEqual(cfg["semantic_engine"]["model"], "deepseek-flash")
        self.assertEqual(cfg["semantic_engine"]["fallback_model"], "qwen3.6-chat")
        self.assertEqual(cfg["notification"]["renderer"], "intelligent_v2")
        self.assertEqual(cfg["notification"]["mode"], "production")
        self.assertTrue(cfg["notification"]["production_route_enabled"])
        self.assertEqual(cfg["notification"]["original_policy"], "always")

    def test_custom_v1_backend_and_route_are_preserved(self):
        old = {
            "version": 1,
            "semantic_engine": {
                "provider": "custom",
                "endpoint": "https://example.invalid/v1",
                "model": "private-model",
            },
            "notification": {
                "renderer": "private_renderer",
                "mode": "production",
                "production_route_enabled": True,
            },
        }
        original = copy.deepcopy(old)
        cfg = email_onboarding._sanitize_existing_config(old)
        self.assertEqual(old, original)
        self.assertEqual(cfg["version"], 5)
        self.assertEqual(cfg["semantic_engine"]["provider"], "custom")
        self.assertEqual(cfg["semantic_engine"]["endpoint"], "https://example.invalid/v1")
        self.assertEqual(cfg["semantic_engine"]["model"], "private-model")
        self.assertEqual(cfg["notification"]["renderer"], "private_renderer")

    def test_known_v2_policy_moves_to_model_owned_attachment_assistant(self):
        old = {
            "version": 2,
            "semantic_engine": {
                "model": "deepseek-flash", "fallback_model": "qwen3.6-chat",
                "protocol": "readable_grounded_core_v1u",
            },
            "notification": {"fast_lane_enabled": True},
            "delivery": {"auto_download_attachments": False},
        }
        cfg = email_onboarding._sanitize_existing_config(old)
        self.assertEqual(cfg["version"], 5)
        self.assertEqual(cfg["semantic_engine"]["protocol"], "readable_grounded_core_v1w")
        self.assertFalse(cfg["notification"]["fast_lane_enabled"])
        self.assertTrue(cfg["delivery"]["auto_download_attachments"])
        self.assertTrue(cfg["delivery"]["forward_attachments_to_weixin"])

    def test_v031_policy_with_reminders_already_enabled_gets_full_assistant_upgrade(self):
        old = {
            "version": 3,
            "notification": {
                "renderer": "adaptive_v1f",
                "original_max_chars": 5000,
            },
            "delivery": {
                "create_reminders": True,
                "managed_cron": False,
            },
        }
        cfg = email_onboarding._sanitize_existing_config(old)
        self.assertEqual(cfg["notification"]["renderer"], "intelligent_v2")
        self.assertEqual(cfg["notification"]["original_max_chars"], 900)
        self.assertTrue(cfg["delivery"]["create_reminders"])
        self.assertTrue(cfg["delivery"]["managed_cron"])
        self.assertTrue(cfg["delivery"]["auto_forward_safe_attachments"])
        self.assertEqual(cfg["delivery"]["reminder_offsets_minutes"], [1440, 60])

        migrated, changed = email_onboarding._migrate_known_v3_assistant_policy(old)
        self.assertTrue(changed)
        self.assertEqual(migrated["notification"]["renderer"], "adaptive_v1g")
        self.assertTrue(migrated["delivery"]["managed_cron"])
        self.assertNotIn("auto_forward_safe_attachments", old["delivery"])

    def test_v04_presentation_moves_to_intent_composer_and_state_calendar(self):
        old = {
            "version": 3,
            "notification": {
                "renderer": "adaptive_v1g",
                "mode": "production",
                "production_route_enabled": True,
            },
            "delivery": {
                "calendar_path": "~/Documents/EmailAttachments/email-watchdog-calendar.ics",
            },
        }
        migrated, changed = email_onboarding._migrate_known_v4_presentation_policy(old)
        self.assertTrue(changed)
        self.assertEqual(migrated["version"], 4)
        self.assertEqual(migrated["notification"]["renderer"], "intelligent_v2")
        self.assertIn("plugin-data/hermes-email-watchdog/calendar", migrated["delivery"]["calendar_path"])
        self.assertEqual(old["notification"]["renderer"], "adaptive_v1g")

    def test_v05_storage_moves_shipped_attachment_root_but_preserves_custom_root(self):
        shipped = {
            "version": 4,
            "notification": {"renderer": "intelligent_v2"},
            "paths": {"attachment_dir": "/opt/data/.hermes-home/EmailAttachments"},
        }
        cfg = email_onboarding._sanitize_existing_config(shipped)
        self.assertEqual(cfg["version"], 5)
        self.assertIn("plugin-data/hermes-email-watchdog/attachments", cfg["paths"]["attachment_dir"])

        custom = {
            "version": 4,
            "notification": {"renderer": "intelligent_v2"},
            "paths": {"attachment_dir": "/srv/private-mail-files"},
        }
        cfg = email_onboarding._sanitize_existing_config(custom)
        self.assertEqual(cfg["paths"]["attachment_dir"], "/srv/private-mail-files")

    def test_migrate_current_executes_v5_storage_migration_atomically(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = root / "config.json"
            config.write_text(json.dumps({
                "version": 4,
                "notification": {"renderer": "intelligent_v2"},
                "paths": {"attachment_dir": "/opt/data/.hermes-home/EmailAttachments"},
            }), encoding="utf-8")
            with mock.patch.object(email_onboarding, "CONFIG_PATH", config), mock.patch.object(
                email_onboarding, "BACKUP_DIR", root / "backups"
            ), mock.patch.object(email_onboarding, "TRANSACTION_LOCK_FILE", root / "onboarding.lock"):
                result = email_onboarding.migrate_current_config()
            migrated = json.loads(config.read_text(encoding="utf-8"))
        self.assertTrue(result["changed"])
        self.assertEqual(migrated["version"], 5)
        self.assertIn("plugin-data/hermes-email-watchdog/attachments", migrated["paths"]["attachment_dir"])

    def test_release_migration_preserves_unknown_and_identity_fields(self):
        raw = {
            "version": 2,
            "custom_extension": {"keep": "exactly"},
            "accounts": [{"id": "private", "email": "user@example.invalid"}],
            "semantic_engine": {
                "model": "deepseek-flash",
                "fallback_model": "qwen3.6-chat",
                "protocol": "readable_grounded_core_v1u",
            },
            "notification": {"fast_lane_enabled": True},
            "delivery": {
                "auto_download_attachments": False,
                "target": {"platform": "weixin", "chat_id": "secret-chat"},
            },
        }
        migrated, changed = email_onboarding._migrate_known_v2_assistant_policy(raw)
        self.assertTrue(changed)
        self.assertEqual(migrated["custom_extension"], {"keep": "exactly"})
        self.assertEqual(migrated["accounts"], raw["accounts"])
        self.assertEqual(migrated["delivery"]["target"], raw["delivery"]["target"])
        self.assertEqual(raw["version"], 2)

    def test_release_migration_leaves_custom_v2_unchanged(self):
        raw = {
            "version": 2,
            "semantic_engine": {
                "model": "deepseek-flash",
                "fallback_model": "qwen3.6-chat",
                "protocol": "my-custom-protocol",
            },
            "notification": {"fast_lane_enabled": True},
            "delivery": {"auto_download_attachments": False},
        }
        migrated, changed = email_onboarding._migrate_known_v2_assistant_policy(raw)
        self.assertFalse(changed)
        self.assertEqual(migrated, raw)


if __name__ == "__main__":
    unittest.main()
