# Role & Context
你是由 **5. 交易执行工程师**、**6. 风控工程师** 与 **1. 项目总架构师** 组成的交易核心研发引擎。
请基于 **Phase 1 & Phase 2** 的数据库与数学模型，为 `SOL-bot` 编写 **Phase 3：风控守门员、实盘 `Crypto-bot` 与影子 `Crypto-shadow` 同源双轨执行引擎、非机器人手动单识别与一键托管模块、以及 BNB 手续费自动补仓模块** 的完整 Python 代码。

---

## 1. 核心业务规则与风控铁律（必须 100% 严格落实）

### 1.1 实盘 `Crypto-bot` 与影子 `Crypto-shadow` 同源双轨架构
* **100% 共享大脑**：`Crypto-bot`（实盘）与 `Crypto-shadow`（虚拟盘）共享同一套行情扫描流、四因子策略模型、数学定价模型与风控参数配置。
* **执行层分流**：
  1. **通道 A（`Crypto-bot` 实盘引擎）**：
     * **唯一前置安全锁**：开仓前必须检查 `trading_config.live_switch_enabled == 1`（即用户在 App 打开了实盘授权开关）且内存 `CryptoVault` 已解锁 Binance API Key。若开关关闭，实盘通道静默跳过，绝不发起任何 Binance 下单请求。
     * **订单染色协议**：所有由 `Crypto-bot` 自主发出的订单，`newClientOrderId` 必须强制携带前缀 `BOT_AUTO_<timestamp>_<uuid4>`，以便与用户自己在币安手动操作的订单严格区分。
  2. **通道 B（`Crypto-shadow` 影子模拟引擎）**：
     * $24 \times 7$ 全天候运行（不受实盘 LIVE 开关限制），拥有独立的 **$2000\text{ USDT}$ 初始虚拟资金账户** 与独立持仓表（`account_mode = 'SHADOW'`）。
     * **高拟真盘口撮合算法**：当影子引擎挂出限价买单 $P_{\text{buy}}^*$ 时，实时比对 Binance `bookTicker` 的卖一价 $P_a$ 与卖一量 $Q_a$（当 $P_{\text{buy}}^* \ge P_a$ 时按 $P_a$ 立即撮合成交，当 $P_{\text{buy}}^* < P_a$ 时等待盘口 $P_a \le P_{\text{buy}}^*$ 成交；若 $3\text{ s}$ 未成交同样执行微磨损追单逻辑），并按 $0.075\%$ 扣除虚拟手续费，为策略优化提供 100% 真实的回测与历史对照数据。

### 1.2 两笔持仓（Max 2 Positions）隔离与风控守门员规则
系统默认最多同时持有 $2$ 笔 `SOL/USDT` 机器人自主仓位（`origin_type = 'BOT_AUTO'` 且 `status = 'OPEN'`）：
1. **首笔开仓条件**：当前持仓数为 $0$，且账户可用 USDT $\ge 300\text{ USDT}$（若账户总资金低于 $2000\text{ USDT}$，只要够第一笔 $700\text{ USDT}$ 或满足动态下限即可开仓）。
2. **第 2 笔加仓隔离铁律（专家推荐方案 A）**：当已有 $1$ 笔持仓正在运行时，仅当同时满足以下条件之一且再次出现高置信度信号（$S_{\text{total}} \ge 80\%$ 或触发止损反弹补丁）时，才允许开启第 $2$ 笔：
   * **条件 ①（浮盈安全垫解锁）**：第 $1$ 笔持仓当前浮盈率 $r_1(t) \ge +0.80\%$；
   * **条件 ②（价差拉开防重叠）**：当前市场价格与第 $1$ 笔持仓买入价 $P_{\text{entry}, 1}$ 的偏离幅度 $\frac{|P(t) - P_{\text{entry}, 1}|}{P_{\text{entry}, 1}} \ge 1.50\%$，且距离第 $1$ 笔开仓时间已超过 $15\text{ min}$。
3. **防重复下单幂等锁**：同一 `5m` K 线周期内最多只允许开仓 $1$ 次，并使用 `asyncio.Lock` 防止并发重入。

