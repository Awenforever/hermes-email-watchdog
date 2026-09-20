# Hermes Email Watchdog

Hermes 的只读多账号邮件监控插件。它读取新邮件，完成拆分、归类、证据化摘要和风险判断，只把真正需要处理的事项通知到 Hermes 消息通道。

## 功能

- 通过 Himalaya 读取一个或多个 IMAP 邮箱。
- 区分正文、引用、签名、列表退订信息和常见模板噪声。
- 输出带事实依据的类别、重要性、期限、风险和建议动作。
- 默认复用 Hermes 中名为 `USTC` 的 OpenAI 兼容提供方，并使用
  `qwen3.6-chat`；自定义提供方和模型配置会在升级时保留。
- 默认只通知紧急、高风险、有截止时间或明确需要行动的邮件。
- 普通通知、营销邮件和新闻简报仍会被记录和学习，但保持静默。
- 通知使用持久 outbox；失败重试不会阻塞下一轮收信。
- 配置变更采用锁、原子写入和失败回滚。

本插件不发送邮件，也不改变已读、标记、移动或删除状态。微信等消息传输由 Hermes 及对应通道插件负责。

## 要求

- Hermes `>=0.21.3,<0.22`
- Python 3.11+
- [Himalaya](https://github.com/pimalaya/himalaya) 可执行文件
- 邮箱密码通过系统密钥环、密码管理器或外部命令提供；不要写入插件配置

## 安装

```bash
hermes plugins install Awenforever/hermes-email-watchdog
hermes plugins enable hermes-email-watchdog
hermes email-watchdog install-runtime
```

让 Hermes 引导完成邮箱、Himalaya 配置和微信通知目标后，可先预览再原子应用：

```bash
hermes email-watchdog onboarding-plan --input-json @setup.json
hermes email-watchdog onboarding-apply --input-json @setup.json
hermes email-watchdog doctor
```

引导只询问尚未解析的账户信息，并要求通过系统密钥环、密码管理器或环境变量命令读取凭据；不会要求在聊天或配置中填写明文密码。应用阶段只执行一封信的只读列表验证，失败会恢复原配置并保持禁用。

配置邮箱后，先做一次只读试运行：

```bash
hermes email-watchdog run-once
hermes email-watchdog status
```

确认分类和通知目标无误后再启用定时监控：

```bash
hermes email-watchdog enable
```

## 数据与边界

配置、seen 索引、缓存、学习数据库、状态和 outbox 位于当前 Hermes profile 的 `plugin-data/hermes-email-watchdog/`。安装和升级不会覆盖已有数据；从旧版升级时，仅在新位置没有对应文件的情况下迁移旧数据。

邮件正文可能包含敏感信息。请限制 profile 目录权限、加密备份，并为缓存设置符合个人需求的保留周期。插件日志和通知不应输出邮箱密码、访问令牌或完整认证命令。

## 生产建议

- 首次接入使用专用测试邮件验证匹配、拆分、时区和期限。
- 保持“仅行动项通知”默认策略，按实际误报逐步调整。
- 定期检查失败 outbox、解析降级率和被静默的高风险样本。
- 升级前备份 plugin-data；升级后先执行 `run-once` 再恢复计划任务。

## License

[MIT](LICENSE)
