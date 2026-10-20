#!/usr/bin/env python3
"""Regression tests for state-path resolution invariants (installer/path follow-up).

Covers the split-brain junk-directory hazard where an unexpanded ``$VAR`` token
became a real on-disk path (observed as stray
``$HERMES_EMAIL_WATCHDOG_STATE_ROOT`` directories holding a duplicate email.db),
plus the portable-template invariant that ``repository_contract_check`` enforces.

Invariants:
  * portable defaults -- with path env unset, ``DEFAULT_CONFIG`` paths stay the
    raw ``~``-literal template (NOT host-expanded), so the checked-in
    ``references/email_watchdog_config.template.json`` parity is hermetic;
  * load-time expansion -- ``load_config()`` resolves those raw defaults to
    absolute paths (``~`` expanded) before anything touches the filesystem;
  * never a literal ``$VAR`` -- ``_expand`` raises on a leftover *undefined*
    ``$VAR`` instead of silently passing it through (``os.path.expandvars``
    leaves undefined vars literal), so a missing env var surfaces at config load
    rather than as a divergent state path.

``email_config`` computes ``STATE_ROOT``/``DEFAULT_CONFIG`` at import time, so
each scenario reloads the module under a controlled environment.
"""
from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path

_ENV_KEYS = (
    "HERMES_EMAIL_WATCHDOG_STATE_ROOT",
    "HERMES_HOME",
    "EMAIL_WATCHDOG_CONFIG",
    "HOME",
)

_PORTABLE_DB = "~/.hermes/plugin-data/hermes-email-watchdog/email.db"


def _reload_email_config(**env):
    """Reload email_config with only the given env keys set (others cleared)."""
    saved = {k: os.environ.get(k) for k in _ENV_KEYS}
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    for k, v in env.items():
        os.environ[k] = v
    try:
        import email_config

        return importlib.reload(email_config)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class PortableDefaultTemplateTests(unittest.TestCase):
    """DEFAULT_CONFIG must stay a portable, host-independent template."""

    def test_default_paths_are_portable_raw_not_host_expanded(self):
        home = Path(tempfile.mkdtemp(prefix="qoder-cfg-home-"))
        # STATE_ROOT unset -> the default derives from HERMES_HOME's "~/.hermes".
        mod = _reload_email_config(HOME=str(home))
        db = mod.DEFAULT_CONFIG["paths"]["db"]
        self.assertEqual(db, _PORTABLE_DB)
        # Must NOT bake in the host HOME (that would break hermetic parity).
        self.assertNotIn(str(home), db)
        self.assertIn("~", db)

    def test_explicit_state_root_is_used_verbatim(self):
        root = Path(tempfile.mkdtemp(prefix="qoder-cfg-root-"))
        mod = _reload_email_config(HERMES_EMAIL_WATCHDOG_STATE_ROOT=str(root))
        self.assertEqual(str(mod.STATE_ROOT), str(root))
        self.assertEqual(
            mod.DEFAULT_CONFIG["paths"]["db"], str(root / "email.db")
        )
        self.assertEqual(
            mod.DEFAULT_CONFIG["delivery"]["calendar_path"],
            str(root / "calendar/email-watchdog.ics"),
        )


class LoadTimeExpansionTests(unittest.TestCase):
    """Raw ~-literal defaults must resolve to absolute paths at load time."""

    def test_load_config_expands_default_paths(self):
        # With STATE_ROOT unset the raw default is "~/.hermes/..."; load_config
        # must expand it to an absolute path (no literal ~) before anything uses
        # it. Asserted structurally (not pinned to a specific HOME) because ~
        # expansion happens at load time against the then-current HOME.
        mod = _reload_email_config()
        mod.reset_cache()
        cfg = mod.load_config()
        db = cfg["paths"]["db"]
        self.assertTrue(os.path.isabs(db), db)
        self.assertNotIn("~", db)
        self.assertTrue(
            db.endswith("/.hermes/plugin-data/hermes-email-watchdog/email.db"), db
        )


class ExpandUndefinedVarTests(unittest.TestCase):
    """_expand must never let an undefined $VAR pass through literally."""

    def setUp(self):
        import email_config

        self.mod = importlib.reload(email_config)

    def test_defined_var_expands(self):
        token = "QODER_TEST_DEFINED_VAR"
        os.environ[token] = "/tmp/qoder-defined"
        try:
            self.assertEqual(
                self.mod._expand(f"${token}/email.db"),
                "/tmp/qoder-defined/email.db",
            )
        finally:
            os.environ.pop(token, None)

    def test_undefined_var_raises(self):
        token = "QODER_TEST_UNDEFINED_VAR_XYZ"
        os.environ.pop(token, None)
        with self.assertRaises(ValueError):
            self.mod._expand(f"${token}/email.db")

    def test_undefined_braced_var_raises(self):
        token = "QODER_TEST_UNDEFINED_BRACED"
        os.environ.pop(token, None)
        with self.assertRaises(ValueError):
            self.mod._expand(f"${{{token}}}/seen.json")

    def test_plain_path_without_var_is_unchanged(self):
        plain = "/opt/data/plugin-data/hermes-email-watchdog/email.db"
        self.assertEqual(self.mod._expand(plain), plain)

    def test_literal_dollar_without_name_is_unchanged(self):
        # "$100" and "cost$" are not env-var references and must pass through.
        self.assertEqual(
            self.mod._expand("/tmp/cost$100/email.db"), "/tmp/cost$100/email.db"
        )


class LoadConfigUndefinedVarTests(unittest.TestCase):
    """End-to-end: load_config surfaces an undefined-var path loudly."""

    def test_load_config_raises_on_undefined_var_path(self):
        root = Path(tempfile.mkdtemp(prefix="qoder-cfg-load-"))
        cfg_path = root / "config.json"
        token = "QODER_TEST_UNDEFINED_LOAD_VAR"
        os.environ.pop(token, None)
        cfg_path.write_text(
            json.dumps({"version": 7, "paths": {"db": f"${token}/email.db"}}),
            encoding="utf-8",
        )
        mod = _reload_email_config(
            HERMES_EMAIL_WATCHDOG_STATE_ROOT=str(root),
            EMAIL_WATCHDOG_CONFIG=str(cfg_path),
        )
        with self.assertRaises(ValueError):
            mod.load_config()


if __name__ == "__main__":
    unittest.main(verbosity=2)
