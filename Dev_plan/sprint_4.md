# Sprint 4 Plan-Mode Prompt：本地文件夹 K 线增量归档与独立子进程隔离的 1W/1M/3M/6M/1Y 五维周期复盘引擎

> **使用方式**：在 Sprint 3 验收通过后，在 Antigravity + 4 Argon 的 **Plan Mode** 中输入本 Prompt。

---

## 【角色激活与任务目标】
你现在由 **数据库工程师**、**量化策略专家** 与 **金融数学家** 联合驱动。请在 **Plan Mode** 下完成 `Crypto-bot` 的 **Sprint 4：每日全周期 K 线与微观因子本地文件夹 Parquet 归档器、盘口切片与 WAL 空间自动回收、双端增量同步脚本，以及采用独立子进程（Process Isolation）防 OOM 的 1周（1W）、1月（1M）、3个月（3M）、6个月（6M）、1年（1Y）五维自动化交易复盘报告与策略参数寻优引擎**。

---

## 【待生成文件清单与复盘算法规范】

### 1. `crypto-bot-server/app/market/kline_archiver.py` 与 `scripts/sync_shadow_to_local.sh`
- **每日 K 线与微观因子全量归档器（`KlineDailyArchiver`）**：
  - 使用 `pandas` / `pyarrow`（若环境未安装 pyarrow，自动无损回退至 `gzip` 压缩的 `.csv.gz` 并保持统一读取接口），每天凌晨 `00:01` 将前一日 `SOLUSDT` 的 `1m`, `5m`, `15m`, `1h`, `4h` 五个周期 K 线及实时计算出的微观因子（`rsi_6`, `rvol_roll`, `taker_buy_ratio`, `vwap_15m`, `kalman_price`, `kalman_velocity`, `market_regime`）导出至项目本地文件夹 `./shadow_data/klines_daily/YYYY-MM/SOLUSDT_YYYY-MM-DD.parquet`。
  - **【审计修复：服务器磁盘与 WAL 空间闭环保护】**：
    1. 每日归档完成后，清理 SQLite `kline_cache` 表中超过 $15$ 天的历史 K 线记录，并立即调用 `db.checkpoint_and_truncate_wal()` 执行 `PRAGMA wal_checkpoint(TRUNCATE);` 回收 WAL 日志体积；
    2. 扫描 `./shadow_data/trade_orderbooks/` 目录，自动删除超过 $30$ 天的历史盘口切片文件，确保 40GB 磁盘永不写满。
- **本地开发机一键增量同步脚本（`scripts/sync_shadow_to_local.sh`）**：
  - 支持通过 SSH/Rsync 或 HTTPS API (`/api/v1/shadow/archive_sync`) 将服务器 `./shadow_data/` 下的每日 K 线文件、盘口切片与复盘报告增量拉取至本地电脑项目文件夹 `./shadow_data/`，实现“服务器轻负载运行 + 本地项目文件夹全量永久存储”。

### 2. `crypto-bot-server/app/review_engine/multi_horizon_reviewer.py`
构建基于全量归档 K 线的 **“平仓后轨迹回溯（Post-Trade Trajectory Analysis）+ 参数网格重放寻优”** 复盘引擎（`MultiHorizonReviewer`）：

#### (1) 【审计修复：独立子进程内存隔离与低优先级调度（防 2C2G OOM 与主循环卡顿）】
- **严禁在 FastAPI 主进程协程内直接加载多月/全年 DataFrame 进行网格回放或蒙特卡洛模拟！**
- 必须使用 `multiprocessing.get_context("spawn").Process` 启动独立子进程执行复盘计算：
  - 子进程启动后首先调用 `os.nice(10)` 降低 CPU 抢占优先级，确保实盘行情接收与毫秒级状态机不受任何延迟影响；
  - 读取长期归档 K 线时按月分块流式加载（Chunked Reading），仅保留计算所需的列（`open_time`, `close`, `high`, `low`, `rsi_6`, `rvol_roll`, `market_regime`）并使用 `float32` 降低 $50\%$ 内存占用；
  - 子进程将生成的复盘报告与推荐参数写入磁盘与 SQLite 后立即退出（`sys.exit(0)`），由操作系统 100% 彻底回收子进程物理内存，保障主服务常驻内存始终稳定在 $< 250\text{ MB}$。

