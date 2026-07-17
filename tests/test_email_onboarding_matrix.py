#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "setup.sh"
HANDLER = ROOT / "hooks/hermes-email-watchdog/handler.py"
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import email_config

COUNT = 0


def ok(condition: bool, message: str) -> None:
    global COUNT
    if not condition:
        raise AssertionError(message)
    COUNT += 1


def write_fake_himalaya(root: Path) -> Path:
    path = root / "fake-himalaya"
    path.write_text(
        """#!/usr/bin/env python3
import json,os,sys
log=os.environ.get('FAKE_HIMALAYA_LOG')
if log:
    with open(log,'a',encoding='utf-8') as f:
        f.write(json.dumps(sys.argv[1:])+"\\n")
if os.environ.get('FAKE_HIMALAYA_FAIL')=='1':
    print('fake read-only validation failure',file=sys.stderr)
    raise SystemExit(7)
args=sys.argv[1:]
if 'envelope' in args and 'list' in args:
    print('[]')
    raise SystemExit(0)
print('{}')
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def env_for(root: Path, fake: Path) -> dict[str, str]:
    env = os.environ.copy()
    home = root / "home"
    state = home / ".hermes"
    home.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    env.update(
        {
            "HOME": str(home),
            "HERMES_EMAIL_WATCHDOG_STATE_ROOT": str(state),
            "EMAIL_WATCHDOG_CONFIG": str(state / "email_watchdog_config.json"),
            "HERMES_EMAIL_WATCHDOG_ENABLED_FILE": str(state / "email_watchdog_enabled"),
            "HERMES_EMAIL_WATCHDOG_ONBOARDING_FILE": str(state / "email_watchdog_onboarding.json"),
            "HERMES_EMAIL_WATCHDOG_ONBOARDING_BACKUP_DIR": str(state / "email_watchdog_onboarding_backups"),
            "HERMES_EMAIL_WATCHDOG_OWNED_HIMALAYA_DIR": str(state / "email_watchdog_himalaya"),
            "HERMES_EMAIL_WATCHDOG_HIMALAYA_BIN": str(fake),
            "FAKE_HIMALAYA_LOG": str(root / "himalaya.log"),
            "PYTHONPATH": str(SCRIPTS),
        }
    )
    for key in (
        "HERMES_SESSION_PLATFORM",
        "HERMES_SESSION_CHAT_ID",
        "HERMES_SESSION_THREAD_ID",
        "HERMES_SESSION_CHAT_TYPE",
        "FAKE_HIMALAYA_FAIL",
    ):
        env.pop(key, None)
    return env


def run_setup(env: dict[str, str], *args: str, input_text: str | None = None, expect: int = 0):
    cp = subprocess.run(
        ["bash", str(SETUP), *args],
        input=input_text,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    if cp.returncode != expect:
        raise AssertionError(
            f"command {args} rc={cp.returncode} expected={expect}\nstdout={cp.stdout}\nstderr={cp.stderr}"
        )
    stream = cp.stdout if cp.stdout.strip() else cp.stderr
    return json.loads(stream), cp


def explicit_account(config_path: Path) -> dict:
    return {
        "id": "primary",
        "label": "Primary",
        "email": "user@example.com",
        "type": "himalaya",
        "himalaya_config": str(config_path),
        "enabled": True,
    }


def write_existing_himalaya(path: Path, email: str = "user@example.com") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'''[accounts.primary]\nemail = "{email}"\ndisplay-name = "Primary"\ndefault = true\nbackend.type = "imap"\nbackend.host = "imap.example.com"\nbackend.port = 993\nbackend.encryption.type = "tls"\nbackend.login = "{email}"\nbackend.auth.type = "password"\nbackend.auth.cmd = "pass show mail/example"\n''',
        encoding="utf-8",
    )
    path.chmod(0o600)


def import_handler(env: dict[str, str], name: str):
    old = os.environ.copy()
    os.environ.clear()
    os.environ.update(env)
    try:
        spec = importlib.util.spec_from_file_location(name, HANDLER)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        os.environ.clear()
        os.environ.update(old)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="email-onboarding-matrix-") as raw:
        root = Path(raw)
        fake = write_fake_himalaya(root)

        # Missing configuration is redacted and disabled.
        case = root / "case-status"
        env = env_for(case, fake)
        result, _ = run_setup(env, "status", "--json")
        ok(result["configured"] is False, "missing config should be unconfigured")
        ok(result["enabled"] is False, "missing config should be disabled")
        ok(result["values_redacted"] is True and result["mailbox_access"] is False, "status safety")

        # A clear agent:start setup intent captures target metadata only.
        case = root / "case-hook"
        env = env_for(case, fake)
        handler = import_handler(env, "email_watchdog_onboarding_hook_test")
        asyncio.run(
            handler.handle(
                "agent:start",
                {
                    "platform": "weixin",
                    "chat_id": "chat-secret-123",
                    "thread_id": "",
                    "chat_type": "dm",
                    "session_id": "session-1",
                    "user_id": "user-1",
                    "message": "请安装并启用邮件监控",
                },
            )
        )
        state_path = Path(env["HERMES_EMAIL_WATCHDOG_ONBOARDING_FILE"])
        state = json.loads(state_path.read_text(encoding="utf-8"))
        ok(state["pending_target"]["chat_id"] == "chat-secret-123", "hook target capture")
        ok("message" not in state and "请安装" not in state_path.read_text(encoding="utf-8"), "hook stores no body")
        ok(stat.S_IMODE(state_path.stat().st_mode) == 0o600, "hook state mode")

        # Unrelated chat does not create onboarding state.
        case = root / "case-hook-unrelated"
        env = env_for(case, fake)
        handler = import_handler(env, "email_watchdog_onboarding_hook_unrelated_test")
        asyncio.run(handler.handle("agent:start", {"platform": "weixin", "chat_id": "x", "message": "今天天气如何"}))
        ok(not Path(env["HERMES_EMAIL_WATCHDOG_ONBOARDING_FILE"]).exists(), "unrelated chat ignored")

        # Configured delivery target takes precedence in scheduler lookup.
        case = root / "case-handler-target"
        env = env_for(case, fake)
        cfg_path = Path(env["EMAIL_WATCHDOG_CONFIG"])
        cfg_path.write_text(json.dumps({"delivery": {"target": {"platform": "weixin", "chat_id": "stored-chat"}}}), encoding="utf-8")
        handler = import_handler(env, "email_watchdog_onboarding_handler_target_test")
        ok(handler._chat_id() == "stored-chat", "handler reads structured target")

        # Plan without an account remains unresolved.
        case = root / "case-unresolved"
        env = env_for(case, fake)
        env.update({"HERMES_SESSION_PLATFORM": "weixin", "HERMES_SESSION_CHAT_ID": "chat-1"})
        result, _ = run_setup(env, "plan", "--input-json", "{}", expect=1)
        ok("account" in result["unresolved"], "missing account unresolved")

        # Standard Himalaya config is auto-detected without exposing values.
        case = root / "case-detect"
        env = env_for(case, fake)
        standard = Path(env["HOME"]) / ".config/himalaya/config.toml"
        write_existing_himalaya(standard)
        env.update({"HERMES_SESSION_PLATFORM": "weixin", "HERMES_SESSION_CHAT_ID": "chat-2"})
        result, _ = run_setup(env, "plan", "--input-json", "{}")
        ok(result["ok"] is True and result["account_source"] == "auto_detected_himalaya", "auto-detect config")
        ok(result["accounts"][0]["email"] == "u***@example.com", "plan email redaction")
        ok("chat-2" not in json.dumps(result), "plan target redaction")

        # Natural-session and explicit non-interactive target produce identical config hashes.
        account = explicit_account(standard)
        natural_input = json.dumps({"accounts": [account], "enable": False})
        natural, _ = run_setup(env, "plan", "--input-json", natural_input)
        parity_env = env_for(root / "case-parity", fake)
        write_existing_himalaya(root / "case-parity/existing.toml")
        explicit_input = json.dumps(
            {
                "accounts": [explicit_account(root / "case-parity/existing.toml")],
                "delivery_target": {"platform": "weixin", "chat_id": "chat-2"},
                "enable": False,
            }
        )
        # Use the same path so only target source differs.
        explicit_data = json.loads(explicit_input)
        explicit_data["accounts"] = [account]
        explicit, _ = run_setup(parity_env, "plan", "--input-json", json.dumps(explicit_data))
        ok(natural["config_sha256"] == explicit["config_sha256"], "natural/noninteractive parity")

        # capture-context uses session environment and redacts its output.
        case = root / "case-capture"
        env = env_for(case, fake)
        env.update({"HERMES_SESSION_PLATFORM": "weixin", "HERMES_SESSION_CHAT_ID": "capture-chat"})
        result, _ = run_setup(env, "capture-context", "--json")
        ok(result["passed"] and result["target"]["chat_id_present"], "capture context")
        ok("capture-chat" not in json.dumps(result), "capture output redacted")

        # Generated configuration is IMAP-only and provider host is inferred.
        case = root / "case-generate"
        env = env_for(case, fake)
        env.update({"HERMES_SESSION_PLATFORM": "weixin", "HERMES_SESSION_CHAT_ID": "generated-chat"})
        payload = {
            "new_himalaya": {
                "id": "gmail",
                "email": "person@gmail.com",
                "display_name": "Person",
                "secret_command": "printenv EMAIL_WATCHDOG_IMAP_PASSWORD",
            },
            "enable": False,
        }
        planned, _ = run_setup(env, "plan", "--input-json", json.dumps(payload))
        ok(planned["ok"] and planned["generated_himalaya"]["imap_only"], "IMAP-only plan")
        applied, _ = run_setup(env, "apply", "--input-json", json.dumps(payload))
        ok(applied["passed"] and applied["enabled"] is False, "safe apply disabled")
        state_root = Path(env["HERMES_EMAIL_WATCHDOG_STATE_ROOT"])
        generated = state_root / "email_watchdog_himalaya/gmail.toml"
        content = generated.read_text(encoding="utf-8")
        ok("imap.gmail.com" in content and "smtp" not in content.lower() and "message.send" not in content, "generated TOML contract")
        ok(stat.S_IMODE(generated.stat().st_mode) == 0o600, "generated TOML mode")
        cfg = json.loads(Path(env["EMAIL_WATCHDOG_CONFIG"]).read_text(encoding="utf-8"))
        ok(cfg["safety"] == email_config.DEFAULT_CONFIG["safety"], "immutable safety")
        ok(set(cfg["paths"]) == {"attachment_dir", "cache_dir", "contacts", "db", "seen", "threads"}, "legacy paths removed")

        # Validation invokes envelope list only, never message read.
        log_lines = [json.loads(x) for x in Path(env["FAKE_HIMALAYA_LOG"]).read_text(encoding="utf-8").splitlines()]
        ok(any("envelope" in x and "list" in x for x in log_lines), "envelope validation called")
        ok(not any("message" in x or "send" in x or "delete" in x for x in log_lines), "no mailbox mutation/read body")

        # Reapplying the same input is idempotent.
        before = Path(env["EMAIL_WATCHDOG_CONFIG"]).read_bytes()
        applied2, _ = run_setup(env, "apply", "--input-json", json.dumps(payload))
        after = Path(env["EMAIL_WATCHDOG_CONFIG"]).read_bytes()
        ok(applied["config_sha256"] == applied2["config_sha256"] and before == after, "idempotent apply")

        # Explicit enable validates, and disable is immediate.
        enabled, _ = run_setup(env, "enable", "--json")
        ok(enabled["passed"] and enabled["enabled"], "enable after validation")
        disabled, _ = run_setup(env, "disable", "--json")
        ok(disabled["passed"] and not disabled["enabled"], "disable")

        # Redacted export excludes raw account, target, and full paths.
        exported, _ = run_setup(env, "export-redacted", "--json")
        encoded = json.dumps(exported, ensure_ascii=False)
        ok("person@gmail.com" not in encoded and "generated-chat" not in encoded, "export identifiers redacted")
        ok(str(generated) not in encoded and "EMAIL_WATCHDOG_IMAP_PASSWORD" not in encoded, "export path/secret redacted")

        # Literal secret commands and literal secret JSON keys are rejected.
        bad = json.loads(json.dumps(payload))
        bad["new_himalaya"]["secret_command"] = "echo 'literal-password'"
        result, _ = run_setup(env_for(root / "case-bad-command", fake), "plan", "--input-json", json.dumps(bad), expect=2)
        ok("forbidden" in result["error"], "echo secret rejected")
        bad2 = {"password": "literal", "accounts": [account], "delivery_target": {"platform": "weixin", "chat_id": "x"}}
        result, _ = run_setup(env_for(root / "case-bad-value", fake), "plan", "--input-json", json.dumps(bad2), expect=2)
        ok("literal secret value" in result["error"], "secret value rejected")

        # Unsupported notification platform is rejected.
        bad3 = {"accounts": [account], "delivery_target": {"platform": "telegram", "chat_id": "x"}}
        result, _ = run_setup(env_for(root / "case-bad-platform", fake), "plan", "--input-json", json.dumps(bad3), expect=2)
        ok("Weixin" in result["error"], "unsupported platform rejected")

        # Failed validation restores exact config, enabled state, and generated file.
        case = root / "case-rollback"
        env = env_for(case, fake)
        env.update({"HERMES_SESSION_PLATFORM": "weixin", "HERMES_SESSION_CHAT_ID": "rollback-chat"})
        cfg_path = Path(env["EMAIL_WATCHDOG_CONFIG"])
        cfg_path.write_text('{"sentinel":"before"}\n', encoding="utf-8")
        enabled_path = Path(env["HERMES_EMAIL_WATCHDOG_ENABLED_FILE"])
        enabled_path.write_text("true\n", encoding="utf-8")
        before_cfg = cfg_path.read_bytes()
        before_enabled = enabled_path.read_bytes()
        env["FAKE_HIMALAYA_FAIL"] = "1"
        result, _ = run_setup(env, "apply", "--input-json", json.dumps(payload), expect=2)
        ok("validation failed" in result["error"], "failed validation reported")
        ok(cfg_path.read_bytes() == before_cfg and enabled_path.read_bytes() == before_enabled, "atomic rollback")
        ok(not (Path(env["HERMES_EMAIL_WATCHDOG_STATE_ROOT"]) / "email_watchdog_himalaya/gmail.toml").exists(), "generated file rollback")

        # Runtime default and distributable template match exactly.
        template = json.loads((ROOT / "references/email_watchdog_config.template.json").read_text(encoding="utf-8"))
        ok(email_config.DEFAULT_CONFIG == template, "runtime/template parity")
        ok(email_config.DEFAULT_CONFIG["delivery"]["create_reminders"] is False, "no reminder write default")
        ok(email_config.DEFAULT_CONFIG["safety"]["mailbox_read_only"] is True, "safety default")

        # Static TOML template has no SMTP or literal secret example.
        template_toml = (ROOT / "references/config_template.toml").read_text(encoding="utf-8").lower()
        ok("smtp" not in template_toml and "message.send" not in template_toml, "TOML template IMAP-only")
        ok("echo '" not in template_toml and 'printf ' not in template_toml, "TOML no literal secret command")

    print(f"ONBOARDING_MATRIX_OK checks={COUNT}")


if __name__ == "__main__":
    main()
