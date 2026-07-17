#!/usr/bin/env python3
from pathlib import Path
import ast,json,re,sys

root=Path(sys.argv[1]).resolve()
required=[
 "README.md","SKILL.md","INSTALLATION.md","SECURITY.md","CHANGELOG.md","LICENSE",
 "docs/REMOTE_HISTORY_MIGRATION.md","docs/PUBLICATION_GUARD.md",
 "install.sh","setup.sh","verify.sh","upgrade.sh","rollback.sh","uninstall.sh","purge.sh",
 "diagnose.sh","VERSION","docs/ONBOARDING.md",
 "hooks/hermes-email-watchdog/HOOK.yaml","hooks/hermes-email-watchdog/handler.py",
 "scripts/email_onboarding.py","scripts/email_notification_renderer.py",
 "tests/test_email_onboarding_matrix.py","tests/test_email_state_concurrency_recovery_matrix.py",
 "tests/test_email_outbox_nonblocking_backoff_matrix.py",
]
errors=[f"missing required file: {p}" for p in required if not (root/p).is_file()]
forbidden_modules=[
 "scripts/email_actions.py","scripts/email_commands.py","scripts/email_reply.py",
 "scripts/email_pending_processor.py",
]
errors += [f"mailbox-write module included: {p}" for p in forbidden_modules if (root/p).exists()]

license_text=(root/"LICENSE").read_text(encoding="utf-8",errors="replace") if (root/"LICENSE").is_file() else ""
if "MIT License" not in license_text or "Copyright (c) 2026 Awenforever" not in license_text:
    errors.append("MIT license contract mismatch")
if (root/"LICENSE-DECISION.md").exists():
    errors.append("license decision placeholder still present")
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
baseline=(root/"tests/handler_baseline.py").read_text(encoding="utf-8")
renderer=(root/"scripts/email_notification_renderer.py").read_text(encoding="utf-8")
watch=(root/"scripts/email_watch.py").read_text(encoding="utf-8")
config_text=(root/"scripts/email_config.py").read_text(encoding="utf-8")
onboarding=(root/"scripts/email_onboarding.py").read_text(encoding="utf-8")
skill=(root/"SKILL.md").read_text(encoding="utf-8")
hook_yaml=(root/"hooks/hermes-email-watchdog/HOOK.yaml").read_text(encoding="utf-8")
toml=(root/"references/config_template.toml").read_text(encoding="utf-8").lower()
template=json.loads((root/"references/email_watchdog_config.template.json").read_text(encoding="utf-8"))
namespace={}
exec(compile(config_text,str(root/"scripts/email_config.py"),"exec"),namespace)
checks={
 "handler_nonblocking":"EMAIL_WATCHDOG_OUTBOX_NONBLOCKING_BACKOFF_V1" in handler,
 "handler_onboarding":"EMAIL_WATCHDOG_ONBOARDING_CONTEXT_CAPTURE_V1" in handler,
 "handler_state_transaction_lock":"EMAIL_WATCHDOG_STATE_TRANSACTION_LOCK_V1" in handler,
 "onboarding_transaction_lock":"EMAIL_WATCHDOG_ONBOARDING_TRANSACTION_LOCK_V1" in onboarding,
 "handler_baseline_identical":handler==baseline,
 "hook_agent_start":"agent:start" in hook_yaml and "gateway:startup" in hook_yaml,
 "renderer_v1e":"EMAIL_WATCHDOG_ADAPTIVE_RENDERER_V1E" in renderer and "adaptive_v1e" in renderer,
 "protocol_v1u":"readable_grounded_core_v1u" in config_text,
 "safe_thread_tracker":"email_thread_tracker" in watch and "email_reply" not in watch,
 "weixin_not_present":not any(p.name=="weixin.py" for p in root.rglob("weixin.py")),
 "onboarding_json_commands":all(x in onboarding for x in (
     "status","plan","apply","validate","enable","disable","capture-context","export-redacted"
 )),
 "natural_protocol":"Natural conversation onboarding" in skill and "Never ask" in skill,
 "runtime_template_parity":namespace["DEFAULT_CONFIG"]==template,
 "immutable_safety":template.get("safety")=={
     "mailbox_read_only":True,"outbound_email_enabled":False,"mailbox_mutation_enabled":False
 },
 "legacy_paths_absent":not any(x in namespace["DEFAULT_CONFIG"]["paths"] for x in (
     "drafts_dir","pending","calendar","groups","settings","invoice_dir"
 )),
 "toml_imap_only":"smtp" not in toml and "message.send" not in toml,
 "toml_no_literal_secret":"echo '" not in toml and "printf " not in toml,
}
errors += [f"contract false: {k}" for k,v in checks.items() if not v]
workflow=(root/".github/workflows/ci.yml").read_text(encoding="utf-8")
security=(root/"SECURITY.md").read_text(encoding="utf-8")
publication_guard=(root/"docs/PUBLICATION_GUARD.md").read_text(encoding="utf-8")
workflow_checks={
 "permissions_contents_read":bool(re.search(r"(?ms)^permissions:\s*\n\s+contents:\s*read\s*$",workflow)),
 "checkout_immutable":"actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5" in workflow,
 "setup_python_immutable":"actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065" in workflow,
 "checkout_credentials_disabled":"persist-credentials: false" in workflow,
 "no_mutable_action_tags":not re.search(r"uses:\s*actions/(?:checkout|setup-python)@v\d+",workflow),
 "security_route_selected":"GitHub private vulnerability reporting" in security and "Report a vulnerability" in security,
 "publication_remote_guard":"1c545a3075c3db7d92a892d7ae6799db829a3255" in publication_guard,
 "publication_forbids_force_push":"force-push" in publication_guard and "forbidden" in publication_guard,
 "deleted_token_not_reused":"confirmed deleted" in publication_guard and "must never be reused" in publication_guard,
}
errors += [f"publication contract false: {k}" for k,v in workflow_checks.items() if not v]
if errors: raise SystemExit("\n".join(errors))
print("REPOSITORY_CONTRACT_OK")
