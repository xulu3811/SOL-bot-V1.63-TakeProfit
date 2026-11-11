# P3恢复说明

实际运行链接 `/opt/sol-bot/p3` 指向 `releases/p3-20261111T045723Z`。运行数据库位于 `/var/lib/sol-bot/database`，SOL账本的 execution_owner.kind 为 SHADOW_P3。**禁止用P2程序直接打开已迁移账本。**

## 恢复P3备份

1. 记录余额、预留、仓位、挂单、保护、硬锁、信号检查点、LIVE状态及运行版本。
2. 只停止 sol-shadow.service / sui-strategy.service；不得停止市场/微观服务。保留故障现场数据库，使用SQLite备份API保存一致性副本，不直接复制活动WAL主文件。
3. 选择已验证的P3备份；在隔离路径通过SQLite backup还原、integrity_check、完整schema/rows逻辑哈希核对，并用同版本P3执行器打开。恢复硬锁、保护和成交去重元数据，不手动重置现金或状态。
4. 将验证过的P3库通过SQLite backup写回目标库。确保所有相关连接已停止，检查文件归属solbot及目录权限。不得手工删除未核实的WAL来“修复”数据。
5. 数学审计、策略库与账本不是跨库原子快照；恢复时核对每笔信号/订单/成交，重复信号由持久幂等键拒绝。若无法确认一致性，保持新入场关闭，不自行补造订单。
6. 使用P3版本重启策略worker；检查已有保护先运行、LIVE仍OFF、无重复成交、硬锁未解除。市场服务的原PID应保持。

实际最终隔离恢复证据位于 `/var/lib/sol-bot/backups/p3-acceptance-20261111T045812Z`，六库完整内容哈希已匹配。隔离合成部分成交备份重放验证去重，不代表真实交易所订单恢复。

## 仅迁移当时失败的P2回退

`scripts/p3-migrate.py` 异常分支停止策略worker，用 `/var/lib/sol-bot/backups/p3-migration-20261111T045417Z/final.sqlite` 恢复完整P2 preimage，并恢复保存的旧unit，保持worker停止等待排查。该回退只适用于迁移尚未接受后续P3交易的失败窗口；不能在P3运行产生历史后用旧preimage抹掉新历史。

P2实验执行器曾采用不同的强平政策，不能把恢复旧程序视为获得按旧政策交易的授权。生产继续使用P3“禁止新入场、保留保护”的5%锁政策。

## 定时备份与权限

原 sol-bot-backup.timer 自动遍历数据库目录中的SQLite，已包含两数学审计库及微观库。新备份归档目录必须允许solbot组遍历（root:solbot、750），否则原find清理会报告权限失败。已修复本次归档目录及创建脚本，最终backup/health service均成功。归档及原始数据只在本机/授权服务器保存，不上传外部审计服务。
