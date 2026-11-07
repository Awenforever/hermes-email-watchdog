# 连接、登录与限流

这页解释看门狗为什么这样连邮箱，以及出问题时该调什么。默认值对绝大多数部署是安全的，**不改也能正常工作**。

## 一、每个账户只有一条连接

对 IMAP 账户，看门狗保持**一条长连接**，同时承担三件事：

1. 每 5 秒一次 `NOOP` + 计数，发现新邮件立刻叫醒扫描；
2. 列出最近的邮件信封；
3. 按需读取正文与附件（用 `BODY.PEEK`，**不会把邮件标记为已读**）。

历史上每轮扫描每账户要 7 次独立登录（列信 1 次 + 每封正文 1 次），这正是 Gmail 报 `Too many simultaneous connections` 的原因。单连接把这个数字降到“每次重连 1 次”。

## 二、账户级开关

```json
{
  "accounts": [
    { "label": "Gmail", "himalaya_config": "...", "idle_watch": true, "direct_imap": true },
    { "label": "163",  "himalaya_config": "...", "direct_imap": false, "min_interval_seconds": 120 }
  ]
}
```

| 键 | 默认 | 作用 |
|---|---|---|
| `direct_imap` | `true` | 走内置长连接；设为 `false` 则用 himalaya CLI（每条命令一次登录） |
| `idle_watch` | `true` | 在同一连接上做 5 秒一次的 NOOP 检测，发现新信立即唤醒扫描 |
| `idle_interval` | `5` | 上面那次检测的间隔（秒） |
| `min_interval_seconds` | `0` | 该账户最小的扫描间隔；用于“登录贵但时效要求低”的邮箱 |

**什么时候该关 `direct_imap`**：某些服务端（实测 163）会在代码路径下拒绝 `SELECT`，此时回退到 himalaya 更稳；配合 `min_interval_seconds` 控制登录频率。

## 三、全局节流参数

都放在 `watchdog` 段，不写就用默认值：

| 键 | 默认 | 作用 |
|---|---|---|
| `backfill_page_size` | `20` | 每页信封数 |
| `backfill_max_pages` | `4` | 最多往回翻几页；扫描会一直翻到遇见已入库的邮件为止，因此突发不会漏信 |
| `scan_batch_limit` | `6` | 单轮最多取几封新邮件，避免一次开太多连接 |
| `throttle_backoff_seconds` | `180` | 被服务端限流后的冷却时间 |
| `login_budget_per_hour` | `60` | 每账户每小时登录上限（兜底） |

## 四、登录预算落盘

预算存在状态目录下的 `login_budget.json`，记录最近一小时每个账户的登录时间戳：

```json
{"logins": {"gmail": [1794069410.3], "163": [...]}}
```

- 跨进程重启保留，重启不会白送一份新额度；
- 超过 1 小时的条目在读写时自动清理；
- 额度用尽时**跳过该账户本轮扫描**并记一条日志，而不是继续敲服务端。

## 五、怎么判断某个账户是否真的在工作

每轮扫描会对每个账户输出一行：

```
Hermes Email Watchdog: Gmail path=session budget=2
Hermes Email Watchdog: 163   path=himalaya budget=32
```

- `path=session` 表示走了长连接，`path=himalaya` 表示走 CLI；
- `budget` 是该账户最近一小时的登录次数；
- 如果某个账户**长期没有这行日志**，说明它在一开始就失败了（历史上正是这样漏掉了整个邮箱的邮件）。

## 六、已知边界

- 长连接会被上游代理回收（实测偶发），客户端会自动重连，每次重连计 1 次登录；
- 163 的服务端在长连接路径下不稳定，默认走 himalaya；
- 服务端的限流措辞若变化，冷却判定可能失效，此时由登录预算兜底。
