# Crypto-bot & Crypto-shadow 全局架构总纲与 Antigravity + 4 Argon 执行指南 (v2.0 审计修复版)

## 一、 集成包使用说明（Antigravity + 4 Argon 协同工作流）

本开发方案集成包专为 **Antigravity + 4 Argon** 的 **Plan Mode（规划与分步执行模式）** 设计。整套系统被拆解为 **1 个全局总纲文件 + 6 个独立 Sprint 阶段 Prompt 文件**：

| 序号 | 标准文件路径 | 核心开发交付物 | 协同角色配置 |
| :--- | :--- | :--- | :--- |
| **00** | `00_Master_Architecture_and_Plan_Guide.md` | 全局常量锁定、全栈工程目录树、跨模块接口契约 | 项目总架构师、QA/独立审计工程师 |
| **01** | `01_Sprint1_Infra_Security_and_Database_Prompt.md` | 4GB Swap 脚本、UFW/Fail2ban 加固、IP 自签名证书、SQLite WAL 单写队列数据库 | 项目总架构师、数据库工程师、QA工程师 |
| **02** | `02_Sprint2_Market_Kalman_and_Strategy_Math_Prompt.md` | 币安单连接多路 WS (含 `1m` 与盘口缓存)、自适应卡尔曼滤波、模式 A + 补丁 3、无复数安全高数定价模型 | 金融分析师、量化策略专家、金融数学家 |
| **03** | `03_Sprint3_Dual_Track_Execution_and_FSM_Patches_Prompt.md` | 实盘/影子双轨引擎、补丁 1/2/4 状态机、净持仓精确核算、`-2011` 防重追单、动态 BNB 补给、手动单托管 | 交易执行工程师、量化策略专家、QA工程师 |
| **04** | `04_Sprint4_Local_Kline_Archive_and_5Horizon_Review_Prompt.md` | 本地文件夹 Parquet K线归档、盘口切片滚动清理、独立子进程内存隔离的 $1\text{W}/1\text{M}/3\text{M}/6\text{M}/1\text{Y}$ 五维复盘引擎 | 数据库工程师、量化策略专家、金融数学家 |
| **05** | `05_Sprint5_FastAPI_Gateway_WebSocket_and_FCM_Prompt.md` | FastAPI 网关、孤儿单启动对账、设备口令绑定、AES-256-GCM 无人值守恢复、6 大类 FCM 预警推送 | 项目总架构师、交易执行工程师 |
| **06** | `06_Sprint6_Android_Compose_M3_3D_Client_Prompt.md` | Android Jetpack Compose (Material 3 + 3D 扁平化四大板块)、自定义 X509 证书指纹锁定、Android 14 `specialUse` 保活 | 项目总架构师、移动端工程师、QA工程师 |

### 在 Antigravity + 4 Argon 中的标准操作步骤：
1. 将本集成包所有 `.md` 文件置于工作区根目录的 `prompts_plan/` 文件夹下，严格按照上表文件名保存。
2. 开启 Antigravity 的 **Plan Mode**，首先将 `00_Master_Architecture_and_Plan_Guide.md` 挂载为全局上下文（Pinned Context）。
3. 按顺序打开 `01` 至 `06` 号 Markdown 文件，将其中的 **【Prompt 正文】** 投喂给 Agent：
   - **Step A (Plan)**：要求 Agent 先输出该 Sprint 的文件改动清单与接口签名验证计划；
   - **Step B (Execute)**：审核通过后，指令 Agent 逐文件生成完整生产级代码（严禁省略或使用占位符）；
   - **Step C (Verify)**：运行每个 Prompt 末尾自带的 **【QA 独立审计验收脚本】**，全部通过后再进入下一个 Sprint。

---

## 二、 全局锁定参数与四大实战补丁基线（不可篡改常量）

所有 Sprint 的代码实现必须严格遵守以下已锁定的全局数学与业务常量：

