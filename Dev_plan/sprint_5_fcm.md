# Sprint 5 Plan-Mode Prompt：FastAPI 安全网关、实时双向 WebSocket 通道与 Firebase (FCM) 6 大类预警服务

> **使用方式**：在 Sprint 4 验收通过后，在 Antigravity + 4 Argon 的 **Plan Mode** 中输入本 Prompt。

---

## 【角色激活与任务目标】
你现在由 **项目总架构师**、**交易执行工程师** 与 **QA/独立审计工程师** 联合驱动。请在 **Plan Mode** 下完成 `Crypto-bot` 的 **Sprint 5：FastAPI 异步 REST + WebSocket 网关、启动期孤儿单对账恢复、设备激活与币安 API 密钥动态配置接口、后台心跳自检看门狗协程，以及覆盖 6 大必推场景的 Google Firebase (FCM) 弹窗预警服务**。

---

## 【待生成文件清单与接口协议规范】

### 1. `crypto-bot-server/app/core/fcm_notifier.py`
实现高可靠 Google Firebase Cloud Messaging (FCM HTTP v1 API / `firebase-admin`) 推送通知器（`FCMNotifier`），并同时通过 WebSocket 实时通道双路下发（确保 App 在前台或后台均 100% 弹窗告警）：
- **严格实现以下 6 大类必推弹窗预警场景**：
  1. **`EVENT_LIVE_BUY_FILLED`（实盘建仓成功通知）**：包含买入均价、净持仓数量 `qty_net`、投入 `USDT` 金额、综合信号得分 $S_{\text{total}}$、$\text{RVOL}$ 倍数，以及当日是否同步补买 BNB 的状态。
  2. **`EVENT_LIVE_SELL_CLOSED`（止盈/止损/超时平仓通知）**：包含触发类型（`-0.8% 止损` / `+1.0% 保底止盈` / `S2 呼吸式移动止盈` / `120h 超时平仓`）、卖出均价、本单扣除手续费后的净盈亏金额（`USDT`）与毛/净收益率（`%`）。
  3. **`EVENT_FSM_STATE_UPGRADE`（持仓状态跃迁喜报）**：当实盘持仓涨幅突破 $+1.0\%$ 锁定 `S1_LOCKED` 保底止盈，或突破 $+1.5\%$ 激活 `S2_TRAILING` 移动止盈（及进入 `S2_B` 破量主升浪、`S2_C` 顶部收割态）时实时弹窗提醒。
  4. **`EVENT_MANUAL_ORDER_DETECTED`（手动订单检测托管提醒）**：当检测到用户在币安手动买入 `SOLUSDT` 时，立即推送高优先级通知：“检测到新的手动买单（均价 $X$，数量 $Y$），点击进入 App 一键开启自动止盈止损托管”。
  5. **`EVENT_RISK_CIRCUIT_BREAKER`（系统级风控与复盘报告提醒）**：包括宏观极端瀑布熔断触发、恐慌衰竭极值抄底豁免触发、币安 API 密钥权限异常、`USDT` 余额不足，以及 `1W/1M/3M/6M/1Y` 复盘报告生成完毕通知。
  6. **`EVENT_SERVER_SCANNER_FAULT`（第 6 类：服务器故障与后台信号扫描异常弹窗预警）**：
     - 内置独立的 **`WatchdogHeartbeat`（看门狗协程）**，每 $15\text{ s}$ 检查一次：
       - ① 行情 WebSocket 流最后收到 Tick 的时间差是否 $> 15\text{ s}$（防止连接未断开但无数据推送的假死状态）；
       - ② 策略扫描协程事件循环延迟是否 $> 5\text{ s}$；
       - ③ 系统物理内存 + Swap 使用率是否 $> 85\%$ 或磁盘剩余空间是否 $< 2\text{ GB}$；
       - ④ 服务器发生异常重启后的恢复事件。
     - 一旦任一指标异常，立即发送最高优先级（Android `PRIORITY_MAX` + 独立报警通知渠道 `cryptobot_critical_fault`）FCM 弹窗预警，并在后台触发自动重连自愈（带 $300\text{ s}$ 同类告警冷却防刷屏机制）。

