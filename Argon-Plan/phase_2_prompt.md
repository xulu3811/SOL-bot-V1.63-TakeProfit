# Role & Context
你是由 **3. 量化策略专家** 与 **4. 金融数学家** 组成的算法核心研发引擎。
请基于 **Phase 1** 提供的数据流与配置底座，为 `SOL-bot` 编写 **Phase 2：多周期四因子策略模型、信号置信度动态仓位分配模型、微磨损限价单定价模型，以及移动止盈/防插针止损/RVOL 补丁状态机** 的纯享高内聚 Python 数学与策略模块。

---

## 1. 数学模型与策略方程严格规范（必须 100% 精确实现）

### 1.1 四因子共振策略与“策略达成百分比仪表板”算法
策略总达成度 $S_{\text{total}} \in [0.0, 100.0]$ 由以下四个正交因子得分相加构成（实时输出至 Android 策略面板仪表盘）：

1. **因子 1：`1H` 趋势滤波因子 $S_{1H} \in [0, 25]$**
   * 计算 `1H` 收盘价序列的 $\text{EMA}_{21}$、$\text{EMA}_{55}$ 及一维卡尔曼滤波（Kalman Filter）速度斜率 $v_{1H}$。
   * **一票否决硬约束**：若 $P_{1H} < \text{EMA}_{21} < \text{EMA}_{55}$ 且 $v_{1H} < -0.0015 \cdot P_{1H}$（处于 `1H` 级别单边瀑布下跌态），则强制令 `veto_flag = True`，此时禁止开立任何常规新仓。
   * 当 $P_{1H} > \text{EMA}_{21} > \text{EMA}_{55}$ 且 $v_{1H} > 0$ 时，$S_{1H} = 25$；当处于均线收敛或筑底拐头（$v_{1H} > 0$ 且 $P_{1H} > \text{EMA}_{21}$）时，$S_{1H} \in [15, 22]$ 线性插值。

2. **因子 2：`15m` 波段结构与盈亏比空间因子 $S_{15m} \in [0, 25]$**
   * 计算 `15m` 周期布林带（周期 $20$，标准差倍数 $2.0$）的位置指标 $\%B = \frac{P - \text{BB}_{\text{lower}}}{\text{BB}_{\text{upper}} - \text{BB}_{\text{lower}}}$ 以及 $\text{ATR}_{14}$。
   * 计算上方阻力空间比 $R_{\text{space}} = \frac{\text{BB}_{\text{upper}} - P}{P}$。
   * 若 $\%B > 0.88$（短线极端超买追高区）或 $R_{\text{space}} < 0.012$（上方连 $+1.2\%$ 止盈空间都不足），得分压制在 $[0, 8]$；
   * 若 $\%B \in [0.15, 0.68]$ 且 $R_{\text{space}} \ge 0.016$，得分为 $[20, 25]$。

3. **因子 3：`5m / 1m` 动量扳机因子 $\text{RSI}(6)$ $S_{\text{rsi}} \in [0, 25]$**
   * 严格使用 Wilder 平滑（$\alpha = \frac{1}{6}$）计算 `5m` 与 `1m` 的 $\text{RSI}(6)$：
     $$\text{RSI}(6) = 100 - \frac{100}{1 + \frac{\text{RMA}(\max(\Delta P, 0), 6)}{\text{RMA}(\max(-\Delta P, 0), 6)}}$$
   * **扳机模式 A（顺势急跌超卖拐头）**：`5m` $\text{RSI}(6) \in [20, 38]$ 且较上一根上升，同时 `1m` $\text{RSI}(6)$ 上穿 $45$ $\Rightarrow S_{\text{rsi}} = 25$。
   * **扳机模式 B（强势放量突破初段）**：`5m` $\text{RSI}(6) \in [55, 74]$ 且斜率为正，`1m` $\text{RSI}(6) \in [50, 78]$ $\Rightarrow S_{\text{rsi}} = 22$。
   * 其余区间按距离触发阈值的接近度映射为 $[0, 18]$。

4. **因子 4：时间进度补偿相对成交量因子 $\text{RVOL}_{5m}$ $S_{\text{rvol}} \in [0, 25]$**
   * 为解决当前 `5m` K 线尚未走完时的成交量低估问题，引入时间进度补偿因子 $\tau \in (0, 300]$（当前 `5m` K 线已走秒数）：
     $$\text{RVOL}_{5m}(t) = \frac{V_{\text{current}}(\tau)}{\left(\frac{1}{20}\sum_{i=1}^{20} V_{t-i}\right) \cdot \max\left(\frac{\tau}{300}, 0.20\right)}$$
   * 若当前 `5m` K 线为阳线（$P_{\text{close}} > P_{\text{open}}$）且 $\text{RVOL}_{5m} \ge 1.8$，$S_{\text{rvol}} = \min\left(25.0, 18.0 + 7.0 \times \frac{\text{RVOL}_{5m} - 1.8}{1.2}\right)$；若缩量或阴线放量砸盘，得分按比例扣减。