### 1.3 非机器人手动订单识别与“一键托管”功能（不计入 Bot 战绩）
1. **实时手动买单自动捕获**：
   * 后端通过 Binance Spot User Data Stream 监听账户 `executionReport` 事件。
   * 当检测到 `symbol == 'SOLUSDT'`、`side == 'BUY'`、`orderStatus == 'FILLED'`，且 `clientOrderId` **不以 `BOT_` 开头** 时，自动将其识别为 **用户手动交易订单（`origin_type = 'USER_MANUAL'`）**。
   * 自动在 `positions` 表插入一条记录：`origin_type = 'USER_MANUAL'`，`is_managed = 0`（默认未托管），`count_in_bot_stats = 0`（强制不计入机器人战绩），并推送到 App 持仓面板展示为“手动订单卡片”。
2. **存量 SOL 持仓手动登记支持**：
   * 提供 REST 接口允许用户将系统启动前已持有的存量 `SOL` 余额创建为一张 `USER_MANUAL` 持仓卡片（由用户在 App 端输入或确认买入成本均价 $P_{\text{entry}}$ 与数量）。
3. **一键打开“托管按钮”接管止盈止损**：
   * 当用户在 App 端点击自己的手动订单卡片上的 **“托管开关（Hosting Toggle）”** 时，将 `is_managed` 置为 $1$。
   * 此时 `Crypto-bot` 的 L0 级止盈止损监控引擎立即接管该笔订单，按照机器人的 **移动止盈（$+1\%$ 保底、$+1.5\%$ 激活、$-0.5\%$ 回撤 + RVOL 放量豁免补丁）** 与 **$-0.8\%$ 防插针止损** 自动挂出限价卖单（`clientOrderId` 前缀设为 `BOT_HOSTED_SELL_...`）。
   * **战绩严格隔离**：该笔托管订单平仓获得的利润或亏损，单独展示在“手动托管收益”栏，**绝不计入 `Crypto-bot` 的胜率、总收益率与交易成绩统计**（`WHERE count_in_bot_stats = 1`）。

### 1.4 每日开仓前 BNB 手续费自动检查与补仓模块
* **规则**：使用 `BNB` 抵扣币安现货交易手续费（享 $25\%$ 折扣，单边费率 $0.075\%$）。
* **执行逻辑**：在每日首次开仓前（以及后台每 $6\text{ h}$ 定时巡检一次），查询现货账户 `BNB` 余额并结合 `bnbusdt@bookTicker` 最新价计算其美元市值 $V_{\text{BNB}}$：
  * 若 $V_{\text{BNB}} < 3.0\text{ USDT}$，则自动触发 `BNBUSDT` 现货买入逻辑；
  * **专家确认微调值**：为满足币安 `MIN_NOTIONAL` 限制并留出安全余量，单次自动买入固定等值 **$11.5\text{ USDT}$** 的 `BNB`（按 `BNBUSDT` 卖一价挂微磨损限价单买入，并確保补仓后总持有 `BNB` 市值不超过 **$20.0\text{ USDT}$** 上限）。

---

## 2. 待生成交付物清单（请在项目中创建以下文件）
1. `backend/exchange/binance_spot_client.py`：封装带 HMAC-SHA256 签名、时间偏移自动校准 (`recvWindow=5000`)、`LOT_SIZE` / `PRICE_FILTER` 精度规整、限价单 $3\text{ s}$ 未成交撤单重挂状态机的 Binance Spot REST & User Data Stream 客户端。
2. `backend/engine/bnb_fee_manager.py`：实现 `BNB` 余额低于 $3\text{ USD}$ 自动补仓 $11.5\text{ USD}$（上限 $20\text{ USD}$）的完整逻辑。
3. `backend/engine/risk_gatekeeper.py`：实现两笔持仓隔离校验、余额校验、自定义参数动态缩放与异常熔断器（连续 API 报错或单日极端异常时暂停开新仓并推送告警）。
4. `backend/engine/dual_execution_router.py`：实现实盘 `Crypto-bot` 与影子 `Crypto-shadow` 的双轨调度器、手动订单识别与托管平仓执行器。

---

## 3. 代码质量与验收标准
* 限价单遇到部分成交（`PARTIALLY_FILLED`）再撤单追单时，必须精确计算剩余未成交数量 `remaining_qty = orig_qty - executed_qty`，严禁超额重复买入。
* 影子引擎 `Crypto-shadow` 的所有成交与平仓必须完整写入 SQLite `positions` 表（`account_mode='SHADOW'`），并提供与实盘完全一致的统计查询接口。