### 2. `crypto-bot-server/app/main.py`
整合所有模块生命周期（`lifespan`），构建完整的 FastAPI 异步网关服务：
- **生命周期管理（含无人值守恢复与孤儿单对账）**：
  - 启动时：初始化 SQLite WAL 单写队列数据库 $\to$ 检查/加载 `/etc/cryptobot/.master_key` 自动解密恢复币安 API 密钥 $\to$ **调用 `binance_spot_executor.reconcile_open_orders_on_startup()` 对账并清理遗留挂单** $\to$ 从 `positions` 表恢复所有未平仓的实盘与影子状态机 $\to$ 启动币安 WebSocket 行情流与 `UserDataStream` $\to$ 启动每日 K 线归档与五维复盘定时调度器 $\to$ 启动看门狗健康监控协程。
- **REST API 路由清单（除 `/api/v1/auth/activate` 外，全部强制校验设备绑定 JWT）**：
  - `POST /api/v1/auth/activate`：校验一次性设备激活口令（`activation_token`）并永久绑定 Android 设备硬件 ID，返回长效 JWT。
  - `POST /api/v1/config/binance_keys`：接收 App 端输入的币安 `api_key` 与 `api_secret`，先调用币安接口校验其具备现货交易权限（且若检测到开启了提现权限则给出安全告警），随后经 AES-256-GCM 加密存入数据库并热加载至实盘执行器。
  - `POST /api/v1/config/toggle_live`：开启或关闭 `Crypto-bot` 实盘授权总开关（关闭时执行“优雅停机：停新单、保老单风控”）。
  - `POST /api/v1/config/update_params`：支持用户在 App 端自定义修改交易总预算（默认 $2000\text{ USDT}$）、最大持仓数（$2$）、单笔范围（$300 \sim 700\text{ USDT}$）、止损率（$-0.8\%$）、保底止盈率（$+1.0\%$）、移动止盈激活线（$+1.5\%$）与回撤幅度（$-0.5\%$）。
  - `POST /api/v1/manual_order/toggle_managed`：开启或关闭手动订单托管（支持处理已跌破 $-0.8\%$ 时的 `IMMEDIATE_STOP_LOSS` 或 `RESET_BENCHMARK_TO_CURRENT` 二次确认动作）。
  - `GET /api/v1/dashboard/overview`：一次性返回 Android 四大板块所需的聚合快照数据（实时行情与卡尔曼曲线、策略达成度仪表板 $0\% \sim 100\%$ 明细、预估动态仓位金额、实盘资金与持仓卡片、影子资金与持仓卡片）。
  - `GET /api/v1/reviews/list` 与 `GET /api/v1/reviews/detail/{report_id}`：获取 $1\text{W}/1\text{M}/3\text{M}/6\text{M}/1\text{Y}$ 复盘报告列表、Markdown 正文与推荐参数对比表。
  - `POST /api/v1/reviews/apply_recommended/{report_id}`：用户在 App 端审阅复盘报告后，一键将推荐参数应用至策略引擎。
  - `GET /api/v1/shadow/archive_sync`：供本地电脑脚本增量下载 `./shadow_data/` 归档文件。
- **WebSocket 实时流（`WSS /ws/client`）**：
  - 每 $500\text{ ms}$ 向已连接的 Android 客户端推送实时价格、策略达成度仪表板百分比 $S_{\text{total}}$、实盘/影子持仓实时浮盈率及状态机阶段（`S0` / `S1` / `S2_A` / `S2_B` / `S2_C`），并支持客户端双向 `PING/PONG` 帧保活；发生交易或告警事件时毫秒级推送弹窗指令。

---

## 【Plan Mode 执行与 QA 验收要求】
1. 先输出 **Sprint 5 实现计划（Plan）**。
2. 编写完整代码后，创建集成测试 `tests/test_sprint5_api_and_fcm.py`（使用 `httpx.AsyncClient` 测试全部 REST 接口、JWT 鉴权拦截、启动期孤儿挂单自动对账恢复、手动单托管二次确认流程、复盘参数一键应用，以及模拟 WebSocket 假死超过 $15\text{ s}$ 触发第 6 类服务器扫描异常 FCM 预警与自动重连）。