* **开仓判定准则**：当 `veto_flag == False` 且 $S_{\text{total}} = S_{1H} + S_{15m} + S_{\text{rsi}} + S_{\text{rvol}} \ge 80.0$ 时，触发自主买入信号，输出归一化置信度 $C = \frac{S_{\text{total}}}{100.0} \in [0.80, 1.00]$。

---

### 1.2 高等数学模型一：置信度动态仓位分配公式（兼容用户自定义参数）
* 默认模式下，$W_{\max} = 700.0\text{ USDT}$，$W_{\min} = 300.0\text{ USDT}$。
* **自定义配置模式（专家推荐方案）**：当用户在 App 开启“自定义配置开关”并设置新的单笔上限 $W_{\max}^{\text{user}}$ 时，实盘与影子引擎同步将上限设为 $W_{\max} = W_{\max}^{\text{user}}$，并将动态下限设为 $W_{\min} = 0.45 \times W_{\max}$。
* **修正凯利 + Sigmoid 连续动态定仓公式**：
  $$W(C, \sigma_{15m}) = W_{\min} + (W_{\max} - W_{\min}) \cdot \left[ \frac{1}{1 + \exp\left(-25 \cdot (C - 0.88)\right)} \right] \cdot \min\left(1.0, \frac{\sigma_{\text{target}}}{\sigma_{15m}}\right)$$
  其中 $\sigma_{15m} = \frac{\text{ATR}_{14, 15m}}{P}$，目标基准波动率 $\sigma_{\text{target}} = 0.008$。
* **余额边界检查**：
  * 若账户总资金低于 $2000\text{ USDT}$，只要可用余额够第一笔上限（默认 $\ge 700\text{ USDT}$，或已持有一笔时剩余可用余额 $\ge W_{\min}$），均允许正常开仓，最终下单金额取 $W_{\text{final}} = \min(W(C, \sigma_{15m}), \text{Available\_USDT})$。

---

### 1.3 高等数学模型二：盘口失衡度 ($OBI$) 与微磨损最优限价单定价模型
1. **盘口压差失衡度**：
   $$OBI = \frac{Q_b - Q_a}{Q_b + Q_a} \in [-1, 1]$$
2. **买入限价单最优价格 $P_{\text{buy}}^*$（允许微幅磨损，确保成交）**：
   $$P_{\text{buy}}^* = \begin{cases} 
   P_b + \text{TickSize}, & \text{若 } OBI < 0.2 \text{ 且 } \text{RVOL}_{5m} < 2.0 \\
   P_a \cdot \left(1 + 0.0003 \cdot \max(0, OBI)\right), & \text{若 } OBI \ge 0.2 \text{ 或 } \text{RVOL}_{5m} \ge 2.0
   \end{cases}$$
   * 若第 $1$ 次挂单在 $3\text{ s}$ 内未完全成交，第 $2$ 次追单价格上调为 $P_a \cdot (1 + 0.0005)$（最大容忍 $0.05\%$ 微磨损），随后按币安 `PRICE_FILTER` (`tickSize = 0.01`) 向下取整规范化。
3. **卖出限价单最优价格 $P_{\text{sell}}^*$（允许微幅磨损，确保平仓逃生）**：
   * 常规止盈平仓：$P_{\text{sell}}^* = P_b \cdot (1 - 0.0002)$（万分之二微磨损穿透买一）。
   * 触发 $-0.8\%$ 止损紧急平仓：$P_{\text{sell}}^* = P_b \cdot (1 - 0.0006)$（万分之六微磨损确保瞬间成交出局）。

---

### 1.4 高等数学模型三：移动止盈、防插针止损与双 RVOL 补丁状态方程
设持仓买入均价为 $P_{\text{entry}}$，当前 $3\text{ s}$ 防插针平滑价为 $\bar{P}_{3s}(t)$，持仓期间记录的最高价为 $P_{\max}(t)$：
* 当前毛收益率：$r(t) = \frac{\bar{P}_{3s}(t) - P_{\text{entry}}}{P_{\text{entry}}}$
* 历史最高毛收益率：$r_{\max}(t) = \frac{P_{\max}(t) - P_{\text{entry}}}{P_{\text{entry}}}$

