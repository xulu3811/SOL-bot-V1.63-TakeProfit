# 报告E：独立QA、回归、迁移与恢复

## 结果及范围

P3工程范围的验证证据见本报告和独立报告 p3-independent-qa.md。总体验收为 PARTIAL：指定 Cursor/Grok 外部审计没有执行，不能由Codex独立代理冒充通过；实证参数和历史盘口覆盖也仍有限。

最终运行版 `/opt/sol-bot/releases/p3-20261111T045723Z` 在服务器Python3.14.4执行198单元+20集成，共218项全部通过，包括原P1/P2 118项。独立代理随后增加一项真实Scheduler分钟边界回归；最终本地Python3.11全量为199单元+20集成，共219项全部通过。32项自编独立QA属于这219项，不重复累加；9项回放测试也属于该集合。部署源码与本地运行源码哈希一致，测试文件仅多出该独立回归。

## 实际迁移

2026-11-11 12:54 CST，迁移前账户现金2000、预留0、仓位0、挂单0、硬锁0；实际读取状态而非假定空账本。先在线SQLite备份及隔离还原核验，再停止 sol-shadow / sui-strategy 两项worker，生成最终一致性备份并核验。事务增补P3表/字段、更新owner为SHADOW_P3，逐字段确认原账户/订单/仓位无变化，再切换服务入口。

备份目录：`/var/lib/sol-bot/backups/p3-migration-20261111T045417Z`。online.sqlite、final.sqlite、隔离恢复副本和原unit均保留。迁移失败分支已实现：停止相关worker、用final.sqlite还原完整preimage、恢复旧unit并保持worker停止；不得直接用旧P2程序打开P3账本。迁移中途异常的事务回滚另有独立故障注入测试；生产迁移成功，未故意破坏生产库演练失败。

旧实例每次写事务重新检查owner，新P2实例同样拒绝迁移后账本。已加载的旧release不会自动得到新检查，因此先停旧worker的顺序不可省略。

## 重启及恢复

最终验收目录：`/var/lib/sol-bot/backups/p3-acceptance-20261111T045812Z`。对SOL账本、两策略库、两数学审计库、LIVE库共6库使用SQLite备份API，恢复到隔离文件，检查integrity与完整SQL逻辑哈希相等。恢复后只能使用P3引擎打开P3账本。

实际重启两策略worker，检查原信号ID及完整payload保留、无重复ID、现金/预留/仓位/订单/成交/硬锁不变，LIVE OFF及版本不变。四项市场/微观服务PID均未改变：SOL市场261317、SUI市场272669、SOL微观283155、SUI微观283156。

生产实际无订单，不把空账本重启称为验证了真实部分成交。另在隔离SQLite中执行合成部分买入、备份、还原、同深度重放，成交数仍1；持久硬锁重开后仍1，撤入场余单且保留仓位。初始止损/APTS更新/部分退出/缺数据保护/两标的隔离由单元和独立测试覆盖。

## 验收发现及关闭

- 独立QA发现部分BUY余单误挡主动SELL，修正检查仅针对SELL，原子撤BUY余单测试通过。
- 持久ready标志老化、源哈希溯源、旧对象owner绕过均已修复并回归。
- 首次部署在新分钟输出P2浮点评分时触发严格状态序列化并重启。只规范化旧诊断字段，金额/模型仍拒float；本地及真实Scheduler独立回归通过，最终版本跨多个分钟无重启。
- 新root备份目录导致原定时备份清理find权限失败。目录组改为solbot、保留750，脚本同步修正；备份和health最终均Result=success/Exit=0。
- 真实回放的稀疏6小时缓存跳跃和特征消费者年龄边界在独立审查后补强。旧回放已明确中止并标为被替代，最终仅引用新版完整结果。

## 资源及安全状态

最终35秒采样期间P3两worker各约21.6MiB内存，单核CPU约2.52%/2.11%，四行情服务保持active、全部NRestarts=0。内存可用约1174MiB、Swap使用0、磁盘可用约38GiB。两数学审计库各6条/56KiB；空账户保护审计0条、未随深度增长。采样是短期观察，不是长期SLA或持续持仓压力测试。

LIVE保持OFF、修订号3；SUI真实执行器未构造、R1未获实盘授权。项目代码/测试只允许固定公开行情路径，没有私有账户客户端、真实Secret或真实下单调用。本任务私有API调用0、真实订单0；未调用私有账户接口来证明此项，也未声称监控了项目外的所有主机流量。

主要证据：data/p3-final-tests-v2.txt、data/p3-final-local-unit.txt、data/p3-final-local-integration.txt、data/p3-migration-evidence.txt、data/p3-restart-restore-final.txt、data/p3-backup-fix.txt、data/p3-final-observation.txt、data/p3-final-source-manifest.json。
