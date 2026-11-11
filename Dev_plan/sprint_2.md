# Sprint 2 Plan-Mode Prompt：WebSocket 行情采集、自适应卡尔曼滤波、模式 A + 补丁 3 策略引擎与高数定价模型

> **使用方式**：在 Sprint 1 验收通过后，在 Antigravity + 4 Argon 的 **Plan Mode** 中输入本 Prompt。

---

## 【角色激活与任务目标】
你现在由 **加密货币市场金融分析师**、**量化策略专家** 与 **金融数学家** 联合驱动。请在 **Plan Mode** 下完成 `Crypto-bot` 的 **Sprint 2：低内存单连接多路复用行情采集器（含 1m K 线与 60s 盘口切片缓存）、显式过程噪声协方差卡尔曼滤波器、带恐慌见底豁免的宏观四态识别器、模式 A 超卖反弹 + 补丁 2/3 评分矩阵，以及定义域完备的高等数学动态仓位与微磨损限价定价模型**。

---

## 【待生成文件清单与数学算法规范】

### 1. `crypto-bot-server/app/market/kalman_filter.py`
实现针对 `SOLUSDT` 非均匀时间序列的一维状态空间卡尔曼滤波器（`KalmanPriceFilter`）：
- 状态向量 $\mathbf{x}_t = \begin{bmatrix} \hat{p}_t \\ \hat{v}_t \end{bmatrix}$，其中 $\hat{p}_t$ 为滤波后真实潜在价格，$\hat{v}_t$ 为瞬时价格变化速度（斜率，单位：$\text{USDT}/\text{s}$）。
- **【审计修复：非均匀步长 $\Delta t$ 保护与连续白噪声加速度协方差矩阵 $\mathbf{Q}(\Delta t)$】**：
  - 记步长 $\Delta t = \text{Clip}(t - t_{\text{prev}}, \; 0.001, \; 10.0)$（防止同毫秒 Tick 导致 $\Delta t = 0$ 或断线重连后步长过大）。
  - 状态转移矩阵 $\mathbf{F}(\Delta t) = \begin{bmatrix} 1 & \Delta t \\ 0 & 1 \end{bmatrix}$，观测矩阵 $\mathbf{H} = \begin{bmatrix} 1 & 0 \end{bmatrix}$。
  - 显式定义随 $\Delta t$ 动态缩放的过程噪声协方差矩阵：
    $$\mathbf{Q}(\Delta t) = q_a^2 \begin{bmatrix} \frac{1}{3}\Delta t^3 & \frac{1}{2}\Delta t^2 \\ \frac{1}{2}\Delta t^2 & \Delta t \end{bmatrix}$$
    其中过程加速度噪声标准差默认取 $q_a = 0.05\text{ USDT}/\text{s}^2$，确保高频成交簇下速度估计 $\hat{v}_t$ 平滑收敛不发散。
- 支持自适应测量噪声协方差 $R = \max\big(0.0001, \; (\text{ask}_1 - \text{bid}_1)^2 \times 2.5\big)$，提供 `update(price: float, spread: float, timestamp: float) -> tuple[float, float]` 方法，输出 $(\hat{p}_t, \hat{v}_t)$。

### 2. `crypto-bot-server/app/market/ws_stream_manager.py`
针对 `2vCPU + 2GB RAM` 服务器优化的单连接多路复用 WebSocket 客户端：
- **【审计修复：补齐 `1m` K 线订阅与 $60\text{ s}$ `Depth5` 环形缓存】**：
  - 通过单一币安 Combined Stream URL 订阅：`solusdt@kline_1m`、`solusdt@kline_5m`、`solusdt@kline_15m`、`solusdt@kline_1h`、`solusdt@kline_4h`、`solusdt@depth5@100ms`、`solusdt@bookTicker`、`solusdt@aggTrade` 以及 `bnbusdt@bookTicker`。
