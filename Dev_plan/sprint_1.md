# Sprint 1 Plan-Mode Prompt：日本 2C2G 服务器加固、4GB Swap、IP 证书与 SQLite WAL 单写队列数据库

> **使用方式**：在 Antigravity + 4 Argon 中开启 **Plan Mode**，挂载 `00_Master_Architecture_and_Plan_Guide.md` 后，将以下内容作为当前阶段任务指令输入。

---

## 【角色激活与任务目标】
你现在由 **项目总架构师**、**数据库工程师** 与 **QA/独立审计工程师** 联合驱动。请采用 **Plan Mode（先输出实现计划，确认后生成完整生产级代码）** 完成 `Crypto-bot` 的 **Sprint 1：服务器底层防崩溃基建、纯 IP HTTPS 加密通信、设备口令 AES-256-GCM 密钥管理与 SQLite (WAL + 异步单写队列) 高并发数据库层**。

---

## 【待生成文件清单与技术规范】

### 1. `crypto-bot-server/deploy/01_setup_swap_and_security.sh`
编写面向日本 Ubuntu (2vCPU + 2GB RAM + 40GB Disk) 服务器的幂等自动化加固脚本：
- **强制挂载 4GB Swap 防崩溃缓冲垫**：
  - 检查 `/swapfile` 是否存在，若不存在则使用 `fallocate -l 4G /swapfile` 创建，设置权限 `0600`，执行 `mkswap` 与 `swapon`，并幂等写入 `/etc/fstab`。
  - 配置内核参数 `vm.swappiness=20` 与 `vm.vfs_cache_pressure=50` 并写入 `/etc/sysctl.d/99-cryptobot.conf`，确保物理内存占用超过 $80\%$ 时温和启用 Swap，杜绝 OOM 崩溃。
- **防 AI 恶意扫描与系统漏洞加固**：
  - 执行 `apt-get update && apt-get dist-upgrade -y`，安装并启用 `unattended-upgrades`、`ufw`、`fail2ban`。
  - 配置 `UFW`：默认拒绝所有入站（`default deny incoming`），仅放行自定义 SSH 端口（默认变量 `SSH_PORT=58222`）与自定义 HTTPS 网关端口（`API_HTTPS_PORT=8443`）。
  - 配置 `Fail2ban`：针对 `sshd` 与 `nginx-botsearch` / `nginx-http-auth` 设置规则，$60\text{ s}$ 内出现 $3$ 次 `401/403/404` 或非法路径探测立即通过 `iptables` 封禁 IP $7$ 天（`bantime = 604800`）。
  - 创建受 Root 保护的无人值守主密钥目录 `/etc/cryptobot/`（权限 `0700`），若 `/etc/cryptobot/.master_key` 不存在，则使用 `openssl rand -hex 32` 生成随机主密钥种子并锁定权限为 `0400`。

### 2. `crypto-bot-server/deploy/02_generate_ip_tls_cert.sh`、`nginx_cryptobot.conf` 与 `cryptobot.service`
- **`02_generate_ip_tls_cert.sh`**：自动获取或接收服务器公网 IP，使用 `OpenSSL` 生成有效期 $3650$ 天的 **ECDSA P-256 (`prime256v1`)** 自签名证书，必须在 `subjectAltName` 中显式写入 `IP:${SERVER_PUBLIC_IP}`（同时设置 `basicConstraints = CA:FALSE` 与 `keyUsage = digitalSignature, keyEncipherment`），并提取证书公钥计算 SHA-256 Base64 指纹（`sha256/...`）输出至控制台，供 Android 客户端配置证书锁定。
- **`nginx_cryptobot.conf`**：监听 `8443 ssl http2`，仅开启 `TLSv1.3`，转发 `/api/` 与 `/ws/` 至本地 `127.0.0.1:8000`。
  - **【审计修复：WebSocket 长连接保活】**：在 `/ws/` 路由块中必须显式配置 `proxy_http_version 1.1;`、`proxy_set_header Upgrade $http_upgrade;`、`proxy_set_header Connection "upgrade";`、`proxy_read_timeout 3600s;`、`proxy_send_timeout 3600s;`，彻底防止 Nginx 默认 $60\text{ s}$ 断开空闲连接。
  - 拦截所有非授权 User-Agent 与常见扫描路径（如 `.env`, `.git`, `wp-admin` 直接返回 `444` 断开连接）。
- **`cryptobot.service`**：Systemd 守护进程配置，设置 `MemoryHigh=550M`、`MemoryMax=850M`、`Restart=always`、`RestartSec=3`，保障 2GB 内存服务器常驻进程永不越界且异常秒级自愈。

### 3. `crypto-bot-server/app/config.py` 与 `app/core/security.py`
- **无人值守 AES-256-GCM 加解密引擎**：
  - 读取 `/etc/cryptobot/.master_key`（开发环境下支持回退至本地 `.dev_master_key`）与设备绑定 Salt，通过 `PBKDF2HMAC`（SHA-256，迭代 $100,000$ 次）派生 $32\text{ Bytes}$ 密钥。
  - 实现 `encrypt_secret(plain_text: str) -> str` 与 `decrypt_secret(cipher_text: str) -> str`，用于加密存储用户在 App 端录入的币安 `API Key` 与 `API Secret`。重启服务器时自动在内存态解密恢复，磁盘永不落盘明文。
