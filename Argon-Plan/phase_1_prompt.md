# Role & Context
你是由 **2. 加密货币市场金融分析师**、**7. Python 后端工程师** 与 **9. 数据库工程师** 组成的联合研发引擎。
请基于 **Phase 0** 的环境基线，为 `SOL-bot` 生成 **Phase 1：Binance 免费数据源接入、三级错峰防崩溃扫描引擎与 SQLite WAL 数据库底座** 的完整 Python 代码。

---

## 1. 核心架构与防崩溃约束
* **交易标的**：仅限 Binance 官方现货 `SOLUSDT`（及手续费抵扣币种 `BNBUSDT`），全年 $24\text{ h}$ 运行，不使用任何杠杆。
* **内存定长约束**：在 $2\text{ GB RAM}$ 服务器上，所有内存 K 线序列必须使用 `collections.deque(maxlen=500)` 定长环形缓冲区（RingBuffer），严禁无限追加导致内存泄漏。
* **多周期框架**：采用 **`1H` 定趋势方向 + `15m` 看波段结构 + `5m / 1m` 触发 `RSI(6)` 与 `RVOL` 精准入场**，同时维护 **盘口实时价 + $3\text{ s}$ 防插针平滑过滤窗口**。

---

## 2. 待生成交付物清单（请在项目中创建以下文件）

### 2.1 `backend/database/schema.py` & `backend/database/db_manager.py`
使用 `aiosqlite` 构建异步 SQLite 3 数据库管理器，初始化时必须执行：
`PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA cache_size=-64000; PRAGMA foreign_keys=ON;`

必须创建以下核心表结构：
1. **`positions`（核心持仓与生命周期表，实盘与影子同构）**：
   * `position_id` (TEXT PRIMARY KEY)
   * `account_mode` (TEXT: `'LIVE'` 实盘 | `'SHADOW'` 虚拟盘)
   * `origin_type` (TEXT: `'BOT_AUTO'` 机器人自主交易 | `'USER_MANUAL'` 用户非机器人手动订单)
   * `is_managed` (INTEGER: 机器人单默认为 $1$；用户手动单在 App 打开“托管按钮”后置为 $1$)
   * `count_in_bot_stats` (INTEGER: `'BOT_AUTO'` 强制为 $1$；`'USER_MANUAL'` 强制为 $0$，绝不计入机器人战绩)
   * `symbol` (TEXT DEFAULT `'SOLUSDT'`)
   * `entry_price` (REAL), `quantity` (REAL), `notional_usdt` (REAL), `confidence_score` (REAL)
   * `highest_price` (REAL: 持仓期间最高价), `trailing_active` (INTEGER: 浮盈 $\ge +1.5\%$ 时置为 $1$)
   * `rvol_exempt_active` (INTEGER: 是否处于大绿柱 $\text{RVOL} > 2.5$ 止盈回撤放宽保护期)
   * `status` (TEXT: `'OPEN'`, `'CLOSING'`, `'CLOSED_TP'`, `'CLOSED_SL'`)
   * `close_reason` (TEXT), `close_price` (REAL), `realized_pnl_usdt` (REAL), `realized_pnl_pct` (REAL)
   * `created_at` (INTEGER), `closed_at` (INTEGER)
2. **`sl_rebound_watcher`（$-0.8\%$ 止损后二次触底反弹监控表）**：
   * `watcher_id` (TEXT PRIMARY KEY), `account_mode` (TEXT: `'LIVE'` | `'SHADOW'`)
   * `sl_fill_price` (REAL: 止损实际成交价 $P_{\text{sl}}$)
   * `target_drop_price` (REAL: $P_{\text{sl}} \times (1 - 0.009)$，即止损后再跌至少 $0.9\%$ 的激活线)
   * `lowest_price_seen` (REAL), `is_armed` (INTEGER: 当最低价触及 `target_drop_price` 时置为 $1$)
   * `status` (TEXT: `'WATCHING'`, `'TRIGGERED'`, `'EXPIRED'`), `expires_at` (INTEGER)