- **固定容量环形内存缓冲区（Ring Buffer）**：
  - 使用 `collections.deque(maxlen=N)` 严格限制内存占用：`4h`/`1h` 各保留 $100$ 根，`15m` 保留 $150$ 根，`5m` 保留 $200$ 根，`1m` 保留 $300$ 根（每根 K 线收盘时异步写入 SQLite `kline_cache` 表，供 Sprint 4 全周期归档）。
  - 维护 `depth5_history = collections.deque(maxlen=600)`，缓存最近 $60\text{ s}$ 的五档盘口快照 `(timestamp, bids, asks)`，提供 `get_recent_depth_slices(window_sec=60)` 接口供影子撮合器归档开平仓盘口切片。
  - `aggTrade` 维护最近 $300\text{ s}$ 的时间戳双端队列，以 $\mathcal{O}(1)$ 复杂度增量维护 $300\text{ s}$ 滚动总成交量 $V_{\text{roll}, 300}$、滚动主动买盘成交量 $V_{\text{taker\_buy}, 300}$、滚动最高价 $P_{\text{high}, 300}$、滚动最低价 $P_{\text{low}, 300}$，以及最近 $15\text{ m}$ 的滚动成交量加权均价 $\text{VWAP}_{15\text{m}}$。
- **断线重连与心跳监控**：内置指数退避自动重连；若超过 $10\text{ s}$ 未收到任何市场推送，立即主动断开重连并触发回调通知 FCM 预警模块。

### 3. `crypto-bot-server/app/strategy/regime_detector.py`
实现 `1H` 与 `4H` 周期的宏观市场状态分类器（`MarketRegimeDetector`）：
- 实时计算 `1H` 与 `4H` 的 $\text{EMA}_{20}, \text{EMA}_{50}$ 及相对真实波动率 $\text{ATR}_{14} / P$。
- **【审计修复：解耦瀑布熔断与模式 A 极值超卖死锁】**：
  输出 5 种枚举状态：
  1. `BULL_TREND`（多头趋势态）：正常开启扫描，仓位体制系数 $\kappa = 1.00$；
  2. `RANGE_BOUND`（宽幅震荡态）：正常开启扫描，仓位体制系数 $\kappa = 0.90$；
  3. `CAPITULATION_REBOUND_ALLOWED`（恐慌衰竭极值抄底豁免态）：当满足瀑布特征，但同时出现 `15m` $\text{RSI}(6) \le 22.0$、$300\text{ s}$ 滚动相对量能 $\text{RVOL}_{\text{roll}} \ge 2.20$、主动买盘占比 $\eta_{\text{buy}} \ge 0.62$ 且卡尔曼速度 $\hat{v}_t > 0$ 时，判定为空头恐慌盘宣泄见底、主力放量抄底承接，豁免熔断并允许模式 A 开仓，仓位体制系数 $\kappa = 0.85$；
  4. `CRASH_FREEFALL`（极端瀑布熔断态）：当（`15m` 连续出现 $\ge 2$ 根实体跌幅 $> 1.2\%$ 的放量长阴线，或价格跌破 `4H` $\text{EMA}_{50}$ 且 `15m` 主动卖盘占比 $> 65\%$）且**未触发上述恐慌衰竭见底豁免条件**时，强制熔断一切买入信号；
  5. `LOW_VOL_SQUEEZE`（死水微澜休眠态）：当 `1H` $\text{ATR}_{14} / P < 0.0035$（波动率不足 $0.35\%$）时休眠。

### 4. `crypto-bot-server/app/strategy/rsi_rvol_scorer.py`
实现专注于“模式 A（超卖反弹）”且集成 **【补丁 2：止损后重入双轨锁】** 与 **【补丁 3：RVOL 防诱多一票否决】** 的策略评分引擎（实时输出 $0\% \sim 100\%$ 仪表板数据）：
- **Wilder 平滑 $\text{RSI}(6)$ 计算**：精准计算 `15m` $\text{RSI}(6)$ 与 `5m` $\text{RSI}(6)$ 及其拐头动量 $\Delta \text{RSI}_{5\text{m}}(6) = \text{RSI}_{5\text{m}, t} - \text{RSI}_{5\text{m}, t-1}$。
- **300 秒滚动相对成交量 $\text{RVOL}_{\text{roll}}$ 计算**：
  $$\text{RVOL}_{\text{roll}}(t) = \frac{V_{\text{roll}, 300}(t)}{\max\big(1.0, \; \text{EMA}(V_{5\text{m}}, 20)_{t-1}\big)}$$
