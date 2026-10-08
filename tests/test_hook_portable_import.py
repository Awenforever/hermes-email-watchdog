import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class HookPortableImportTests(unittest.TestCase):
    def test_hook_resolves_canonical_skills_install_without_plugins_tree(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            skill = home / "skills" / "hermes-email-watchdog"
            (skill / "scripts").mkdir(parents=True)
            shutil.copy2(ROOT / "scripts" / "portable_lock.py", skill / "scripts" / "portable_lock.py")
            with patch.dict(os.environ, {"HERMES_HOME": str(home)}, clear=False):
                spec = importlib.util.spec_from_file_location("portable_email_hook_test", ROOT / "hooks" / "hermes-email-watchdog" / "handler.py")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self.assertEqual(module.SKILL_DIR, skill)


if __name__ == "__main__":
    unittest.main()
