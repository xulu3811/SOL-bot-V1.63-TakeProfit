# Sprint 3 Plan-Mode Prompt：实盘/影子双轨执行引擎、呼吸式移动止盈状态机、净持仓核算与手动单一键托管

> **使用方式**：在 Sprint 2 验收通过后，在 Antigravity + 4 Argon 的 **Plan Mode** 中输入本 Prompt。

---

## 【角色激活与任务目标】
你现在由 **交易执行工程师**、**量化策略专家** 与 **QA/独立审计工程师** 联合驱动。请在 **Plan Mode** 下完成 `Crypto-bot` 的 **Sprint 3：实盘与影子同源双轨广播器、集成【补丁 4】的呼吸式自适应移动止盈状态机、币安现货 REST/UserDataStream 订单执行器（含 SOL 扣费净持仓精确核算、`-2011` 撤单竞态对账、每日首单自适应 BNB 补给、手动订单识别与一键托管）以及 `Crypto-shadow` 五档盘口高保真仿真撮合器**。

---

## 【待生成文件清单与执行逻辑规范】

### 1. `crypto-bot-server/app/execution/position_fsm.py`
实现实盘（`LIVE`）与影子（`SHADOW`）完全共用的毫秒级持仓有限状态机（`PositionStateMachine`）：
- **实时收益率计算**：以盘口实时最优买一价 $P_{\text{bid}}(t)$ 与持仓入场均价 $P_{\text{entry}}$ 计算实时毛收益率 $r(t) = \frac{P_{\text{bid}}(t) - P_{\text{entry}}}{P_{\text{entry}}}$，并维护持仓期间最高买一价 $P_{\text{peak}}(t)$、最高毛收益率 $r_{\text{peak}}(t)$ 以及最近一次创新高时间戳 $t_{\text{new\_high}}$。
- **120 小时（5 天）超时强制平仓**：若 $t \ge \text{pos.timeout\_at}$（即 $\text{opened\_at} + 432000\text{ s}$），立即返回平仓指令 `TRIGGER_SELL_TIMEOUT`。
- **三阶段状态跃迁与【补丁 4：呼吸式自适应移动止盈】**：
  1. **`S0_PROTECT`（初始风控态，$r_{\text{peak}} < +1.0\%$）**：
     - 若 $r(t) \le -0.008$（$-0.8\%$），立即返回 `TRIGGER_STOP_LOSS`，并在状态机回调中将当前平仓价与时间戳写入 `system_config` 的 `(last_sl_price, last_sl_time)`（激活补丁 2 重入锁）。
     - 若 $r_{\text{peak}} \ge +0.010$，状态跃迁至 `S1_LOCKED`（若同时 $r_{\text{peak}} \ge +0.015$ 则直接跃迁至 `S2_TRAILING`），并异步持久化至 SQLite。
  2. **`S1_LOCKED`（保底锁定态，$+1.0\% \le r_{\text{peak}} < +1.5\%$）**：
     - 保底止盈触发线死锁在 $r_{\text{trigger}} = +0.010$（$+1.0\%$）。一旦 $r(t) \le +0.010$，立即返回 `TRIGGER_TP1_FLOOR`。
     - 若 $r_{\text{peak}} \ge +0.015$，状态跃迁至 `S2_TRAILING`。
  3. **`S2_TRAILING`（呼吸式自适应移动止盈态，$r_{\text{peak}} \ge +1.5\%$）**：
     - **默认参数（子状态 `S2_A`）**：回撤容忍度 $\delta_{\text{trail}} = 0.0050$（$0.50\%$），保底底线 $r_{\text{floor}} = 0.0100$（$+1.00\%$）。
     - **第一阶：破量脉冲主升浪豁免（子状态 `S2_B`）**：
       当实时 $5\text{ m}$ 滚动相对成交量 $\text{RVOL}_{\text{roll}} \ge 2.20$ 且回撤期间主动卖盘占比 $\eta_{\text{sell}} \le 0.58$（缩量良性换手洗盘，非巨量砸盘）时：
       $$\delta_{\text{trail}} = \min\Big(0.0085, \; \max\big(0.0050, \; 0.0050 + 0.0025 \times \ln(\text{RVOL}_{\text{roll}} / 1.5)\big)\Big)$$
       同时，只要历史最高收益率 $r_{\text{peak}} \ge 0.020$（$+2.0\%$），将刚性保底铁底上移至 $r_{\text{floor}} = 0.0125$（$+1.25\%$）。
     - **第二阶：超买乏力与横盘极速收割（子状态 `S2_C`）**：
       当 $r_{\text{peak}} \ge 0.020$（$+2.0\%$）且满足以下任一衰竭条件时：
       - 条件 ①（RSI 极值钝化拐头）：`5m` $\text{RSI}(6) \ge 82.0$ 且 $\Delta \text{RSI}_{5\text{m}} \le -3.5$；
       - 条件 ②（高位横盘滞涨）：距离上次创新高时间 $t - t_{\text{new\_high}} \ge 600\text{ s}$（$10$ 分钟）且卡尔曼滤波速度 $\hat{v}_t \le 0$；
       系统立即切换至 `S2_C`，将回撤容忍度极速收紧为 $\delta_{\text{trail}} = 0.0025$（$0.25\%$）。
     - **触发判定**：计算当前动态止盈线 $r_{\text{trigger}}(t) = \max\big(r_{\text{floor}}, \; r_{\text{peak}}(t) - \delta_{\text{trail}}(t)\big)$。一旦 $r(t) \le r_{\text{trigger}}(t)$，立即返回 `TRIGGER_TP2_ADAPTIVE`。

