---
name: hermes-email-watchdog
description: 安装、个性化配置并运行只读多账号邮件助理；智能判断邮件价值并通过微信发送可行动提醒、链接、附件和期限通知。
version: 0.7.0
tags: [email, watchdog, notification, read-only, hermes, onboarding]
---

# Hermes Email Watchdog

Email Watchdog 是独立的只读邮箱助理。它读取邮件并把值得打扰用户的内容交给已配置的微信会话；它不发送邮件，也不依赖 Weekly Briefing。

## 安装与配置对话

当用户要求安装、配置、迁移或启用 Email Watchdog 时，必须主动引导：

1. 运行 `hermes email-watchdog setup`，读取已检测账户、当前状态和 `unresolved`。
2. 若 `unresolved` 包含 `himalaya`，先识别 Windows、WSL、Linux 或 NAS 容器环境，说明将安装只读邮箱客户端 Himalaya，获得同意后通过该平台可信的软件包管理器安装并验证 `himalaya --version`。不要使用 `curl | sh`，也不要在未获同意时修改系统环境。
3. 使用当前 Hermes 会话作为通知目标。底层会读取 `HERMES_SESSION_PLATFORM` 与 `HERMES_SESSION_CHAT_ID`；Hook 也会保存待确认目标。
4. 若只检测到一个有效 Himalaya 配置，自动复用；不要询问已经可靠检测到的账户或路径。
5. 只逐项询问缺失的用户级信息：
   - 要监控哪些邮箱；
   - 仅行动项还是更广泛通知；
   - 附件自动下载/微信转发偏好和大小上限；
   - 时区与提醒提前量；
   - 模型偏好（无偏好时保留默认）。
6. 新账户必须使用外部凭据命令，例如 `pass`、`secret-tool`、`security`、`op`、`bw`、`gopass` 或受保护的环境变量。绝不询问、接收或复述邮箱密码、应用专用密码、令牌和密钥；禁止用带明文的 `echo`/`printf`。
7. 由 Agent 在后台构造设置数据，并依次调用：

   ```bash
   hermes email-watchdog onboarding-plan --input-json '<JSON>'
   hermes email-watchdog onboarding-apply --input-json '<JSON>'
   ```

   用户不应被要求创建或编辑 JSON。`plan` 用来说明将发生的变更；`apply` 原子写入，并以一封信列表进行只读验证，失败自动回滚。
8. 普通“安装”默认保持禁用；明确的“安装并启用”或“启用邮件监控”才构成启用授权。
9. 应用后运行 `doctor` 与 `run-once`。只报告脱敏结果，不暴露完整邮箱、聊天 ID、凭据命令或内部路径。
10. 只有只读验证和试运行通过后才运行 `enable`。恢复或升级时跳过已经验证的步骤，不重复要求登录。

自然对话与无人值守部署必须使用同一 `plan`/`apply` 内核，并产生相同的标准配置哈希。

## 强制保证

- 邮箱访问只读；验证只能使用 `envelope list --page-size 1`。
- 不暴露邮件回复、发送、归档、删除、移动、标记或已读操作。
- 缺失或无效配置时调度器保持禁用。
- 安装不修改 Hermes 核心微信适配器。
- 默认卸载保留配置与引导状态；purge 必须显式确认并只删除插件自有数据。
- 微信传输与 `hermes-wechat-enhance` 是独立组件。

## 数据归属

- Skill 源码：当前插件目录；
- 活动 Hook：当前 Hermes profile 的 `hooks/hermes-email-watchdog/`；
- 用户数据：当前 Hermes profile 的 `plugin-data/hermes-email-watchdog/`。

升级不得覆盖邮箱认证、通知目标、个性化策略、已处理索引、附件、日程或 outbox。
