"""Profile-aware lifecycle CLI for Email Watchdog."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path


HIMALAYA_VERSION = "1.2.0"
HIMALAYA_ASSETS = {
    ("linux", "aarch64"): ("himalaya.aarch64-linux.tgz", "643020b220991fac67726f3be11310fcf806e757feadbbab3efbddd713597872"),
    ("linux", "armv6l"): ("himalaya.armv6l-linux.tgz", "1c398356bb5711bc8c3f5eff2d9d0b0fc1c35d654d0071df6923971354b5b385"),
    ("linux", "armv7l"): ("himalaya.armv7l-linux.tgz", "dfcb8e77d478ccae32a9b869e4e4899400435c3ba0c8bf7ed237c88d69efe89e"),
    ("linux", "i686"): ("himalaya.i686-linux.tgz", "6f8a67b0d439418fcd22a4171c1a373d9f4306be5c61ed0e9ff29f7a2cfec083"),
    ("linux", "x86_64"): ("himalaya.x86_64-linux.tgz", "e04e6382e3e664ef34b01afa1a2216113194a2975d2859727647b22d9b36d4e4"),
    ("darwin", "aarch64"): ("himalaya.aarch64-darwin.tgz", "f70230f4d92b5bdc505e0b482db32d587d28252173a34227d6167c109bfa64f7"),
    ("darwin", "x86_64"): ("himalaya.x86_64-darwin.tgz", "4dd2aef8e9e0bbf20bd4aaa2db9b634377c0341070c52c0e182fd615324a6378"),
    ("windows", "x86_64"): ("himalaya.x86_64-windows.zip", "e8af483e97bb66e2c48ef76695a418cf76eae13830ec2dd0d600345a38385184"),
}


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
    actions.add_parser("setup", help="Inspect guided mailbox onboarding and show the next unresolved step")
    actions.add_parser("install-runtime", help="Install or refresh the profile-scoped gateway hook")
    mail_client = actions.add_parser("himalaya-install", help="Install the vetted profile-scoped Himalaya mail client")
    mail_client.add_argument("--yes", action="store_true", help="Confirm the download and profile-scoped installation")
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


def _normalized_machine(value: str) -> str:
    machine = str(value or "").strip().casefold()
    aliases = {
        "amd64": "x86_64", "x64": "x86_64", "x86-64": "x86_64",
        "arm64": "aarch64", "i386": "i686", "i486": "i686", "i586": "i686",
    }
    return aliases.get(machine, machine)


def _himalaya_asset(system: str | None = None, machine: str | None = None) -> tuple[str, str]:
    key = (
        str(system or platform.system()).strip().casefold(),
        _normalized_machine(machine or platform.machine()),
    )
    if key not in HIMALAYA_ASSETS:
        raise RuntimeError(f"Himalaya {HIMALAYA_VERSION} has no vetted binary for {key[0]}/{key[1]}")
    return HIMALAYA_ASSETS[key]


def _download(url: str, maximum: int = 64 * 1024 * 1024) -> bytes:
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "hermes-email-watchdog"})
            with urllib.request.urlopen(request, timeout=90) as response:
                chunks: list[bytes] = []
                size = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        return b"".join(chunks)
                    size += len(chunk)
                    if size > maximum:
                        raise RuntimeError("Himalaya archive exceeds the 64 MiB safety limit")
                    chunks.append(chunk)
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(attempt + 1)
    raise RuntimeError(f"cannot download Himalaya: {last_error}")


def _binary_from_archive(asset: str, payload: bytes) -> bytes:
    expected = "himalaya.exe" if asset.endswith(".zip") else "himalaya"
    if asset.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            matches = [name for name in archive.namelist() if Path(name).name.casefold() == expected]
            if not matches:
                raise RuntimeError("Himalaya archive does not contain its executable")
            # Some official Windows releases contain both a top-level binary
            # and an identical copy under result/bin/.  Reject genuinely
            # ambiguous payloads, but do not fail a signed, checksum-pinned
            # release solely because it repeats the same bytes.
            binaries = [archive.read(name) for name in matches]
            digests = {hashlib.sha256(binary).digest() for binary in binaries}
            if len(digests) != 1:
                raise RuntimeError("Himalaya archive contains conflicting executables")
            return binaries[0]
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        matches = [member for member in archive.getmembers() if member.isfile() and Path(member.name).name == expected]
        if len(matches) != 1:
            raise RuntimeError("Himalaya archive does not contain exactly one executable")
        stream = archive.extractfile(matches[0])
        if stream is None:
            raise RuntimeError("cannot read the Himalaya executable from its archive")
        return stream.read()


def _install_himalaya(confirmed: bool) -> int:
    if not confirmed:
        print("Refusing to download Himalaya without --yes", file=sys.stderr)
        return 2
    try:
        asset, expected_hash = _himalaya_asset()
        url = f"https://github.com/pimalaya/himalaya/releases/download/v{HIMALAYA_VERSION}/{asset}"
        payload = _download(url)
        actual_hash = hashlib.sha256(payload).hexdigest()
        if actual_hash != expected_hash:
            raise RuntimeError("Himalaya archive checksum mismatch")
        binary = _binary_from_archive(asset, payload)
        if not binary or len(binary) > 128 * 1024 * 1024:
            raise RuntimeError("invalid Himalaya executable size")
        destination = _home() / "bin" / ("himalaya.exe" if os.name == "nt" else "himalaya")
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage = destination.with_name(f".{destination.stem}-{os.getpid()}{destination.suffix}")
        stage.write_bytes(binary)
        if os.name != "nt":
            stage.chmod(0o755)
        checked = subprocess.run([str(stage), "--version"], text=True, capture_output=True, timeout=15)
        if checked.returncode:
            stage.unlink(missing_ok=True)
            raise RuntimeError("downloaded Himalaya executable failed its version check")
        backup = None
        if destination.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_path = _state() / "runtime-backups" / stamp / destination.name
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            destination.replace(backup_path)
            backup = str(backup_path)
        try:
            stage.replace(destination)
        except Exception:
            if backup and Path(backup).exists() and not destination.exists():
                Path(backup).replace(destination)
            raise
        print(json.dumps({
            "ok": True,
            "version": re.sub(r"\s+", " ", checked.stdout or checked.stderr).strip()[:160],
            "binary": str(destination),
            "archive_sha256": actual_hash,
            "backup": backup,
            "scope": "hermes-profile",
        }, indent=2))
        return 0
    except Exception as exc:
        print(f"Himalaya installation failed: {exc}", file=sys.stderr)
        return 2


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
    if action == "setup":
        return _run_onboarding("status")
    if action == "install-runtime":
        return _install_runtime()
    if action == "himalaya-install":
        return _install_himalaya(bool(getattr(args, "yes", False)))
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
                    "semantic_provider": str(semantic.get("provider") or "hermes"),
                    "semantic_model": str(semantic.get("model") or "inherit"),
                    "semantic_fallback_model": str(semantic.get("fallback_model") or "inherit"),
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
