---
name: email-watchdog
description: 多账户邮件智能助理——监控+分类+推送+命令交互+草拟回复+审批发送+链接提取+日历提醒，全流程闭环。
version: 3.0.0
tags: [email, watchdog, notification, wechat, contacts, reply, schedule, commands]
related_skills: [himalaya, agently-mail]
---

# Email Watchdog — 邮件智能助理 v3

全流程闭环：收→分→推→命令→草拟→审批→发送。

## 微信命令处理（Agent 自动加载）

当用户通过微信发送以下命令时，Hermes Agent 必须：
1. 用 `email_commands.parse_command()` 解析消息
2. 调用对应的 handler
3. 返回结果给用户

步骤：
```python
import sys, os; sys.path.insert(0, os.path.expanduser("~/.hermes/scripts"))
import email_commands
result = email_commands.handle(用户消息)
# 将 result 发送回微信
```

**支持的完整命令表：**

| 命令 | 执行函数 | 说明 |
|------|---------|------|
| `全文 #N` | `cmd_全文(N)` | 从 SQLite/cache 取邮件全文返回 |
| `附件 #N` | `cmd_附件(N)` | 列出附件及本地路径 |
| `草拟回复 #N` | 返回提示→LLM起草→`save_draft()` | 两阶段流程 |
| `发送 #N` | `approve_and_send(msg_id)` | 发送已审批草稿 |
| `标记已处理 #N` | `_mark_done(N)` | 归档邮件 |
| `今天重要` | `cmd_今天重要()` | 今天的高优先级邮件 |
| `待处理` | `cmd_待处理()` | 需要回复/有截止的邮件 |
| `日程` | `cmd_日程()` | 查看 LLM 创建的邮件提醒日程 |
| `摘要` | `cmd_摘要()` | 今日分类统计 |
| `帮助` | `cmd_帮助()` | 显示命令列表 |

**草拟回复两阶段流程：**

阶段1：用户发送 `草拟回复 #N`
  1. `get_message_by_number(N)` 取邮件
  2. `get_full_email(msg_id)` 取全文
  3. **检查是否为 Agently Mail 转发**：若 `from_email` 是 `@agent.qq.com` 且正文含原始 `From:` 行，从正文提取原始发件人信息（姓名、邮箱、角色）。根据原始发件人角色选择回复语言和称呼格式（导师→中文"您"，期刊编辑→英文"Dear Editor"，国际同行→英文）
  4. LLM 根据邮件内容和原始发件人信息起草回复
  5. `save_draft(msg_id, draft)` 保存草稿
  6. 推送微信："已草拟回复，确认发送请回复 发送 #N"

阶段2：用户发送 `发送 #N`
  1. `approve_and_send(msg_id)` 发送
  2. 推送微信："✅ 已发送"

## 模块清单（15个）

| 模块 | 功能 |
|------|------|
| email_config.py | 运行时配置加载，默认 `~/.hermes/email_watchdog_config.json` |
| email_watch.py | 主监控：抓取→规则预筛→LLM语义规划→交付 |
| email_store.py | SQLite 存储层 |
| email_trust.py | 动态信任模型 |
| email_risk.py | 风险评估 |
| email_delivery.py | 通知格式、附件下载、日程写入、提醒计划 |
| email_commands.py | 微信命令处理 |
| email_actions.py | 主动执行+草稿+审批+链接 |
| email_reply.py | 回复格式化+发送 |
| email_calendar.py | 日历提取+提醒 |
| email_contacts.py | 通讯录管理 |
| email_batch.py | 批量操作 |
| email_followup.py | 跟进提醒 |
| email_pending_processor.py | 定时发送 |
| email_llm.py | LLM 语义交付规划 |

## 配置

运行时配置文件：`~/.hermes/email_watchdog_config.json`。模板见 `references/email_watchdog_config.template.json`。缺失配置时会回退到旧默认路径和账户，避免现有 cron 失效。

LLM 调用使用 OpenAI-compatible HTTP API，配置项位于 `llm.endpoint`、`llm.api_key_env`、`llm.model` 等字段；脚本只用 Python 标准库 `urllib.request`。

## Cron 任务（生产环境只保留 watchdog）

⚠️ **WeChat 10条限制**: 7 个 cron 同时推送会导致 iLink 限流+静默丢弃。生产环境只启用 email-watchdog，其余按需暂停。

