#!/usr/bin/env python3
from pathlib import Path
import ast,re,sys
root=Path(sys.argv[1]).resolve()
required=[
 "README.md","SKILL.md","INSTALLATION.md","SECURITY.md","CHANGELOG.md",
 "install.sh","verify.sh","upgrade.sh","rollback.sh","uninstall.sh","purge.sh",
 "diagnose.sh","VERSION","hooks/hermes-email-watchdog/HOOK.yaml",
 "hooks/hermes-email-watchdog/handler.py",
 "scripts/email_notification_renderer.py",
 "tests/test_email_outbox_nonblocking_backoff_matrix.py",
]
errors=[f"missing required file: {p}" for p in required if not (root/p).is_file()]
forbidden_modules=[
 "scripts/email_actions.py","scripts/email_commands.py","scripts/email_reply.py",
 "scripts/email_pending_processor.py",
]
errors += [f"mailbox-write module included: {p}" for p in forbidden_modules if (root/p).exists()]
for p in root.rglob("*.py"):
    try: ast.parse(p.read_text(encoding="utf-8"))
    except SyntaxError as exc: errors.append(f"syntax: {p.relative_to(root)}:{exc.lineno}")
personal=re.compile(r"(?i)\b(?:wmwen|icylonicera|augenstern)@")
for p in root.rglob("*"):
    if not p.is_file() or ".git" in p.parts: continue
    try: text=p.read_text(encoding="utf-8")
    except Exception: continue
    if personal.search(text): errors.append(f"personal identifier: {p.relative_to(root)}")
handler=(root/"hooks/hermes-email-watchdog/handler.py").read_text(encoding="utf-8")
renderer=(root/"scripts/email_notification_renderer.py").read_text(encoding="utf-8")
watch=(root/"scripts/email_watch.py").read_text(encoding="utf-8")
checks={
 "handler_nonblocking":"EMAIL_WATCHDOG_OUTBOX_NONBLOCKING_BACKOFF_V1" in handler,
 "renderer_v1e":"EMAIL_WATCHDOG_ADAPTIVE_RENDERER_V1E" in renderer and "adaptive_v1e" in renderer,
 "protocol_v1u":"readable_grounded_core_v1u" in (root/"scripts/email_config.py").read_text(encoding="utf-8"),
 "safe_thread_tracker":"email_thread_tracker" in watch and "email_reply" not in watch,
 "weixin_not_present":not (root/"weixin.py").exists(),
}
errors += [f"contract false: {k}" for k,v in checks.items() if not v]
if errors: raise SystemExit("\n".join(errors))
print("REPOSITORY_CONTRACT_OK")