1. **交易标的与双轨资金**：
   - 唯一交易对：`SOLUSDT`（币安正规现货，$0$ 杠杆，$24 \times 7$ 全天候运行）。
   - 实盘账户（`Crypto-bot`）与影子账户（`Crypto-shadow`）基准稳定币均为 `USDT`，初始/默认总预算均为 $2000\text{ USDT}$。
   - 最大同时持仓槽位数：$2$ 个（Slot 1 与 Slot 2），单笔开仓金额由数学模型在 $[300\text{ USDT}, 700\text{ USDT}]$ 区间内根据信号得分 $S_{\text{total}}$ 与量能乘数 $\lambda_{\text{vol}}$ 动态分配。
   - **毛收益率与净收益区分**：状态机触发线统一采用实时最优买一价计算的毛收益率 $r(t) = \frac{P_{\text{bid}}(t) - P_{\text{entry}}}{P_{\text{entry}}}$；落库与展示时同步计算扣除双边手续费与滑点后的净收益 `realized_pnl_usdt` 与 `realized_pnl_pct`。
2. **BNB 手续费抵扣与每日首单自适应补给**：
   - 实盘每日首次触发 `SOLUSDT` 买单时，自动检查币安现货账户可用 `BNB` 折合价值：若 $< 20\text{ USDT}$，动态读取 `BNBUSDT` 交易对的 `MIN_NOTIONAL` / `NOTIONAL` 过滤器阈值 $M_{\text{min}}$，并发执行一笔金额为 $\max(6.0, \; M_{\text{min}} \times 1.05)\text{ USDT}$ 的 `BNBUSDT` 限价买单（防止因交易所上调最小下单金额导致拒单）；若 $\ge 20\text{ USDT}$ 则跳过。
   - `Crypto-shadow` 影子引擎采用真实五档盘口（`Depth5`）加权撮合，默认单边手续费率为 $0.075\%$（双边 $0.15\%$）。
3. **【补丁 1】浮亏禁加仓法则**：
   - 当 1 号槽位持仓毛收益率 $r(t) < +1.0\%$（即尚未进入 `S1_LOCKED` 保底止盈态）时，强制冻结 2 号槽位的开仓权限；仅当 1 号仓位涨破 $+1.0\%$ 锁定保底利润后，才允许响应新信号开启第 2 仓。
4. **【补丁 2】止损后二次重入双轨过滤锁及自动生命周期（SL Re-entry Guard）**：
   - 任何仓位在 $-0.8\%$ 触发刚性止损平仓（记录止损成交价 $P_{\text{SL}}$ 与时间戳 $t_{\text{SL}}$）后，激活重入锁。在重入锁激活期间，下一次买入必须满足以下任一条件且通过 $\text{RVOL}_{\text{roll}} \ge 1.80$ 放量校验方可开仓：
     - **轨道 A（空间深度洗盘重入）**：当前价格相比止损价 $P_{\text{SL}}$ 额外下探幅度 $\le -0.5\%$（即 $\frac{P_t - P_{\text{SL}}}{P_{\text{SL}}} \le -0.005$），代表恐慌盘彻底宣泄，出现模式 A 信号立即抄真底；
     - **轨道 B（时间冷却震荡重入）**：距离止损时间 $t - t_{\text{SL}} \ge 900\text{ s}$（$15$ 分钟），且 `5m` $\text{RSI}(6)$ 底部抬高、综合得分 $S_{\text{total}} \ge 80\%$。
   - **重入锁自动清零与过期机制**：当满足以下任一条件时，重入锁自动解除并将数据库的 `last_sl_price` 与 `last_sl_time` 重置为 `NULL`：① 止损后已成功完成一笔新仓位建仓；② 距离止损时间 $t - t_{\text{SL}} > 14400\text{ s}$（$4$ 小时自然过期）。
5. **【补丁 3】RVOL 放量突破一票否决与三维防诱多验证**：
   - 采用 $300\text{ s}$ 滚动成交量计算 $\text{RVOL}_{\text{roll}}(t) = \frac{\sum_{\tau=t-300}^{t} V(\tau)}{\text{EMA}(V_{5\text{m}}, 20)}$。
   - **一票否决硬门槛**：常规开仓要求 $\text{RVOL}_{\text{roll}} \ge 1.50$（止损后重入要求 $\ge 1.80$）；若 $\text{RVOL}_{\text{roll}} < 1.25$，仪表板总分强制封顶为 $59\%$。
   - **三维真突破验证**：主动买盘占比 $\eta_{\text{buy}} \ge 58\%$ 且主动买盘相对量能 $\text{RVOL}_{\text{taker\_buy}} \ge 1.65$；现价站上 $\text{VWAP}_{15\text{m}}$ 或突破前一根 `5m` K 线最高价；过去 $300\text{ s}$ 滚动窗口脉冲实体饱满度 $\rho_{\text{body}, 300} = \frac{P_t - P_{\text{low}, 300}}{\max(0.01, \; P_{\text{high}, 300} - P_{\text{low}, 300})} \ge 0.60$。
