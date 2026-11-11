# Role & Context
你是由 **10. DevOps/SRE 工程师**、**11. 网络安全工程师** 与 **1. 项目总架构师** 组成的联合基础设施研发引擎。
请根据以下严格的生产级规范，为 `SOL-bot`（`SOL/USDT` 现货实盘与影子双擎交易系统）生成 **Phase 0：日本服务器安全加固、4GB Swap 挂载与加密通信基线** 的全部可执行脚本、Caddy 配置文件与 Python 密钥安全模块代码。

---

## 1. 硬件环境与内存防崩溃硬性约束
* **服务器规格**：日本区域云服务器，Ubuntu 22.04 / 24.04 LTS，$2\text{ vCPU} + 2\text{ GB RAM} + 40\text{ GB Disk}$。
* **首步强制优化**：必须首先挂载 **$4\text{ GB}$ Swap 虚拟内存**，并设置内核参数 `vm.swappiness=20`、`vm.vfs_cache_pressure=50`，作为极端行情下突发峰值防崩溃缓冲垫。
* **2GB 物理内存严格配额**：
  * 系统内核 + Caddy + Fail2ban：$\le 220\text{ MB}$
  * Python 异步单进程交易核心 (`solbot-core`)：通过 `systemd` 的 `MemoryHigh=700M` 与 `MemoryMax=850M` 严格限制，确保常驻内存控制在 $800\text{ MB}$ 以内。
  * SQLite 3 (WAL 模式)：限制页缓存 $64\text{ MB}$ (`PRAGMA cache_size = -64000;`)。

---

## 2. 待生成交付物清单（请在项目中创建以下文件）

### 2.1 `deploy/phase0_server_hardening.sh`
编写完整的 Bash 自动化加固脚本，需包含幂等性检查并依次执行：
1. **Swap 挂载与内核调优**：创建 `/swapfile` ($4\text{ GB}$)，写入 `/etc/fstab`，配置 `/etc/sysctl.d/99-solbot-tuning.conf`（开启 TCP SYN Cookies、防 IP 欺骗、设置 `vm.swappiness=20`）。
2. **防 AI 恶意扫描与防火墙加固**：
   * 禁用无用服务（`snapd`, `cups`, `avahi-daemon`）以释放物理内存。
   * 配置 `ufw`：默认拒绝所有入站，仅放行自定义 SSH 端口（如 `51822/tcp`）、`80/tcp`（仅用于 Let's Encrypt 证书验证）与 `443/tcp`（HTTPS 加密通信）。
   * 配置 `fail2ban`：针对 SSH 与 Caddy 访问日志，在 $600\text{ s}$ 内出现 $3$ 次非法扫描或鉴权失败即封禁 IP $86400\text{ s}$。
3. **Systemd 守护进程配置 (`/etc/systemd/system/solbot.service`)**：
   * 配置 `Restart=always`、`RestartSec=3`、`OOMScoreAdjust=-500`，确保 $24 \times 7$ 全年无休运行与故障自愈。

### 2.2 `deploy/Caddyfile`
编写轻量级 Caddy 反向代理配置，实现 **廉价/免费域名 + Let's Encrypt 标准 TLS 1.3 证书**：
1. 自动申请并续期 Let's Encrypt HTTPS 证书，强制开启 TLS 1.3 与 HSTS。
2. **反 AI 漏洞扫描路由白名单**：仅允许 `/api/v1/*` 与 `/ws/v1/*` 转发至本地 Python 后端 `127.0.0.1:8443`；其余任何探测路径（如 `/.env`, `/wp-admin`, `/phpmyadmin` 等）直接返回 `444` 或立即断开连接，并记录日志供 Fail2ban 封禁。

### 2.3 `backend/security/crypto_vault.py`
编写 Python 3.11+ 安全凭证托管与设备激活绑定模块（严禁明文落盘 API Key）：
1. **Android 终端设备口令绑定**：
   * 提供设备激活接口：校验客户端提交的 `device_passphrase`（设备激活口令）、`android_device_id` 与时间戳 HMAC-SHA256 签名。
   * 验证通过后签发绑定该设备指纹的 JWT / Session Token。
2. **Binance API Key & Secret 零明文加密托管**：
   * 用户在 Android App 输入 Binance API Key 与 Secret 后，通过 HTTPS POST 提交至后端。
   * 后端读取服务器硬件唯一标识 `/etc/machine-id` 并结合用户的 `device_passphrase`，使用 $\text{Argon2id}$ 算法（参数：$t=3, m=65536\text{ KB}, p=2$）派生出 $256\text{ bit}$ 根密钥 $K_{\text{master}}$：
     $$K_{\text{master}} = \text{Argon2id}(\text{passphrase} \parallel \text{machine\_id}, \text{salt})$$
   * 使用 **AES-256-GCM** 加密 `api_key` 与 `api_secret`，仅将密文、Nonce 与 Tag 写入本地安全文件 `data/vault.enc`（权限 `0600`）。
   * 仅当用户在 App 端打开 **Crypto-bot LIVE 实盘开关** 时，解密注入内存中的受保护单例对象供实盘引擎调用；当关闭 LIVE 开关时，主动覆写清除内存中的明文引用于或锁定实盘下单通道。

---

## 3. 代码质量与验收标准
* 所有 Python 代码需具备完整的 Type Hints、异步非阻塞支持及异常捕获。
* 严禁在任何日志（Log）中打印 `api_key`、`api_secret` 或 `device_passphrase` 的明文。
* 请输出上述 3 个核心文件的完整、可直接运行的代码实现，禁止使用省略号（`...`）跳过关键逻辑。