- **四维评分矩阵（满分 $100$ 分）**：
  1. **宏观与波动健康度（满分 $25$ 分）**：非熔断/非休眠态（$+15$ 分），`1H` $\text{ATR}_{14}/P \ge 0.0045$（$+10$ 分，在 $[0.0035, 0.0045]$ 之间线性插值）。
  2. **模式 A 多周期 RSI(6) 超卖反弹共振（满分 $35$ 分）**：要求 `15m` $\text{RSI}(6) \le 28$ 且 `5m` $\text{RSI}(6)$ 在最近 $3$ 根 K 线内曾触及 $\le 22$ 后向上拐头（$\Delta \text{RSI}_{5\text{m}} \ge +3.5$）。根据超卖深度与拐头斜率在 $0 \sim 35$ 分之间连续打分。
  3. **RVOL 放量突破与主动买盘确认（满分 $25$ 分）**：根据 $\text{RVOL}_{\text{roll}} \ge 1.50$（最高 $+15$ 分）及主动买盘占比 $\eta_{\text{buy}} \ge 58\%$（最高 $+10$ 分）连续插值打分。
  4. **盘口微观承接力（满分 $15$ 分）**：卡尔曼滤波速度 $\hat{v}_t > 0$（$+8$ 分），且前 5 档订单簿加权失衡度 $I > 0$（$+7$ 分）。
- **【补丁 3】防诱多一票否决硬门槛（Hard Veto Gate）**：
  - 若 $\text{RVOL}_{\text{roll}} < 1.25$，无论其他维度得分多高，综合得分 $S_{\text{total}}$ **强制封顶为 $59.0\%$**，标记 `veto_reason = "LOW_RVOL_BULL_TRAP"`。
  - 触发 `BUY` 指令必须**同时满足**以下硬性条件：
    1. 宏观状态属于 `{"BULL_TREND", "RANGE_BOUND", "CAPITULATION_REBOUND_ALLOWED"}` 且 $S_{\text{total}} \ge 75.0$；
    2. $\text{RVOL}_{\text{roll}} \ge 1.50$（若处于止损后重入锁有效期内，门槛自动提升至 $\text{RVOL}_{\text{roll}} \ge 1.80$）；
    3. 主动买盘占比 $\eta_{\text{buy}} \ge 0.58$ 且主动买盘相对量能 $\text{RVOL}_{\text{taker\_buy}} \ge 1.65$；
    4. **【审计修复：防除零滚动实体饱满度】** 当前价 $P_t \ge \text{VWAP}_{15\text{m}}$ 或 $P_t > H_{5\text{m}, t-1}$，且过去 $300\text{ s}$ 滚动窗口脉冲阳线实体饱满度 $\rho_{\text{body}, 300} = \frac{P_t - P_{\text{low}, 300}}{\max(0.01, \; P_{\text{high}, 300} - P_{\text{low}, 300})} \ge 0.60$；
    5. 卡尔曼速度 $\hat{v}_t > 0$；
    6. **【补丁 2 校验与 4 小时自动过期】**：检查 `last_sl_price` 与 `last_sl_time`：若 $t - t_{\text{SL}} > 14400\text{ s}$（$4$ 小时），自动视为重入锁已过期清零；若在 $14400\text{ s}$ 内，则必须满足 **轨道 A（空间深度洗盘：$\frac{P_t - P_{\text{SL}}}{P_{\text{SL}}} \le -0.005$）** 或 **轨道 B（时间冷却震荡：$t - t_{\text{SL}} \ge 900\text{ s}$ 且 $S_{\text{total}} \ge 80.0$）**；
    7. **【补丁 1 校验】**：若当前已持有 1 个自营仓位，该仓位必须已处于 `S1_LOCKED` 或 `S2_TRAILING` 状态，否则严禁开第 2 仓。

