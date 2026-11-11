# P4 报告A：Guardian交付

任务 SOL-P4-GUARDIAN，2026-11-11。结论 **PARTIAL**：本阶段代码、部署及工程验收已完成；指定 Cursor/Grok 外部审计缺失，不构成实盘许可。

服务器运行版本 `/opt/sol-bot/releases/p4-20261111T054542Z`，链接 `/opt/sol-bot/p4`。新增 `solbot-guardian.service`。原 P3 `/opt/sol-bot/releases/p3-20261111T045723Z`、六个行情/策略服务继续运行。最终验收时原六服务 PID 未改变。

核心入口：`accounts/allocation_ledger.py`、`risk/guardian.py`、`risk/permission_engine.py`、`risk/drawdown_engine.py`、`risk/exposure_manager.py`、`risk/recovery_guard.py`、`risk/bnb_reserve.py`。部署入口 `solbot.p4_runtime`。风险规则固定为 `guardian-p4-1`，未提供任意参数更新接口。

`Guardian.evaluate(TradeIntent, reserve=True)` 在单个 BEGIN IMMEDIATE 事务内检查完整意图、身份、策略版本、时效、资金、净值、占位和风险预算，产生内部资金规划决策。内部 ALLOW 的 `live_authorized` 恒为 false。`authorize_live` 再检查 LIVE 状态并因 P5 缺失拒绝；`LiveExecutor.execute_intent` 接入此门禁，数据库异常返回暂停。旧不完整请求接口继续拒绝，不能绕开完整意图授权。

批准绑定全部请求字段的哈希、策略与风险版本、有效期和唯一意图；相同意图不得改变内容，重复请求不重复冻结。同信号换请求 ID 不得重复预留。资金与审计同事务提交；审计表触发器禁止 UPDATE/DELETE。

P4 使用独立 `/var/lib/sol-bot/database/sui-guardian-p4.sqlite`，并以只读方式读取管理员拥有的旧 LIVE 控制库。旧库硬锁会传入 Guardian；旧库出现非 OFF 状态使 Guardian 暂停。Guardian 自身硬锁直接阻断其授权边界。P4 不通过修改旧库写权限实现同步；旧 LIVE ON 路径仍返回 DENY_NOT_READY。

运行源码哈希与上传清单逐文件一致，证据见 `data/p4-server/data/p4-acceptance.json`。最终交付包包含本地源码、测试、报告和脱敏证据；交付后的辅助验收脚本与报告可能比运行包多，不改变运行源码。

索引：[B 风险数学](p4-report-B-drawdown.md)、[C 资产隔离](p4-report-C-accounts.md)、[D LIVE与恢复](p4-report-D-recovery.md)、[E QA与迁移](p4-report-E-qa.md)、[F P5阻断](p4-report-F-readiness.md)。
