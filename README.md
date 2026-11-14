# SOL-bot v1.0（可执行SOL-bot v1.0）

Binance SOL/USDT 现货自动化研究、实盘与影子模拟交易系统：包含 Python 多引擎后端（行情与微观盘口采集、`T1 / T2 / R2 / R1` 策略引擎、`Shadow` 沙盒账本、`Guardian` 独立风控、`APTS v2` 阶梯止盈止损、本机认证 App API 与 FCM HTTP v1 推送）以及 Android 客户端（`v1.40`）。

> 生产 `LIVE` 开关默认处于安全关闭状态（由 App 端经 Ed25519 签名显式开启/关闭）。仓库不含服务器口令、交易所 API 密钥、Firebase Admin 私钥及本地一年 K 线数据库。

## 核心能力

- **多引擎买入策略**
  - `T1` 顺势回调引擎（含多周期极端超卖与放量衰竭反弹模式，固定 `-1.0%` 初始止损）
  - `T2` 波段回踩引擎（`4h EMA` 方向过滤 + `1h` 回踩 + `15m` 收复 + `5m RSI` 拐头，动态止损 `0.8%–2.0%`）
  - `R2` 超卖反弹状态机（`IDLE → WATCHING → ARMED → TRIGGERED` 四阶段防飞刀结构，结构低点止损）
  - `R1` 区间均值反转引擎（震荡环境 Shadow 影子研究评估）
- **APTS v2 自适应阶梯止盈止损**
  - `S0` 初始硬止损：交易所原生 `STOP_LOSS_LIMIT` 挂单保护（策略开仓 `-1.0%` / 动态止损；手动持仓托管 `-0.8%`）
  - `S1` 移动保本：浮盈达 `+1.0%` 自动抬升止损至成本上方保本位（覆盖双边手续费与滑点缓冲）
  - `S2` 动态追踪止盈：浮盈达 `+1.5%+` 随最高买一价动态上移回撤保护距离（强势突破 `0.8%–1.2%` / 常规 `0.5%` / 转弱 `0.4%`）
  - `S3` 趋势衰竭平仓：脱离 `S0` 后连续 2-tick 确认反转时主动改挂限价平仓；已取消 `12h / 24h` 限时退场，未触发止损或趋势衰竭前持续持仓
- **独立影子沙盒 & 风控门禁**
  - `P3` Shadow 沙盒使用真实盘口 Bid/Ask 与 BNB 折扣费率 `0.075%` 进行 1:1 独立模拟记账
  - `P4` Guardian 监控日内最大回撤，触及 `5.0%` 自动 `HARD_LOCKED` 熔断；单笔动态算仓 `$300–$700 USDT`，实盘上限 2 个策略仓位
- **Android 监控与控制端（`v1.40`）**
  - Jetpack Compose Material 3 界面，Ed25519 设备配对与请求签名、WebSocket 实时状态流、`T1 / T2 / R2 / R1` 4 引擎看板、实盘持仓/手动托管控制及 Google FCM HTTP v1 告警推送

## 目录结构

- `backend/` Python 核心包 `solbot`，依赖与入口见 [`backend/pyproject.toml`](backend/pyproject.toml)
- `android/` Kotlin + Jetpack Compose Android 客户端
- `tests/` 单元与集成测试套件（`464 passed`）
- `scripts/` 部署、回放与验收脚本
- `deploy/` systemd 与 Nginx HTTPS 配置
- `docs/` 策略说明、分阶段合同与审计记录
- `FCM-Serv/` 本地密钥目录（不入库；Android 客户端配置位于 `android/app/google-services.json`）

## 本地密钥与环境准备

把服务器账号、Binance 密钥和 Firebase Admin JSON 放在 `FCM-Serv/` 下（已被 `.gitignore` 忽略）。一年 SOL K 线库放在 `data/solusdt_1y.sqlite`。
