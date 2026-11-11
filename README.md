# SOL-bot

Binance SOL/USDT 现货研究交易系统：Python 后端（行情、T1/R1 策略、影子账本、风控、本机应用接口）和 Android 监控端。

生产 LIVE 默认关闭。仓库不含服务器口令、交易所密钥、Firebase Admin 私钥，也不含本地一年 K 线库。

## 目录

- `backend/` Python 包 `solbot`，入口见 `backend/pyproject.toml`
- `android/` Kotlin 客户端
- `tests/` 单元与集成测试
- `scripts/` 部署与验收脚本
- `deploy/` systemd / Nginx 单元
- `docs/` 分阶段合同与验收记录
- `FCM-Serv/` 本地密钥目录（不入库；Android 客户端配置在 `android/app/google-services.json`）

## 本地密钥

把服务器账号、Binance 密钥和 Firebase Admin JSON 放在 `FCM-Serv/`，文件名保持现有约定即可。一年 K 线库放在 `data/solusdt_1y.sqlite`。