#### (2) 基于每日 K 线的四大核心诊断数学指标
对复盘周期内的每一笔 `SHADOW` 与 `LIVE` 订单，结合归档 K 线计算：
1. **最大不利偏离（MAE, Maximum Adverse Excursion）**：统计所有最终盈利订单在持仓期间曾触及的最大浮亏百分比分布（如 $90\%$ 分位数 $\text{MAE}_{90}$），用于量化评估 $-0.8\%$ 止损位是否合理。
2. **最大有利偏离（MFE）与平仓后 4 小时踏空率（Early Exit Regret Rate）**：
   - 对每一笔在 $+1.0\% \sim +1.6\%$ 止盈出局的订单，调取其平仓后 $4$ 小时内的 `5m` 归档 K 线最高价 $P_{\text{post\_4h\_high}}$。
   - 若 $\frac{P_{\text{post\_4h\_high}} - P_{\text{entry}}}{P_{\text{entry}}} \ge +0.025$（即卖完后继续暴涨超 $+2.5\%$），计为一次“洗盘早产踏空”。计算早产踏空率 $\eta_{\text{early\_exit}}$，直接用于校准【补丁 4】的 $\text{RVOL}$ 破量豁免阈值与回撤容忍度。
3. **假止损悔恨率（False Stop-Loss Regret Rate）与二次重入捕获率**：
   - 对每一笔触发 $-0.8\%$ 止损的订单，调取止损后 $2$ 小时内的归档 K 线最低价 $P_{\text{post\_2h\_low}}$ 与最高价 $P_{\text{post\_2h\_high}}$。
   - 统计有多少比例的止损单在止损位下方 $-0.3\% \sim -1.0\%$ 真正见底并反弹超 $+1.5\%$，以及【补丁 2】成功二次抄底捕获的比率 $\eta_{\text{reentry\_capture}}$。
4. **漏单机会成本率（Missed Wave Rate）**：
   - 扫描周期内所有 `15m` 级别涨幅 $\ge +2.0\%$ 的反弹波段，回查波段起点处 `signal_logs` 的四维得分与 `veto_reason`，定位哪个子项拦截了最多的潜在真底波段。

#### (3) 五级周期（`1W` / `1M` / `3M` / `6M` / `1Y`）定时任务与差异化分析深度
通过 `AsyncIOScheduler` 注册 5 个定时复盘任务，并将生成的 Markdown 报告保存至 `./shadow_data/review_reports/<horizon>/`：
- **`1W`（每周一 00:05，战术复盘）**：汇总过去 $7$ 天实盘 vs 影子胜率、盈亏比、毛收益与扣除摩擦后的净收益、平均追单磨损、MAE/MFE 分布、本周典型成功/失败交易 K 线复盘切片。
- **`1M`（每月 1 日 00:10，参数网格寻优）**：在独立子进程中加载过去 $30$ 天归档 K 线，对核心参数空间（`rsi_15m_threshold` $\in [25, 30]$，`rvol_min` $\in [1.4, 1.8]$，`sl_reentry_dip` $\in [-0.004, -0.008]$，`s2_b_rvol` $\in [2.0, 2.5]$）进行向量化网格回放，计算夏普比率最高的参数组合，生成 `recommended_params_json`。
- **`3M`（每季首日 00:20，市场状态机适应性复盘）**：按 `BULL_TREND`、`RANGE_BOUND`、`CAPITULATION_REBOUND_ALLOWED`、`CRASH_FREEFALL` 分类统计季度收益贡献，校准 `4H/1H` 宏观熔断阈值。
- **`6M`（每半年 00:30，因子衰减与持仓时长复盘）**：分析 $180$ 天内四维评分因子的 IC（Information Coefficient）衰减情况，检验 `120h` 超时平仓线是否需要调整。
- **`1Y`（每年 1 月 1 日 01:00，战略全量审计与蒙特卡洛压力测试）**：在独立子进程中基于全年归档 K 线分块计算年度夏普比率、卡尔玛比率（Calmar Ratio）、最大回撤（MDD），并进行 $1000$ 次随机滑点/极端行情蒙特卡洛模拟，输出年度模型迭代白皮书。

---

## 【Plan Mode 执行与 QA 验收要求】
1. 先输出 **Sprint 4 实现计划（Plan）**。
2. 编写完整代码后，创建单元测试 `tests/test_sprint4_archiver_and_reviewer.py`：
   - 生成 $7$ 天包含 `1m` 至 `4h` 周期的模拟 K 线与 $10$ 笔包含止盈、假止损、二次重入的影子交易记录；
   - 验证每日 K 线成功压缩归档至 `./shadow_data/klines_daily/`、可无损读取，且 WAL 截断与盘口切片过期清理正常生效；
   - 触发一次独立子进程模式的 `1W` 与 `1M` 复盘任务，验证主事件循环延迟 $< 50\text{ ms}$，且生成的 Markdown 报告完整包含 MAE、早产踏空率、假止损重入捕获率及 `recommended_params_json` 推荐参数结构。