6. **【补丁 4】呼吸式自适应移动止盈状态机与 120 小时超时平仓**：
   - **阶段 `S0_PROTECT`**（$r_{\text{peak}} < +1.0\%$）：触及 $-0.8\%$ 立即止损；持仓满 $120\text{ h}$（$5$ 天）强制按最优限价平仓。
   - **阶段 `S1_LOCKED`**（$+1.0\% \le r_{\text{peak}} < +1.5\%$）：保底止盈线锁定在 $+1.0\%$，跌破 $+1.0\%$ 立即保底止盈出局。
   - **阶段 `S2_TRAILING`**（$r_{\text{peak}} \ge +1.5\%$）：
     - **常规态（`S2-A`）**：回撤容忍度 $\delta_{\text{trail}} = 0.50\%$，保底线 $r_{\text{floor}} = +1.00\%$；
     - **破量脉冲豁免态（`S2-B`）**：当 `5m` $\text{RVOL}_{\text{roll}} \ge 2.20$ 且主动卖盘占比 $\eta_{\text{sell}} \le 58\%$ 时，弹性放宽回撤容忍度 $\delta_{\text{trail}} = \min(0.85\%, \max(0.50\%, 0.50\% + 0.25\% \times \ln(\text{RVOL}_{\text{roll}} / 1.5)))$，同时若 $r_{\text{peak}} \ge +2.0\%$，将刚性保底铁底上移至 $r_{\text{floor}} = +1.25\%$；
     - **超买乏力收割态（`S2-C`）**：当 $r_{\text{peak}} \ge +2.0\%$ 且出现（① `5m` $\text{RSI}(6) \ge 82$ 后拐头 $\Delta \text{RSI}_{5\text{m}} \le -3.5$，或 ② 连续 $600\text{ s}$ 未创新高且卡尔曼速度 $\hat{v}_t \le 0$）时，瞬间将回撤容忍度收紧至 $\delta_{\text{trail}} = 0.25\%$，锁定顶部利润。

---

## 三、 全栈标准工程目录结构契约

```text
crypto-bot-workspace/
├── prompts_plan/                           # 00~06 标准开发方案 Prompt 目录
├── crypto-bot-server/                      # Python 3.11+ (FastAPI + Asyncio) 后端工程
│   ├── deploy/
│   │   ├── 01_setup_swap_and_security.sh
│   │   ├── 02_generate_ip_tls_cert.sh
│   │   ├── nginx_cryptobot.conf
│   │   └── cryptobot.service
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── core/
│   │   │   ├── database.py
│   │   │   ├── security.py
│   │   │   └── fcm_notifier.py
│   │   ├── market/
│   │   │   ├── ws_stream_manager.py
│   │   │   ├── kalman_filter.py
│   │   │   └── kline_archiver.py
│   │   ├── strategy/
│   │   │   ├── regime_detector.py
│   │   │   ├── rsi_rvol_scorer.py
│   │   │   └── math_pricing_model.py
│   │   ├── execution/
│   │   │   ├── dual_track_router.py
│   │   │   ├── position_fsm.py
│   │   │   ├── binance_spot_executor.py
│   │   │   └── shadow_matcher.py
│   │   └── review_engine/
│   │       └── multi_horizon_reviewer.py
│   ├── shadow_data/                        # 本地/增量同步的 K 线归档与五维复盘报告库
│   │   ├── klines_daily/
│   │   ├── trade_orderbooks/
│   │   └── review_reports/
│   ├── scripts/
│   │   └── sync_shadow_to_local.sh
│   ├── tests/
│   └── requirements.txt
└── crypto-bot-android/                     # Android Jetpack Compose (minSdk 26) 客户端工程
    └── app/src/main/java/com/cryptobot/sol/
```