### 2. `crypto-bot-server/app/execution/binance_spot_executor.py`
实现币安现货实盘 API 执行器与账户 `UserDataStream` 监听器：
- **精度截断器与真实净持仓核算（【审计修复：彻底杜绝 `-2010` 余额不足拒单】）**：
  - 严格按 `SOLUSDT`（`tickSize=0.01`, `stepSize=0.001`）与 `BNBUSDT` 过滤器执行 `Decimal` 向下取整截断（`ROUND_DOWN`）。
  - 解析买单成交回报（`fills` 数组或 `executionReport`）时，若存在 `commissionAsset == "SOL"`（例如账户 BNB 耗尽时直接扣减了 SOL 作为手续费），必须从成交总数量 `executedQty` 中精确减去以 `SOL` 计价的 `commission` 之和，并按 `stepSize = 0.001` 向下截断得到真实可卖净持仓数量 `qty_net`；卖出平仓时固定取 `min(pos.qty_net, account_free_sol)` 下单。
  - 每当成功建立一笔新的自营实盘或影子持仓时，立即将 `system_config` 中的 `last_sl_price` 与 `last_sl_time` 清零为 `NULL`（完成补丁 2 重入锁闭环）。
- **结构化 `newClientOrderId` 编码**：
  - 机器人买单：`CBOT_BUY_<timestamp>_S<slot>_R<retry>`
  - 机器人卖单：`CBOT_SELL_<timestamp>_P<pos_id>_<reason>`
  - 手动单托管卖单：`CBOT_MNG_<timestamp>_P<pos_id>_<reason>`
  - 每日 BNB 补给单：`CBOT_BNB_<YYYYMMDD>`
- **3 秒追单、部分成交与 `-2011` 撤单竞态保护状态机**：
  - 买单发出后等待 $3.0\text{ s}$：若完全成交则转入持仓；若未完全成交，立即调用 `DELETE /api/v3/order` 撤单。
  - **【审计修复：捕获 `-2011` 未知订单异常】**：若撤单请求抛出 `-2011 (Unknown order sent)`，说明订单在网络传输瞬间已完全成交（或已被系统撤销），此时严禁直接当作撤单成功重发新单，必须立即调用 `GET /api/v3/order` 查询该 `clientOrderId` 的真实 `status` 与 `executedQty` 进行状态对齐。
  - 若撤单后发现已部分成交金额 $\ge 15.0\text{ USDT}$，且当前买一价相比初始触发价已上涨超过 $+0.3\%$（防追高保护），则**不再追单剩余额度，直接将已成交净额作为有效仓位转入 `PositionStateMachine`**；若涨幅未超 $+0.3\%$，则对剩余未成交金额重算最新盘口最优限价追单（最多追单 $3$ 次）。
  - 卖单平仓追单：若平仓限价卖单 $3.0\text{ s}$ 未完全成交，立即撤单（含 `-2011` 对账保护）并以最新买一价下浮 $0.04\%$ 重新挂单，循环直至 $100\%$ 卖出完毕。
- **启动期孤儿挂单对账恢复（`reconcile_open_orders_on_startup`）**：
  - 进程启动时主动调用币安 `GET /api/v3/openOrders?symbol=SOLUSDT`，检查是否存在前缀为 `CBOT_` 的遗留未成交挂单，若有则先行撤单并将已部分成交的净额同步至 `positions` 表。