### 5. `crypto-bot-server/app/strategy/math_pricing_model.py`
实现高等数学动态仓位分配与微磨损最优限价定价模型：
- **模型 A：RVOL 正相关动态仓位模型（$[300, 700]\text{ USDT}$）**：
  - **【审计修复：非负底数截断防止复数崩溃 + 显式定义 $\kappa$】**：
    $$\lambda_{\text{vol}} = \text{Clip}\Big(0.85 + 0.30 \times \ln\big(\max(0.1, \; \text{RVOL}_{\text{roll}})\big), \; 0.85, \; 1.30\Big)$$
    $$C_{\text{raw}} = \left[ 300.0 + 400.0 \times \left( \frac{\max(0.0, \; S_{\text{total}} - 75.0)}{25.0} \right)^{1.2} \right] \times \lambda_{\text{vol}} \times \kappa$$
    $$C_{\text{final}} = \min\Big( \text{Clip}(C_{\text{raw}}, \; 300.0, \; 700.0), \; \text{Available\_USDT\_Budget} \Big)$$
    其中 $\kappa$ 为宏观体制系数（`BULL_TREND` 取 $1.00$，`RANGE_BOUND` 取 $0.90$，`CAPITULATION_REBOUND_ALLOWED` 取 $0.85$，其余状态取 $0.85$）。即便在仪表板 $S_{\text{total}} < 75.0$ 时调用该函数预估仓位，也始终返回安全的实数浮点数。
- **模型 B：基于 5 档订单簿失衡度（OBI）的微磨损限价定价模型**：
  - 权重 $w_i = e^{-0.5(i-1)} \; (i=1,\dots,5)$，计算失衡度 $I = \frac{\sum w_i Q_{\text{bid}, i} - \sum w_i Q_{\text{ask}, i}}{\sum w_i Q_{\text{bid}, i} + \sum w_i Q_{\text{ask}, i}} \in [-1, +1]$。
  - **买入最优限价 $P_{\text{buy\_limit}}$**：当 $I \le 0.2$ 时，定于 $\min(b_1 + 0.01, a_1)$；当 $I > 0.2$（抢筹剧烈）时，启用最大 $0.04\%$ 微磨损保护，定于 $\text{round\_step}(a_1 \times (1 + 0.0004 \times I), 0.01)$，确保瞬间吃单成交且滑点严格受控在 $0.04\%$ 以内。
  - **卖出最优限价 $P_{\text{sell\_limit}}$**：平仓触发时，定于 $\text{round\_step}(b_1 \times (1 - 0.0003), 0.01)$（最大磨损不超过 $0.04\%$），确保毫秒级 100% 卖出平仓。

---

## 【Plan Mode 执行与 QA 验收要求】
1. 先输出 **Sprint 2 实现计划（Plan）**。
2. 编写完整代码后，创建单元测试 `tests/test_sprint2_strategy_and_math.py`，构造以下 6 组仿真场景验证：
   - 场景 1：当 $S_{\text{total}} = 42.0\%$ 时调用动态仓位模型，验证不抛出复数 `TypeError` 且返回有效实数；
   - 场景 2：RSI(6) 极度超卖但 $\text{RVOL}_{\text{roll}} = 0.9$（缩量诱多），验证总分被封顶在 $59\%$ 且不触发买入；
   - 场景 3：连续放量长阴线后出现 `15m` $\text{RSI}(6) = 19.5$、$\text{RVOL}_{\text{roll}} = 2.5$、主动买盘占比 $65\%$、$\hat{v}_t > 0$，验证触发 `CAPITULATION_REBOUND_ALLOWED` 豁免态并成功发出抄底信号；
   - 场景 4：刚在 $100.00\text{ USDT}$ 止损后 2 分钟，价格在 $99.80\text{ USDT}$ 反抽（跌幅仅 $-0.2\%$），验证被【补丁 2】拦截；当价格跌至 $99.10\text{ USDT}$（跌幅 $-0.9\% \le -0.5\%$）且 $\text{RVOL}_{\text{roll}} = 1.95$ 时，验证立即解锁抄真底；以及当距离止损时间超过 $14401\text{ s}$ 后，验证重入锁自动过期解除；
   - 场景 5：验证 1 号仓浮亏 $-0.4\%$ 时，第 2 仓信号被【补丁 1】严格拦截；
   - 场景 6：验证高频非均匀时间步长（$\Delta t = 0.0\text{ s}$ 与 $\Delta t = 0.002\text{ s}$）下卡尔曼滤波器数值稳定不发散。