| 任务 | 频率 | 模式 | 用途 | 生产状态 |
|------|------|------|------|---------|
| email-watchdog | 1min | no_agent | 抓取+规则预筛+LLM规划+交付 | ✅ 始终启用 |
| email-pending-sender | 1min | no_agent | 定时发送队列 | ⏸️ 按需 |
| email-calendar-reminder | 1h | no_agent | 日历提醒 | ⏸️ 按需 |
| email-followup-reminder | 9am | no_agent | 未回复+截止预警 | ⏸️ 按需 |
| email-llm-triage | 10min | no_agent | 语义分析回填 | ⏸️ 按需 |
| email-link-processor | 10min | no_agent | 链接提取+快讯合并 | ⏸️ 按需 |
| email-draft-reply | 15min | agent (pro) | 自动草拟回复 | ⏸️ 按需 |

## 关键设计约束

- **规则只做预筛**: 广告/风险自动邮件可跳过，验证码可本地提取，其余由 LLM 决定格式、附件和提醒。
- **交付格式**: `code_extraction`、`summary`、`full_body` 由 `email_llm.analyze_email()` 决定，`email_delivery.py` 负责渲染。
- **WeChat 10条限制**: 详见 `references/wechat-silent-drop.md`
- **睡眠窗口**: `SLEEP_START=0, SLEEP_END=6` 即凌晨静默。禁用用负值 `(-1,-1)`

## 已知陷阱 (详见 references/)

- **himalaya JSON 双转义**: body 中 `\\n` 需 `replace` 为 `\n` 后再做 header 剥离 → `references/himalaya-pitfalls.md`
- **域名仿冒误判**: 检查 impersonation 时必须排除合法 edu.cn/com/org 域名 → `references/pitfalls.md`
- **子串误匹配**: `ems` 匹配 `items` → 使用 `\b` 词边界 → `references/pitfalls.md`
- **Agently 页脚**: 自动附加 `举报退订` → 分类前必须剥离 → `references/pitfalls.md`
- **学校通知按内容**: 不仅看域名，`中期检查/研究生院/教务处` 也应命中 → `references/pitfalls.md`
- **仿冒优先**: phishing 检测必须在 account security 之前 → `references/pitfalls.md`
- **推送格式规范**: 去重账户标签、显示邮箱、全文vs摘要边界、内联日历 → `references/push-format-rules.md`
- **全文审核方法论**: 审核分类必须读全文，不能用 subject 替代 → `references/pitfalls.md`
- **Gmail stderr**: WARN 行污染 stdout → `2>/dev/null` → `references/himalaya-pitfalls.md`
- **Body 清洗顺序**: `\n`转换 → header剥离 → footer剥离 → HTML剥离 → 空白合并 (顺序不可变)
- **Agently Mail 转发掩盖原始发件人**: 通过 Agently Mail 转发的邮件，DB 中 `from_email` 存的是 `augenstern@agent.qq.com`（转发地址），而非原始发件人。这导致回复路由错误、联系人偏好查找失效。必须在邮件正文中提取原始发件人信息 → `references/agently-forwarding-pitfalls.md`
- **Cron 任务与 hermes send 冲突**: 当 cron 任务的交付目标与 `hermes send --to` 目标相同时，`hermes send` 会被跳过（提示使用 final response）。Cron 任务应直接在 final response 中输出通知内容，而非调用 `hermes send`
## 参考文件

| 文件 | 内容 |
|------|------|
| `references/pitfalls.md` | 13个已知陷阱及修复 |
| `references/bug-log.md` | 101封邮件审核记录 |
| `references/cron-setup.md` | Cron 配置命令 |
| `references/himalaya-pitfalls.md` | Himalaya CLI 使用陷阱 |
| `references/agently-forwarding-pitfalls.md` | Agently Mail 转发掩盖原始发件人陷阱 |
| `references/push-format-rules.md` | 推送格式10条规则（用户验证） |
| `references/secretary-draft-workflow.md` | 邮件秘书自动草拟回复工作流 |
| `references/config_template.toml` | Himalaya 配置模板 |
| `references/email_watchdog_config.template.json` | Email Watchdog 运行时配置模板 |

| 文件 | 内容 |
|------|------|
| ~/.hermes/email_watchdog_config.json | 运行时配置 |
| ~/.hermes/email.db | SQLite 主存储，含 messages/attachments/schedules |
| ~/.hermes/email_watch_seen.json | 去重 |
| ~/.hermes/email_cache/{id}.json | 邮件全文缓存 |
| ~/.hermes/email_drafts/{id}.draft | 回复草稿 |
| ~/.hermes/email_contacts.json | 通讯录 |
| ~/.hermes/email_calendar.json | 日历 |
| ~/.hermes/email_pending.json | 定时发送队列 |
