"""Option B storage-key migration validation (#66).

Himalaya envelope ids are per-mailbox IMAP UIDs with no cross-account uniqueness
guarantee. Legacy storage used the raw UID directly as ``messages.id``; two
accounts emitting the same UID would silently overwrite each other and cross-wire
the FK children. New ingestion stores ``id = "<account>:<uid>"`` and keeps the raw
UID in ``uid`` for Himalaya transport addressing.

These tests exercise ``email_store.migrate_message_storage_keys`` against a
throwaway temp DB only — never the production database. Prod data-rollback relies
on the verified DB backup/restore path, so no down-migration is tested here.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import email_store


class StorageKeyMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / "email.db")
        self._orig_db_path = email_store.DB_PATH
        email_store.DB_PATH = self.db_path
        if getattr(email_store._local, "conn", None) is not None:
            try:
                email_store._local.conn.close()
            except Exception:
                pass
            email_store._local.conn = None
        email_store.init_db()
        self.conn = email_store._get_conn()

    def tearDown(self):
        try:
            if getattr(email_store._local, "conn", None) is not None:
                email_store._local.conn.close()
        except Exception:
            pass
        email_store._local.conn = None
        email_store.DB_PATH = self._orig_db_path
        self.temp.cleanup()

    # ── helpers ──────────────────────────────────────────────
    def insert_message(self, msg_id, account, uid=""):
        self.conn.execute(
            "INSERT INTO messages (id, account, uid, subject) VALUES (?,?,?,?)",
            (msg_id, account, uid, f"subj-{msg_id}"),
        )
        self.conn.commit()

    def insert_children(self, msg_id, account="USTC"):
        self.conn.execute(
            "INSERT INTO attachments (id, message_id, filename) VALUES (?,?,?)",
            (f"att-{msg_id}", msg_id, "f.txt"),
        )
        self.conn.execute(
            "INSERT INTO links (id, message_id, url) VALUES (?,?,?)",
            (f"lnk-{msg_id}", msg_id, "https://example.com"),
        )
        self.conn.execute(
            "INSERT INTO actions (id, message_id, action_type) VALUES (?,?,?)",
            (f"act-{msg_id}", msg_id, "reply"),
        )
        self.conn.execute(
            "INSERT INTO schedules (id, message_id, account, title) VALUES (?,?,?,?)",
            (f"sch-{msg_id}", msg_id, account, "remind"),
        )
        self.conn.commit()

    def message(self, msg_id):
        row = self.conn.execute(
            "SELECT id, account, uid FROM messages WHERE id=?", (msg_id,)
        ).fetchone()
        return None if row is None else (row[0], row[1], row[2])

    def child_message_ids(self, table):
        return {r[0] for r in self.conn.execute(f"SELECT message_id FROM {table}").fetchall()}

    def fk_violations(self):
        return self.conn.execute("PRAGMA foreign_key_check").fetchall()

    def all_ids(self):
        return {r[0] for r in self.conn.execute("SELECT id FROM messages").fetchall()}

    # ── forward migration ────────────────────────────────────
    def test_legacy_row_is_rescoped_and_uid_preserved(self):
        self.insert_message("42", "USTC", uid="")
        n = email_store.migrate_message_storage_keys(self.conn)
        self.assertEqual(1, n)
        self.assertEqual(("USTC:42", "USTC", "42"), self.message("USTC:42"))
        self.assertIsNone(self.message("42"))

    def test_two_accounts_get_distinct_scoped_keys(self):
        self.insert_message("7", "USTC")
        self.insert_message("99", "163")
        n = email_store.migrate_message_storage_keys(self.conn)
        self.assertEqual(2, n)
        self.assertEqual({"USTC:7", "163:99"}, self.all_ids())

    def test_same_uid_across_accounts_never_collides_after_scoping(self):
        self.insert_message("42", "USTC")
        email_store.migrate_message_storage_keys(self.conn)
        self.insert_message("163:42", "163", uid="42")  # new-style ingestion
        self.assertEqual({"USTC:42", "163:42"}, self.all_ids())

    def test_fk_children_are_remapped_and_integrity_holds(self):
        self.insert_message("42", "USTC")
        self.insert_children("42")
        n = email_store.migrate_message_storage_keys(self.conn)
        self.assertEqual(1, n)
        for table in ("attachments", "links", "actions", "schedules"):
            self.assertEqual({"USTC:42"}, self.child_message_ids(table))
        self.assertEqual([], self.fk_violations())

    def test_migration_is_idempotent(self):
        self.insert_message("42", "USTC")
        self.insert_children("42")
        self.assertEqual(1, email_store.migrate_message_storage_keys(self.conn))
        self.assertEqual(0, email_store.migrate_message_storage_keys(self.conn))
        self.assertIsNotNone(self.message("USTC:42"))
        self.assertIsNone(self.message("USTC:USTC:42"))
        self.assertEqual([], self.fk_violations())

    def test_already_scoped_rows_are_skipped(self):
        self.insert_message("USTC:42", "USTC", uid="42")
        n = email_store.migrate_message_storage_keys(self.conn)
        self.assertEqual(0, n)
        self.assertEqual(("USTC:42", "USTC", "42"), self.message("USTC:42"))

    def test_existing_uid_is_not_overwritten(self):
        self.insert_message("42", "USTC", uid="42")
        email_store.migrate_message_storage_keys(self.conn)
        self.assertEqual("42", self.message("USTC:42")[2])

    def test_collision_with_existing_key_refuses_and_changes_nothing(self):
        self.insert_message("USTC:42", "USTC", uid="42")
        self.insert_message("42", "USTC", uid="")
        n = email_store.migrate_message_storage_keys(self.conn)
        self.assertEqual(0, n)
        self.assertEqual(("42", "USTC", ""), self.message("42"))
        self.assertEqual(("USTC:42", "USTC", "42"), self.message("USTC:42"))

    def test_rows_without_account_are_left_alone(self):
        self.conn.execute("INSERT INTO messages (id, account, uid) VALUES ('42','','')")
        self.conn.commit()
        n = email_store.migrate_message_storage_keys(self.conn)
        self.assertEqual(0, n)
        self.assertEqual(("42", "", ""), self.message("42"))


if __name__ == "__main__":
    unittest.main()
