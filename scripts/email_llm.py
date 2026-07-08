#!/usr/bin/env python3
"""
Email LLM Triage — agent cron job that uses deepseek-v4-flash for semantic understanding.
Processes pending messages from SQLite store, extracts structured data, and pushes results.
Only runs when there are messages that need LLM processing.
"""

import json, os, sys, subprocess
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

try:
    import email_store
    import email_trust
except ImportError:
    print("ERROR: email_store/email_trust not available")
    sys.exit(1)

MAX_BATCH = 5  # max emails per LLM call


def load_cached_body(msg_id: str) -> str:
    """Load full email body from cache."""
    cache_file = os.path.expanduser(f"~/.hermes/email_cache/{msg_id}.json")
    if os.path.exists(cache_file):
        with open(cache_file) as f:
            return json.load(f).get("body", "")
    return ""


def should_use_llm(msg: dict) -> bool:
    """Check if this message needs LLM processing."""
    rule_cat = msg.get("rule_category") or msg.get("final_category", "")
    
    # Skip: ads, auto-mail, verification codes, newsletters
    skip_categories = {"广告", "自动通知", "验证码", "订阅推送"}
    if rule_cat in skip_categories:
        return False
    
    # Skip: pure scholar alerts (too many, low value)
    if rule_cat in ("学术快讯", "学术周报"):
        return False
    
    # Process: personal emails, notifications, paper decisions, meetings, forms
    need_categories = {"个人邮件", "学校通知", "论文决定", "📬 审稿邀请",
                       "会议/活动", "表格/问卷", "💰 付款/缴费", "📋 论文决定"}
    if rule_cat in need_categories:
        return True
    
    # Default: process if has attachments, links, or from known contact
    if msg.get("has_attachment") or msg.get("has_links"):
        return True
    
    trust_label = msg.get("trust_label", "unknown")
    if trust_label in ("trusted", "known"):
        return True
    
    return False


def build_llm_prompt(messages: list) -> str:
    """Build the prompt for the LLM to classify and summarize emails."""
    emails_json = []
    for msg in messages:
        body = load_cached_body(msg["id"]) or msg.get("summary_short", "")
        body = body[:1500]  # truncate for cost
        
        emails_json.append({
            "id": msg["id"],
            "from_name": msg.get("from_name", ""),
            "from_email": msg.get("from_email", ""),
            "subject": msg.get("subject", ""),
            "body_snippet": body[:800],
            "rule_category": msg.get("rule_category", ""),
            "has_attachment": bool(msg.get("has_attachment")),
        })
    
    prompt = f"""你是一个邮件秘书。请分析以下邮件，返回JSON数组。

对每封邮件提取：
- category: 个人邮件/学校通知/会议邀请/论文决定/审稿邀请/付款缴费/表格问卷/其他
- importance: low/medium/high/urgent
- needs_reply: true/false (是否需要回复)
- has_deadline: true/false
- deadline: 截止日期(ISO格式)或null
- summary_short: 一句话摘要(30字以内)
- summary_long: 详细摘要(100字以内)  
- user_action: 用户需要做什么(如"阅读附件并回复"/"填写表格"/"确认参加")或null
- action_type: draft_reply/download_attachment/visit_link/fill_form/none

邮件列表:
{json.dumps(emails_json, ensure_ascii=False, indent=2)}

仅返回JSON数组，不要其他内容。"""

    return prompt


def call_llm(prompt: str, model: str = "deepseek-v4-flash-ascend") -> list:
    """Call the LLM via Hermes terminal and parse JSON response."""
    # Write prompt to temp file to avoid shell escaping issues
    prompt_file = "/tmp/email_llm_prompt.json"
    with open(prompt_file, "w") as f:
        f.write(prompt)
    
    # Use Hermes CLI or direct API call
    # For now, use a subprocess that calls the model
    cmd = f"""python3 -c "
import json, urllib.request

with open('{prompt_file}') as f:
    prompt = f.read()

# Use USTC API
data = json.dumps({{
    'model': '{model}',
    'messages': [{{'role': 'user', 'content': prompt}}],
    'temperature': 0.3,
    'max_tokens': 2000,
    'response_format': {{'type': 'json_object'}}
}}).encode()

req = urllib.request.Request(
    'https://api.llm.ustc.edu.cn/v1/chat/completions',
    data=data,
    headers={{
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + open('/home/augenstern/.hermes/auth.json').read().strip()
    }}
)

try:
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read())
        content = result['choices'][0]['message']['content']
        # Try to extract JSON array
        content = content.strip()
        if content.startswith('```'):
            content = content.split('```')[1]
            if content.startswith('json'):
                content = content[4:]
        print(content)
except Exception as e:
    print(json.dumps([{{'error': str(e)}}]))
"
"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=90)
        output = result.stdout.strip()
        if output:
            return json.loads(output)
    except Exception as e:
        print(f"LLM call failed: {e}", file=sys.stderr)
    
    return []


def process_pending():
    """Main triage function. Called by cron."""
    # Get messages that need LLM processing
    conn = email_store._get_conn()
    rows = conn.execute(
        """SELECT * FROM messages 
           WHERE push_status='pushed' 
           AND (llm_category IS NULL OR llm_category = '')
           ORDER BY date_sent DESC LIMIT ?""",
        (MAX_BATCH * 3,)
    ).fetchall()
    
    messages = [dict(r) for r in rows]
    
    # Filter: only process those that need LLM
    to_process = [m for m in messages if should_use_llm(m)]
    
    if not to_process:
        return ""
    
    # Process in batches
    results_summary = []
    
    for i in range(0, len(to_process), MAX_BATCH):
        batch = to_process[i:i+MAX_BATCH]
        prompt = build_llm_prompt(batch)
        
        try:
            llm_results = call_llm(prompt)
        except Exception as e:
            llm_results = [{"error": str(e), "id": m["id"]} for m in batch]
        
        # Update store with LLM results
        for j, result in enumerate(llm_results):
            if j < len(batch):
                msg = batch[j]
                msg_id = msg["id"]
                
                update_data = {
                    "llm_category": result.get("category", ""),
                    "final_category": result.get("category") or msg.get("rule_category", ""),
                    "importance": result.get("importance", "medium"),
                    "needs_reply": 1 if result.get("needs_reply") else 0,
                    "has_deadline": 1 if result.get("has_deadline") else 0,
                    "summary_short": result.get("summary_short", ""),
                    "summary_long": result.get("summary_long", ""),
                    "action_summary": result.get("user_action", ""),
                    "updated_at": datetime.now().isoformat(),
                }
                
                email_store.upsert_message({"id": msg_id, **update_data})
                
                # Create action if needed
                action_type = result.get("action_type", "none")
                if action_type != "none":
                    email_store.create_action({
                        "message_id": msg_id,
                        "action_type": action_type,
                        "action_status": "pending",
                        "requires_approval": action_type in ("draft_reply", "fill_form"),
                        "risk_level": "low",
                        "approval_prompt": result.get("user_action", ""),
                    })
                
                # Summary for push
                if result.get("importance") in ("high", "urgent") or result.get("needs_reply"):
                    results_summary.append(
                        f"[{msg.get('account','?')}] 🔴 {result.get('importance','?')} {result.get('category','?')}\n"
                        f"  {result.get('summary_short','?')}\n"
                        f"  📋 {result.get('user_action','')}"
                    )
    
    if results_summary:
        return "🤖 LLM分析完成:\n\n" + "\n\n".join(results_summary)
    
    return ""


if __name__ == "__main__":
    output = process_pending()
    if output:
        print(output)