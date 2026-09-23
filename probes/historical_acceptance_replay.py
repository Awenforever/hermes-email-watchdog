#!/usr/bin/env python3
"""Read-only historical mailbox replay with isolated Email Watchdog state.

The probe reads selected messages through Himalaya's raw export path, executes
the real production semantic/delivery pipeline, and writes a review manifest.
It never changes mailbox flags and never sends by itself.  A separate, explicit
production enqueue step is required for Weixin acceptance.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _prepare_config(source: Path, state: Path) -> Path:
    data = json.loads(source.read_text(encoding="utf-8"))
    paths = data.setdefault("paths", {})
    for key, name in {
        "db": "email.db", "seen": "seen.json", "cache_dir": "email_cache",
        "threads": "email_threads.json", "contacts": "email_contacts.json",
        "attachment_dir": "attachments",
    }.items():
        paths[key] = str(state / name)
    delivery = data.setdefault("delivery", {})
    delivery["calendar_path"] = str(state / "calendar" / "email-watchdog.ics")
    # All reminder and learning side effects remain inside the acceptance root.
    delivery["managed_cron"] = True
    config = state / "config.json"
    _write_json(config, data)
    (state / "enabled").write_text("false\n", encoding="utf-8")
    return config


def _envelopes(binary: str, config: str, page_size: int) -> list[dict[str, Any]]:
    cmd = [binary, "-c", config, "envelope", "list", "--page-size", str(page_size), "--output", "json"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-1000:] or "Himalaya envelope list failed")
    value = json.loads(result.stdout)
    return value if isinstance(value, list) else []


def _lint(text: str, email: dict[str, Any], delivery: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    lowered = text.casefold()
    for token in ("{'text'", '"text":', "forwarded message", "举报", "退订", "[image"):
        if token.casefold() in lowered:
            errors.append(f"presentation_noise:{token}")
    status = str(delivery.get("status") or "")
    if status == "suppressed":
        return []
    if "**发件人** `" not in text:
        errors.append("sender_not_inline_code")
    if "**主题** `" not in text:
        errors.append("subject_not_inline_code")
    if re.search(r"(?mi)^\s*[-*]\s*(?:please\s+\w{0,8}|请|.*\b(?:a|an|the|to|of|for|with|or|and|provide|co))\s*$", text):
        errors.append("truncated_action")
    if re.search(r"@[A-Z0-9._%+-]+\.[A-Z]?(?:\s|$)", text, re.I):
        errors.append("truncated_email_address")
    if len(text) > 3500:
        errors.append("notification_too_long")
    if re.search(r"(?m)^>\s*>+", text):
        errors.append("nested_quote_chrome")
    if re.search(r"(?mi)^-\s*[A-Za-z][A-Za-z\s-]{6,}(?:tio|payme|verificatio|provid|pleas|\ba)$", text):
        errors.append("truncated_english_action")
    if text.count("[检查账户安全](https://account.apple.com") > 1:
        errors.append("duplicate_canonical_link")
    if "**需要处理**" in text:
        action_block = text.split("**需要处理**", 1)[1].split("\n\n**", 1)[0]
        if any(len(line) > 240 for line in action_block.splitlines() if line.startswith("- ")):
            errors.append("overlong_action")
        if any(re.match(r"^-\s+[A-Za-z]", line) for line in action_block.splitlines()):
            errors.append("untranslated_action")
    if "**行程**" in text and "**车次**" not in text:
        errors.append("ungrounded_travel_route")
    if re.search(r"(?i)disconnect|suspension|ORCID|报销审核", str(email.get("subject") or "")) and "账户确认" in text:
        errors.append("wrong_account_status_heading")
    if re.search(r"防范.*(?:诈骗|电诈)|反诈", str(email.get("subject") or "")) and "**需要处理**" in text:
        errors.append("awareness_notice_false_action")
    if re.search(r"周报|weekly", str(email.get("subject") or ""), re.I) and re.search(r"内容为文献综述|提到.+等论文", text):
        errors.append("generic_weekly_summary")
    if text.count("t2.service.giffgaff.com") > 1:
        errors.append("duplicate_tracking_links")
    for label, target in re.findall(r"\[([^\]]+)\]\((https?://[^)]+)\)", text):
        if len(target) > 500 and (label.casefold().startswith("打开 ") or re.search(r"(?i)scisig=|utm_|scholar_share", target)):
            errors.append("oversized_tracking_link")
    for line in text.splitlines():
        if "](" in line and line.count("](") != line.count(")"):
            errors.append("broken_markdown_link")
            break
    if text.count("**快捷操作**") > 1 or text.count("**附件**") > 1:
        errors.append("duplicate_section")
    source_attachments = list(email.get("attachments") or [])
    if email.get("has_attachments") and source_attachments:
        delivered = list(delivery.get("attachments") or [])
        norm = lambda value: re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").casefold())
        names = {norm(item.get("filename")) for item in delivered if isinstance(item, dict)}
        for item in source_attachments:
            name = str(item.get("filename") or item.get("name") or "") if isinstance(item, dict) else str(item)
            if name and norm(name) not in names:
                errors.append(f"attachment_missing:{name}")
        for item in delivered:
            if not isinstance(item, dict):
                continue
            if (item.get("download_status") == "downloaded" and item.get("send_to_weixin") is not True
                    and item.get("forward_reason") not in {
                        "archive_expanded", "auxiliary_invoice_xml", "invoice_inline_asset",
                    }):
                errors.append(f"attachment_not_forwardable:{item.get('filename')}")
    return sorted(set(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-config", required=True)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--page-size", type=int, default=500)
    parser.add_argument("message_ids", nargs="+")
    args = parser.parse_args()

    state = Path(args.state_root).resolve()
    state.mkdir(parents=True, exist_ok=True)
    config_path = _prepare_config(Path(args.source_config), state)
    os.environ["HERMES_HOME"] = "/opt/data"
    os.environ["HERMES_EMAIL_WATCHDOG_STATE_ROOT"] = str(state)
    os.environ["EMAIL_WATCHDOG_CONFIG"] = str(config_path)

    scripts = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts))
    import email_config
    import email_delivery
    import email_llm
    import email_store
    import email_watch

    email_config.reset_cache()
    cfg = email_config.load_config()
    account = next((a for a in cfg.get("accounts", []) if a.get("enabled", True)), None)
    if not account or account.get("type") != "himalaya":
        raise SystemExit("an enabled Himalaya account is required")
    himalaya_config = account.get("himalaya_config") or account.get("config")
    binary = str(Path("/opt/data/bin/himalaya"))
    inventory = {str(row.get("id")): row for row in _envelopes(binary, himalaya_config, args.page_size)}

    results = []
    for index, message_id in enumerate(args.message_ids, start=1):
        env = inventory.get(str(message_id))
        if not env:
            results.append({"message_id": str(message_id), "errors": ["envelope_not_found"]})
            continue
        message = email_watch.read_himalaya(himalaya_config, str(message_id))
        if not isinstance(message, dict):
            results.append({"message_id": str(message_id), "errors": ["raw_message_unavailable"]})
            continue
        body = email_watch._extract_message_body(message)
        sender = env.get("from") if isinstance(env.get("from"), dict) else {}
        recipient = env.get("to") if isinstance(env.get("to"), dict) else {}
        attachments = env.get("attachments") or message.get("attachments") or message.get("attachment_list") or []
        links = message.get("links") or email_watch._extract_links_from_text(body)
        from_addr = str(sender.get("addr") or "")
        email = {
            "id": str(message_id), "msg_id": str(message_id),
            "account": account.get("label") or account.get("name") or account.get("id") or "Email",
            "subject": str(env.get("subject") or ""),
            "from_addr": from_addr, "from_email": from_addr.casefold(),
            "from_domain": from_addr.rsplit("@", 1)[-1].casefold() if "@" in from_addr else "",
            "from_name": str(sender.get("name") or ""), "to_addr": str(recipient.get("addr") or ""),
            "body": body[:12000], "date_sent": str(env.get("date") or ""),
            "has_attachments": bool(env.get("has_attachment") or env.get("has_attachments") or attachments),
            "has_attachment": bool(env.get("has_attachment") or env.get("has_attachments") or attachments),
            "attachments": attachments, "links": links, "has_links": bool(links),
        }
        rule = email_watch.classify_rule(email)
        # Mirror the production ingestion contract so attachment/action rows
        # retain their foreign-key parent while all records stay isolated.
        email_store.upsert_message({
            "id": str(message_id), "account": email["account"],
            "subject": email["subject"], "from_name": email["from_name"],
            "from_email": email["from_email"], "from_domain": email["from_domain"],
            "date_sent": email["date_sent"],
            "has_attachment": 1 if email["has_attachments"] else 0,
            "has_links": 1 if links else 0, "push_status": "acceptance_replay",
        })
        analysis = dict(email_llm.fallback_analysis(email, rule, "") or {})
        analysis.update({"llm_notes": "", "should_notify": True, "production_semantic_route_pending": True})
        delivery = email_delivery.deliver_email(email, rule, analysis, account)
        text = str(delivery.get("notification_text") or "").strip()
        acceptance_id = f"{args.run_id}-{index:02d}"
        tagged = text + f"\n\n---\n`Email Watchdog 验收 {acceptance_id}`" if text else ""
        decision = ((delivery.get("semantic") or {}).get("decision") or {})
        status = str(delivery.get("status") or "")
        category = str((decision.get("classification") or {}).get("category") or "")
        errors = _lint(text, email, delivery)
        if delivery.get("legacy_fallback_used") or delivery.get("production_route") != "intelligent_v2":
            errors.append("semantic_production_route_failed")
        if not text and status != "suppressed":
            errors.append("empty_notification")
        acceptable_spam_suppression = bool(
            re.search(r"(?i)^\s*\[(?:spam|junk)\]", email.get("subject") or "")
            and category in {"academic_opportunity_call", "newsletter_marketing"}
        )
        if status == "suppressed" and category not in {"newsletter_marketing"} and not acceptable_spam_suppression:
            errors.append(f"unexpected_suppression:{category or 'unknown'}")
        if category == "newsletter_marketing" and status != "suppressed":
            errors.append("marketing_not_suppressed")
        errors = sorted(set(errors))
        item = {
            "acceptance_id": acceptance_id,
            "message_id": str(message_id), "subject": email["subject"],
            "semantic_model": (delivery.get("semantic") or {}).get("model"),
            "production_route": delivery.get("production_route"),
            "route_lane": delivery.get("route_lane"),
            "status": status, "category": category, "decision": decision,
            "text": text, "tagged_text": tagged,
            "attachments": delivery.get("attachments") or [],
            "delivery_warnings": delivery.get("delivery_warnings") or [],
            "errors": errors,
        }
        _write_json(state / "results" / f"{acceptance_id}.json", item)
        results.append(item)
        print(json.dumps({k: item[k] for k in ("acceptance_id", "message_id", "subject", "semantic_model", "errors")}, ensure_ascii=False), flush=True)
    manifest = {"run_id": args.run_id, "state_root": str(state), "results": results}
    _write_json(state / "manifest.json", manifest)
    print(json.dumps({"manifest": str(state / "manifest.json"), "count": len(results), "errors": sum(bool(x.get("errors")) for x in results)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
