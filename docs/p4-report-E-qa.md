# P4 报告E：测试、独立QA与迁移

最终服务器版本 `/opt/sol-bot/releases/p4-20261111T054542Z`：235项单元 + 20项集成 = **255项全部通过**。包括原P1/P2/P3的219项和新增P4的36项。P4中28项由独立Codex QA编写，8项由主开发编写；包含在总数内，不重复相加。本地Python3.12.14仅执行P4的36项，均通过；全量回归使用服务器既有Python3.14环境。

`data/p4-server/data/p4-test-evidence.json` 保存36项P4测试的方法源码（输入与预期断言）、setup、实际结果、版本和源文件哈希。完整全量输出为同目录 `p4-unit.txt` 与 `p4-integration.txt`。独立报告见 `p4-independent-qa.md`。

发现并关闭：预留SQL列数错误；撤销托管后旧退出批准复用；余额对账消除UNKNOWN订单；未知余单收到部分成交误变PARTIAL。最后一项由独立只读复核发现，主开发补充窄回归。此外，首次服务器打包漏P3回放脚本导致2个导入错误，未越过测试门禁；第二次启动尝试写只读旧LIVE库失败，改为只读状态同步。最终版本重新全量回归并稳定运行，保留失败历史，不宣称从未重启。

初始迁移 `/var/lib/sol-bot/backups/p4-migration-20261111T054223Z`：先在线备份旧LIVE和SOL-shadow，隔离恢复并对比逻辑哈希，再创建独立P4库并导入旧LIVE状态。未迁移原P3表；旧写程序通过schema检查拒绝P4文件。修订部署前另备份现有P4库于 `p4-migration-20261111T054542Z`，资金和高水位保留。

最终验收再对P4做一致性备份与隔离恢复、服务重启，比较资金/预留/仓位/锁状态及旧LIVE行不变，并逐文件验证上传清单。完整路径与结果见 `data/p4-server/data/p4-acceptance.json`。新服务正常active，最终一次启动后的NRestarts=0，内存约13MiB；这是短期工程观测，不是24×7 SLA证明。

未进行外部安全扫描、攻击模拟或向外部AI上传审计数据。Cursor + Grok 4.7 Extra High指定审计未执行，独立Codex QA不替代该要求。因此总体验收保留 **PARTIAL**。