- **每日首单自适应 BNB 智能补给机制**：
  - 当实盘触发当日第一笔 `SOLUSDT` 买入时（检查 `system_config.last_bnb_buy_date != today_str`），查询账户当前可用 `BNB` 数量并乘以 `BNBUSDT` 实时买一价：
  - 若折合价值 $< 20.0\text{ USDT}$，动态读取 `BNBUSDT` 的 `MIN_NOTIONAL` / `NOTIONAL` 阈值 $M_{\text{min}}$（默认兜底 $5.0\text{ USDT}$），使用 `asyncio.gather()` 与主买单并发发送一笔金额为 $\max(6.0, \; M_{\text{min}} \times 1.05)\text{ USDT}$ 的 `BNBUSDT` 保护性限价买单，成交后更新 `last_bnb_buy_date = today_str`；若可用 `BNB` 价值 $\ge 20.0\text{ USDT}$，记录日志并自动跳过当日补给。
- **手动订单自动识别与一键托管机制**：
  - 监听币安 `UserDataStream` 的 `executionReport`：凡检测到 `SOLUSDT` 买单 `FILLED` 且 `clientOrderId` **不以 `CBOT_` 开头**，自动在 `positions` 表生成 `source="USER_MANUAL", is_managed=0, slot_index=NULL` 的独立持仓卡片，并触发 FCM 推送提醒用户是否开启托管。
  - 若用户在币安官方 App 手动卖出了某笔持仓，收到非 `CBOT_` 卖单成交回报后自动核销对应持仓记录。
  - **开启托管接口（`toggle_manual_order_managed`）**：
    - 默认以该手动单的**原买入成交均价 $P_{\text{entry\_manual}}$** 作为基准价计算盈亏。
    - **边界保护**：若用户点击开启托管瞬间，当前买一价相比原买入均价已经跌破 $-0.8\%$（$r(t) \le -0.008$），接口返回 `status="REQUIRE_CONFIRMATION"` 及两个选项供 App 弹窗确认：
      - `action="IMMEDIATE_STOP_LOSS"`：立即按当前最优限价止损卖出；
      - `action="RESET_BENCHMARK_TO_CURRENT"`：将当前市场买一价设为新的 $0\%$ 基准入场价（`entry_price = current_bid`），从零开始监控 $-0.8\%$ 止损与 $+1.0\% / +1.5\%$ 止盈。

### 3. `crypto-bot-server/app/execution/shadow_matcher.py` 与 `dual_track_router.py`
- **`ShadowMatcher`（高保真影子仿真撮合器）**：
  - 当收到买入/卖出指令或限价单时，直接读取那一毫秒币安真实的 `Depth5` 五档买卖挂单簿（`bids` / `asks`），按订单目标数量逐档吃单计算真实加权成交均价，并严格扣除单边 $0.075\%$ 虚拟手续费，独立维护 $2000\text{ USDT}$ 虚拟资金池与最多 $2$ 个虚拟持仓槽位。
  - 同时调用 `ws_stream_manager.get_recent_depth_slices(60)` 将开仓/平仓前后 $60\text{ s}$ 的 `Depth5` 盘口切片以 `.json.gz` 压缩格式异步保存至 `./shadow_data/trade_orderbooks/`。
- **`DualTrackRouter`（同源双轨路由器）**：
  - 策略引擎每次产出合格信号时：
    - **无条件**路由至 `Crypto-shadow` 影子引擎执行建仓；
    - 检查 `system_config.bot_live_enabled`：若为 `True`，同步路由至 `Crypto-bot` 实盘引擎执行建仓；若用户在持仓期间关闭了 `bot_live_enabled`（**优雅停机规范**），立即拦截新买单，但存量实盘持仓继续运行 `PositionStateMachine` 直至全部正常止盈/止损平仓。

---

## 【Plan Mode 执行与 QA 验收要求】
1. 先输出 **Sprint 3 实现计划（Plan）**。
2. 编写完整代码后，创建单元测试 `tests/test_sprint3_execution_and_fsm.py`：
   - 验证【补丁 4】完整轨迹：价格冲至 $+2.0\%$（$\text{RVOL}=2.8$）后缩量回撤 $-0.6\%$ 至 $+1.4\%$（验证 `S2_B` 豁免不平仓，保底线抬至 $+1.25\%$），随后二次冲高至 $+2.5\%$ 后 `RSI(6)=85` 拐头（验证 `S2_C` 收紧回撤至 $0.25\%$，在 $+2.25\%$ 精准触发止盈）；
   - 验证买单成交扣除 `SOL` 手续费时 `qty_net` 精确向下截断，平仓卖出不触发 `-2010`；
   - 验证 3 秒撤单抛出 `-2011` 异常时自动调用查单接口对齐状态，绝不重复发单；
   - 验证每日首单 BNB 价值 $< 20\text{ USDT}$ 时按动态 `NOTIONAL` 保护买 BNB，$\ge 20\text{ USDT}$ 时自动跳过；
   - 验证手动订单已浮亏 $-1.2\%$ 时开启托管的二次确认双选项逻辑。