3. **`trading_config`（交易参数配置表）**：
   * `config_id` (INTEGER PRIMARY KEY CHECK (`config_id = 1`))
   * `live_switch_enabled` (INTEGER DEFAULT $0$: App 端 Crypto-bot 实盘授权总开关)
   * `custom_config_enabled` (INTEGER DEFAULT $0$: 用户自定义参数开关，默认关闭)
   * `total_capital_usdt` (REAL DEFAULT $2000.0$: 交易总金额，实盘与影子共用基准)
   * `max_concurrent_positions` (INTEGER DEFAULT $2$: 最多同时持仓数)
   * `max_single_order_usdt` (REAL DEFAULT $700.0$: 单笔上限 $W_{\max}$，开启自定义后按 $[0.45 W_{\max}, W_{\max}]$ 动态缩放)
   * `tp_min_pct` (REAL DEFAULT $0.010$: 最低止盈点 $+1.0\%$)
   * `tp_trail_activate_pct` (REAL DEFAULT $0.015$: 移动止盈激活点 $+1.5\%$)
   * `tp_trail_drawdown_pct` (REAL DEFAULT $0.005$: 允许回撤 $-0.5\%$)
   * `sl_pct` (REAL DEFAULT $0.008$: 止损阈值 $-0.8\%$)
4. **`shadow_account`（影子机器人独立 2000 USDT 虚拟账户表）**：
   * 记录 `available_usdt`（初始 $2000.0$）、`locked_usdt`、`virtual_bnb_usdt`、`total_trades`、`win_trades`、`cumulative_pnl_usdt`。

### 2.2 `backend/market/binance_ws_scanner.py`
编写基于 `aiohttp` / `websockets` 的高可用 Binance 官方免费流接入与 **三级错峰扫描调度器**：
1. **单连接组合流订阅**：
   `wss://stream.binance.com:9443/stream?streams=solusdt@bookTicker/solusdt@kline_1m/solusdt@kline_5m/solusdt@kline_15m/solusdt@kline_1h/bnbusdt@bookTicker`
2. **启动冷启动回填（Warm-up）**：服务启动时通过 REST API (`/api/v3/klines`) 一次性拉取 `1h`, `15m`, `5m`, `1m` 最近 $200$ 根历史 K 线填充 RingBuffer，避免指标冷启动盲区。
3. **三级错峰计算流水线（防止 CPU 峰值导致服务器死机）**：
   * **L0 盘口毫秒级流（$250\text{ ms}$ 节流采样）**：
     更新最优买卖盘口 $(P_b, Q_b, P_a, Q_a)$，并维护一个容量为 $12$（对应最近 $3\text{ s}$）的时间加权中间价滑动窗口，实时输出 **$3\text{ s}$ 防插针平滑价格 $\bar{P}_{3s}(t)$**：
     $$\bar{P}_{3s}(t) = \frac{\sum_{i=1}^{n} w_i \cdot P_{\text{mid}}(t_i)}{\sum_{i=1}^{n} w_i}, \quad P_{\text{mid}} = \frac{P_b + P_a}{2}$$
     当存在活跃持仓或处于止损反弹观察期时，每 $250\text{ ms}$ 触发一次风控止盈/止损/反弹检查。
   * **L1 短周期信号流（每 $5\text{ s}$ 或 `1m` K 线闭合）**：
     增量计算 `1m` 与 `5m` 的 $\text{RSI}(6)$ 及时间归一化 $\text{RVOL}_{5m}$，推送给策略达成度仪表盘。
   * **L2 结构趋势流（仅在 `5m` / `15m` / `1h` K 线闭合瞬间执行）**：
     计算 `1H` EMA/Kalman 趋势与 `15m` 布林带/ATR 空间并缓存至内存字典，L1 级直接零开销读取该缓存。
4. **断线指数退避重连与心跳看门狗**：若超过 $10\text{ s}$ 未收到任何 WebSocket 消息，主动断开并在 $1\text{ s}, 2\text{ s}, 4\text{ s}, 8\text{ s}$ 退避重连，重连后自动补齐缺失 K 线。

---

## 3. 代码质量与验收标准
* 提供完整可运行的异步 Python 代码，所有 SQL 操作必须使用参数化查询并做好事务封装。
* 编写独立的自检入口 `if __name__ == "__main__":`，可连接 Binance 免费接口打印实时 $\bar{P}_{3s}(t)$ 与四周期 K 线状态。