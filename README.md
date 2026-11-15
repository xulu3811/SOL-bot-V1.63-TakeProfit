# SOL-bot v2.0

Binance SOL/USDT 现货自动化研究、实盘与影子模拟交易系统：包含 Python 多引擎后端（行情与微观盘口采集、`T1 / T2 / R2 / R1` 策略引擎、`Shadow` 沙盒账本、`Guardian` 独立风控、`APTS v2` 自适应阶梯止盈止损、本机认证 App API 与 FCM HTTP v1 推送）以及 Android Jetpack Compose 客户端（`v1.46`）。

> 生产 `LIVE` 开关默认处于安全关闭状态（由 App 端经 Ed25519 签名显式开启/关闭）。仓库不包含服务器口令、交易所 API 密钥、Firebase Admin 私钥、签名 Keystore、打包产物及本地 SQLite 行情/账本数据库。

## v2.0 核心能力

- **实盘 / 模拟盘同步交易配置**
  - 新增 `GET / POST /api/v1/app/trading/config` 与共享配置同步机制（`SOLBOT_TRADING_CONFIG_PATH`），并在 `/live/status`、`/live/risk`、`/shadow/status` 及 WebSocket `/api/v1/app/events` 实时推送
  - Android 实盘页「**配置**」与 Shadow 页「**配置同步**」支持切换固定金额或区间模式（默认 `$500–$1000`，支持 `10–50000 USDT`）及最大同时持仓订单数（`1–10` 槽），实时同步生效于 Trading-bot 实盘与 Shadow-bot（`P3/T` 与 `R2`）
- **多引擎买入策略**
  - `T1` 顺势回调引擎（含 `UPTREND` 门控与放量衰竭反弹识别，固定 `-1.0%` 初始止损）
  - `T2` 波段回踩引擎（`4h EMA` 方向过滤 + `1h` 回踩 + `15m` 收复 + `5m RSI` 拐头，动态止损 `0.8%–2.0%`）
  - `R2` 超卖反弹状态机（`IDLE → WATCHING → ARMED → TRIGGERED` 四阶段防飞刀结构，结构低点止损）
  - `R1` 区间均值反转引擎（震荡环境 Shadow 影子研究评估）
- **APTS v2 自适应阶梯止盈止损**
  - `S0` 初始硬止损：交易所原生 `STOP_LOSS_LIMIT` 挂单保护（策略开仓 `-1.0%` / 动态止损；手动持仓托管 `-0.8%`；浮盈达 `+0.6%` 或连续反转时收紧 50%）
  - `S1` 移动保本：浮盈达 `+0.8%` 自动抬升止损至成本上方保本位（覆盖双边手续费与滑点缓冲）
  - `S2` 动态追踪止盈：浮盈达 `+1.5%+` 随最高买一价动态上移回撤保护；支持主力资金持续介入（`STRONG_BREAKOUT`）与 `ATR` 弹性回撤通道，高浮盈（`+3.0%+`）转弱自动收紧保护
  - `S3` 趋势衰竭平仓：脱离 `S0` 后连续确认反转时主动改挂限价平仓；支持人工手控现货持仓自动对账入场均价、净持仓校验与防尘埃（`< 5 USDT`）误触平仓
- **独立影子沙盒 & 风控门禁**
  - `P3` / `R2` Shadow 沙盒使用真实盘口 Bid/Ask 与 BNB 折扣费率 `0.075%` 进行 1:1 独立模拟记账，并在调大开仓额时自动按配置同步虚拟资金基准
  - `Guardian` 监控机器人归因日内最大回撤（排除人工操作干扰），触及 `5.0%` 自动 `HARD_LOCKED` 熔断
- **Android 监控与控制端（`v1.46`）**
  - Jetpack Compose Material 3 界面，Ed25519 设备配对与请求签名、WebSocket 实时状态流、`T1 / T2 / R2 / R1` 看板、实盘持仓/手动托管与交易配置控制，以及 Google FCM HTTP v1 告警推送

## 目录结构

- `backend/` Python 核心包 `solbot`，依赖与入口见 [`backend/pyproject.toml`](backend/pyproject.toml)
- `android/` Kotlin + Jetpack Compose Android 客户端（当前版本 `1.46`）
- `tests/` Python 单元与集成测试套件
- `deploy/` 生产环境 systemd 服务与 Nginx HTTPS 反代配置
- `scripts/` 历史 K 线回测与样本构造脚本
- `docs/` 策略说明、运行维护与开发/回测准则
- `FCM-Serv/` 本地密钥目录（不入库）
- `data/` 本地 SQLite 行情数据目录（不入库）

## 验证与构建

### 后端测试

```powershell
$env:PYTHONPATH = "$PWD\backend\src;$PWD"
pytest -q
```

### Android 单元测试与 Release APK 构建

```powershell
cd android
.\gradlew.bat testDebugUnitTest assembleRelease --no-daemon
```