1. **不设上限移动止盈 + 放量突破 RVOL 豁免补丁**：
   * **激活条件**：当 $r_{\max}(t) \ge \theta_{\text{act}}$（默认 $+1.5\%$，支持用户自定义覆盖）时，`trailing_active = 1`。
   * **动态回撤容忍度 $\Delta_{\text{dd}}(t)$（专家推荐方案 A）**：
     $$\Delta_{\text{dd}}(t) = \begin{cases} 
     \max\left(0.010, \, 1.5 \times \frac{\text{ATR}_{5m}}{\bar{P}_{3s}(t)}\right), & \text{当 } \text{RVOL}_{5m} > 2.5 \text{ 且当前 5m K 线为阳线（大绿柱放量突破期）} \\
     \theta_{\text{dd}} \text{（默认 } 0.005 \text{ 即 } -0.5\%\text{）}, & \text{常规状态或放量回落期}
     \end{cases}$$
   * **触发止盈判据**：当 `trailing_active == 1` 且满足：
     $$r_{\max}(t) - r(t) \ge \Delta_{\text{dd}}(t) \quad \text{且} \quad r(t) \ge \theta_{\text{min\_tp}} \text{（默认 }+1.0\%\text{ 最低止盈保底线）}$$
     立即输出 `ACTION_TAKE_PROFIT` 指令！（例如最高涨至 $+1.80\%$，回撤 $-0.51\%$ 至 $+1.29\%$，因 $+1.29\% \ge +1.00\%$，立即在 $+1.29\%$ 触发限价单止盈）。

2. **$-0.8\%$ 防插针止损 + 二次触底反弹（$-0.9\% + \text{RVOL} > 2.0$）进场补丁**：
   * **止损触发判据**：当 $3\text{ s}$ 平滑价格满足 $\bar{P}_{3s}(t) \le P_{\text{entry}} \times (1 - \theta_{\text{sl}})$（默认 $\theta_{\text{sl}} = 0.008$ 即 $-0.8\%$）时，立即输出 `ACTION_STOP_LOSS` 指令，并记录实际止损成交价 $P_{\text{sl}}$。
   * **二次触底反弹监控状态机（Re-entry Patch）**：
     1. 止损成交后，写入 `sl_rebound_watcher` 表，设定深跌阈值价 $P_{\text{target}} = P_{\text{sl}} \times (1 - 0.0090)$（在止损成交价基础上再跌至少 $0.9\%$）。
     2. 持续追踪止损后的最低价 $P_{\min\_\text{after\_sl}}$。一旦 $P_{\min\_\text{after\_sl}} \le P_{\text{target}}$，将状态置为 `is_armed = 1`（进入待反弹击发态）。
     3. 在 `is_armed == 1` 状态下，当某根 `5m` K 线满足 **放量阳线反包**（即 $P_{\text{close}, 5m} > P_{\text{open}, 5m}$ 且 $P_{\text{close}, 5m} > P_{\text{open}, 5m, t-1}$ 阳线实体包络前一根阴线实体，同时 $\text{RVOL}_{5m} > 2.0$），无视 `1H` 均线限制，立即输出 `ACTION_REBOUND_BUY` 二次进场信号（置信度固定赋予 $C = 0.90$）！

---

## 2. 待生成交付物清单（请在项目中创建以下文件）
1. `backend/strategy/indicators.py`：实现零依赖、高性能 NumPy/纯 Python 增量计算版 $\text{EMA}$、Wilder $\text{RSI}(6)$、Bollinger Bands $\%B$、$\text{ATR}_{14}$、一维 Kalman Filter 及时间补偿 $\text{RVOL}_{5m}$。
2. `backend/strategy/multi_factor_model.py`：实现四因子评分、一票否决判定与实时仪表盘百分比输出结构 `StrategyDashboardState`。
3. `backend/math_models/execution_math.py`：实现动态仓位分配函数 `calculate_dynamic_position_size()`、盘口失衡度 $OBI$ 与最优限价单定价函数 `calculate_optimal_limit_price()`。
4. `backend/strategy/tp_sl_state_machine.py`：实现移动止盈、RVOL 大绿柱豁免补丁、$-0.8\%$ 3秒平滑止损与 $-0.9\%$ 止损后二次反弹状态机。

---

## 3. 代码质量与验收标准
* 所有数学公式必须包含详尽的 Docstring 注释及边界保护（如除零保护 `1e-9`、NaN 检查）。
* 必须附带 `tests/test_phase2_math.py`，针对 $+1.8\%$ 回撤 $-0.51\%$ 在 $+1.29\%$ 止盈、大绿柱 $\text{RVOL} > 2.5$ 豁免回撤、$-0.8\%$ 3秒防插针止损、止损后再跌 $-0.9\%$ 放量反包二次进场等核心场景编写单元测试。