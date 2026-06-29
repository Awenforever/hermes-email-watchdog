---
name: email-watchdog
description: 多账户邮件看门狗——定时检查 USTC/Gmail/Agently 等多邮箱，智能分类（验证码/论文/通知/广告等）并通过微信/Telegram 推送。零 token 空闲，无新邮件时静默。
version: 1.0.0
tags: [email, watchdog, notification, wechat, imap]
related_skills: [himalaya, agently-mail]
---

# Email Watchdog

多账户邮件监控 + 智能分类 + 即时推送。每 N 分钟检查所有配置的邮箱，新邮件自动分类并推送关键信息到微信/Telegram。无新邮件时零 token 消耗。

## 功能

- 同时监控多个邮箱（IMAP + Agently Mail）
- 8 类智能分类：验证码、账户安全、论文状态、付款、学校通知、学术快讯、个人邮件、广告
- 广告/垃圾邮件静默忽略
- 微信推送（通过 Hermes Gateway）
- no-agent 脚本模式，空闲时零 LLM token

## 快速开始

### 第 1 步 — 安装 himalaya CLI

```bash
curl -sSL https://raw.githubusercontent.com/pimalaya/himalaya/master/install.sh | PREFIX=~/.local sh
```

### 第 2 步 — 配置邮箱账户

为每个邮箱创建独立的 himalaya 配置文件。模板见 `references/config_template.toml`。

```bash
mkdir -p ~/.config/himalaya

# USTC 邮箱示例
cp references/config_template.toml ~/.config/himalaya/config_ustc.toml
# 编辑：填入邮箱地址、密码/专用密码、IMAP/SMTP 服务器

# Gmail 示例
cp references/config_template.toml ~/.config/himalaya/config_gmail.toml
# Gmail 需使用应用专用密码: https://myaccount.google.com/apppasswords
```

### 第 3 步 — 配置 Agently Mail（可选）

```bash
npm install -g @tencent-qqmail/agently-cli
npx skills add Tencent/AgentlyMail -g -y
agently-cli auth login
```

### 第 4 步 — 修改账户列表

编辑 `scripts/email_watch.py`，修改 `ACCOUNTS` 列表：

```python
ACCOUNTS = [
    {
        "name": "USTC",
        "type": "himalaya",
        "config": os.path.expanduser("~/.config/himalaya/config_ustc.toml"),
        "email": "you@mail.ustc.edu.cn",
    },
    {
        "name": "Gmail",
        "type": "himalaya",
        "config": os.path.expanduser("~/.config/himalaya/config_gmail.toml"),
        "email": "you@gmail.com",
    },
    # 不需要 Agently 则删除此项
    {
        "name": "Agently",
        "type": "agently",
        "email": "yourname@agent.qq.com",
    },
]
```

### 第 5 步 — 测试

```bash
python3 ~/.hermes/scripts/email_watch.py
```

首次运行会标记所有已有邮件为"已读"，后续只推送新邮件。

### 第 6 步 — 设置定时任务

通过 Hermes cron 或 agent 对话创建：

```
帮我设置一个每5分钟运行 email_watch.py 的 cron 任务，推送到微信
```

## 分类规则

| 类别 | 触发条件 | 优先级 |
|------|---------|--------|
| 🔐 验证码 | verification code、验证码、确认码 | high |
| ⚠️ 账户安全 | 密码修改、异常登录、新设备 | high |
| 📄 论文决定 | accept、reject、revision、decision | high |
| 📬 审稿邀请 | review invitation | high |
| 💰 付款/注册 | registration、invoice、版面费 | high |
| 🏫 学校通知 | @ustc.edu.cn + 通知/公告 | medium |
| 📚 学术快讯 | Google Scholar、arXiv | low |
| 💬 个人邮件 | 不匹配以上规则 | medium |
| 🗑️ 广告 | 含 unsubscribe/促销/折扣 | skip |

分类逻辑在 `scripts/email_watch.py` 的 `classify()` 函数中，可按需修改。

## 跨设备部署

另一台 Hermes：
1. 安装本 skill：`npx skills add <repo-url> -g -y`
2. 安装 himalaya CLI
3. 复制 himalaya 配置文件
4. 修改 `ACCOUNTS` 列表
5. 设置 cron 任务

## 依赖

- `himalaya` CLI（IMAP 邮箱）
- `agently-cli`（可选，Agently Mail）
- Python 3.8+

## 文件结构

```
email-watchdog/
├── SKILL.md
├── scripts/
│   └── email_watch.py    # 主检查脚本
└── references/
    └── config_template.toml  # himalaya 配置模板
```