- **一次性设备激活口令（Device Activation Token）与硬件绑定**：
  - 首次启动若未绑定设备，在控制台与安全日志生成格式为 `ACT-XXXX-XXXX-XXXX` 的高熵激活口令。
  - 验证 Android 端上传的 `activation_token` 与 `device_hardware_id` 后，将硬件指纹持久化锁定至数据库，并签发经过 HMAC-SHA256 签名的长效 JWT 令牌。

### 4. `crypto-bot-server/app/core/database.py`
使用 `aiosqlite` 构建异步高并发 SQLite 数据库管理器（`DatabaseManager`）：
- **连接初始化钩子**：每次建立连接必须执行：
  ```sql
  PRAGMA journal_mode = WAL;
  PRAGMA synchronous = NORMAL;
  PRAGMA cache_size = -16000;
  PRAGMA temp_store = MEMORY;
  PRAGMA busy_timeout = 5000;
  PRAGMA foreign_keys = ON;
  ```
- **【审计修复：异步单写队列与 WAL 截断回收】**：
  - 在 `DatabaseManager` 内封装基于 `asyncio.Queue` 的单写协程工作器（`execute_write` / `execute_write_many`），使行情流、策略器与状态机的所有并发写操作串行化入队执行，彻底消除高波动行情下的 `database is locked` 异常；读操作（`fetch_one` / `fetch_all`）使用独立并发只读连接池。
  - 提供 `checkpoint_and_truncate_wal()` 方法，执行 `PRAGMA wal_checkpoint(TRUNCATE);`，供每日归档任务调用以防止 `.db-wal` 文件无限膨胀。
- **完整创建 8 张核心数据表与索引**：
  1. `system_config`：单行配置表（主键 `id=1`），含 `bot_live_enabled` (默认 `0`)、`total_budget` (`2000.0`)、`max_positions` (`2`)、`min_order_usdt` (`300.0`)、`max_order_usdt` (`700.0`)、`sl_pct` (`-0.008`)、`tp1_pct` (`0.010`)、`tp2_act_pct` (`0.015`)、`tp2_drawdown` (`0.005`)、`bnb_threshold_usdt` (`20.0`)、`bnb_topup_usdt` (`6.0`)、`last_bnb_buy_date`、止损重入锁状态字段（`last_sl_price`, `last_sl_time`）、加密后的 API 密钥及绑定设备指纹。
  2. `kline_cache`：**【审计修复：对齐全周期与宏观状态列】** 存储 `1m`, `5m`, `15m`, `1h`, `4h` 五个周期的 K 线（`interval`, `open_time`, `open`, `high`, `low`, `close`, `volume`, `taker_buy_volume`）及实时计算出的微观因子：`rsi_6`, `rvol_roll`, `taker_buy_ratio`, `vwap_15m`, `kalman_price`, `kalman_velocity`, `market_regime`。联合主键 `(interval, open_time)`。
  3. `signal_logs`：记录每次策略扫描的 `sig_id`, `timestamp`, `market_regime`, `score_total`, `score_macro`, `score_rsi`, `score_rvol`, `score_obi`, `rvol_roll`, `veto_reason`, `is_triggered`。
  4. `positions`：实盘（`LIVE`）与影子（`SHADOW`）统一持仓表，含 `pos_id`, `track`, `source` (`CBOT_AUTO` / `USER_MANUAL`), `is_managed`, `slot_index` (`1`/`2`/`NULL`), `entry_price`, `qty_gross`, `qty_net`, `usdt_cost`, `fsm_state` (`S0_PROTECT`/`S1_LOCKED`/`S2_TRAILING`/`CLOSED`), `s2_submode` (`S2_A`/`S2_B`/`S2_C`), `peak_price`, `last_new_high_ts`, `stop_loss_price`, `floor_lock_pct`, `dynamic_drawdown_pct`, `opened_at`, `timeout_at`, `closed_at`, `exit_price`, `exit_reason`, `realized_pnl_usdt`, `realized_pnl_pct`。
  5. `order_executions`：记录所有 `CBOT_` 订单与追单明细（含 `client_order_id`, `exchange_order_id`, `pos_id`, `side`, `price`, `executed_qty`, `commission`, `commission_asset`, `chase_count`, `wear_pct`, `status`, `created_at`）。
  6. `account_snapshots`：每小时记录一次实盘与影子的资产净值、可用 `USDT`、持仓 `SOL`、备用 `BNB` 及滚动胜率。
  7. `review_reports_meta`：存储 $1\text{W}/1\text{M}/3\text{M}/6\text{M}/1\text{Y}$ 复盘报告元数据、文件路径、核心指标摘要与推荐参数 JSON（`recommended_params_json`）及是否已应用标志（`is_applied`）。
  8. `audit_logs`：安全与异常事件日志表。

---

## 【Plan Mode 执行与 QA 验收要求】
1. 请先输出 **Sprint 1 实现计划（Plan）**，列出各文件的核心类与函数签名。
2. 生成完整代码与 `requirements.txt`（锁定版本号）。
3. 编写并运行单元测试 `tests/test_sprint1_db_and_security.py`：验证 SQLite WAL 模式开启状态、50 个协程并发写入 `DatabaseManager` 单写队列零锁冲突、`kline_cache` 含 `1m` 与 `market_regime` 字段读写、AES-256-GCM 密钥加解密一致性以及一次性设备激活口令绑定闭环。