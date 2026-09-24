"""Profile-aware lifecycle CLI for Email Watchdog."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _home() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home()


def _root() -> Path:
    return Path(__file__).resolve().parent


def _state() -> Path:
    return _home() / "plugin-data" / "hermes-email-watchdog"


def _hook() -> Path:
    return _home() / "hooks" / "hermes-email-watchdog"


def register_cli(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="email_watchdog_action")
    actions.add_parser("status", help="Show runtime and account readiness")
    actions.add_parser("install-runtime", help="Install or refresh the profile-scoped gateway hook")
    actions.add_parser("enable", help="Enable read-only polling")
    actions.add_parser("disable", help="Disable polling")
    actions.add_parser("run-once", help="Poll once and print the normalized result")
    plan = actions.add_parser("onboarding-plan", help="Preview account setup without changing live state")
    plan.add_argument("--input-json", required=True)
    apply = actions.add_parser("onboarding-apply", help="Validate and atomically apply account setup")
    apply.add_argument("--input-json", required=True)
    actions.add_parser("doctor", help="Validate mailbox access and read-only safety")
    actions.add_parser("export-redacted", help="Export configuration without credentials")
    parser.set_defaults(func=email_watchdog_command)


def _install_runtime() -> int:
    source = _root() / "hooks" / "hermes-email-watchdog"
    target = _hook()
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.with_name(f".{target.name}.stage")
    if stage.exists():
        shutil.rmtree(stage)
    shutil.copytree(source, stage)
    backup = None
    if target.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = _state() / "hook-backups" / stamp
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        target.replace(backup_path)
        backup = str(backup_path)
    stage.replace(target)
    _state().mkdir(parents=True, exist_ok=True)
    print(json.dumps({"ok": True, "hook": str(target), "backup": backup}))
    return 0


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env["HERMES_EMAIL_WATCHDOG_SKILL_DIR"] = str(_root())
    env["HERMES_EMAIL_WATCHDOG_STATE_ROOT"] = str(_state())
    env["EMAIL_WATCHDOG_CONFIG"] = str(_state() / "config.json")
    return env


def _run_once() -> int:
    result = subprocess.run(
        [sys.executable, str(_root() / "scripts" / "email_watch.py")],
        env=_env(),
        text=True,
        capture_output=True,
        timeout=180,
    )
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)
    return result.returncode


def _run_onboarding(action: str, input_json: str | None = None) -> int:
    command = [sys.executable, str(_root() / "scripts" / "email_onboarding.py"), action]
    if input_json is not None:
        command.extend(["--input-json", input_json])
    else:
        command.append("--json")
    result = subprocess.run(command, env=_env(), text=True, capture_output=True, timeout=180)
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)
    return result.returncode


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _enabled() -> bool:
    marker = _state() / "enabled"
    try:
        return marker.read_text(encoding="utf-8").strip().casefold() in {
            "1", "true", "yes", "on", "enabled",
        }
    except OSError:
        return False


def email_watchdog_command(args: argparse.Namespace) -> int:
    action = getattr(args, "email_watchdog_action", None)
    if action == "install-runtime":
        return _install_runtime()
    if action == "enable":
        return _run_onboarding("enable")
    if action == "disable":
        return _run_onboarding("disable")
    if action == "run-once":
        return _run_once()
    if action == "onboarding-plan":
        return _run_onboarding("plan", getattr(args, "input_json", None))
    if action == "onboarding-apply":
        return _run_onboarding("apply", getattr(args, "input_json", None))
    if action == "doctor":
        return _run_onboarding("validate")
    if action == "export-redacted":
        return _run_onboarding("export-redacted")
    if action in {None, "status"}:
        state = _state()
        config = state / "config.json"
        config_data = _load_json(config)
        semantic = config_data.get("semantic_engine") if isinstance(config_data.get("semantic_engine"), dict) else {}
        notification = config_data.get("notification") if isinstance(config_data.get("notification"), dict) else {}
        runtime = _load_json(state / "status.json")
        print(
            json.dumps(
                {
                    "ok": True,
                    "hermes_home": str(_home()),
                    "hook_installed": (_hook() / "HOOK.yaml").is_file(),
                    "enabled": _enabled(),
                    "config_present": config.is_file(),
                    "status_file": str(state / "status.json"),
                    "scheduler_state": str(runtime.get("state") or "not_started"),
                    "semantic_provider": str(semantic.get("provider_name") or semantic.get("provider") or "USTC"),
                    "semantic_model": str(semantic.get("model") or "deepseek-flash"),
                    "semantic_fallback_model": str(semantic.get("fallback_model") or "qwen3.8-chat"),
                    "notification_renderer": str(notification.get("renderer") or "intelligent_v2"),
                    "notification_policy": "actionable",
                    "mailbox_mode": "read-only",
                },
                indent=2,
            )
        )
        return 0
    print(f"Unknown action: {action}")
    return 2
