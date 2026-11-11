# P5 报告E：最终部署、测试与恢复

结论 **PARTIAL**。部署版本 `/opt/sol-bot/releases/p5-20261111T065321Z`，链接 `/opt/sol-bot/p5`。Guardian IPC和P5执行OFF服务均active；原P3六个服务及P4 Guardian PID保持不变。未开启LIVE、未加载Binance交易Secret、未连接私有API或发送真实订单。

最终服务器 **332项单元 + 20项集成 = 352项全部通过**。包括原P1–P4的255项和新增P5的97项。P5组成：适配器19、账户握手7、保护/BNB23、自动保护系统6、主开发组合6、独立QA36。独立QA已包含在总数中，不重复累计。独立报告为 `p5-independent-qa.md`。本地环境执行了对应新增测试；全量回归使用服务器现有Python3.14环境，未安装新依赖。

完整测试输出、每个P5用例的输入和预期断言源码、实际结果与文件哈希位于 `data/p5-server/data/` 中的 `p5-unit.txt`、`p5-integration.txt`、`p5-test-evidence.json`。首轮351项通过后，组合检查增加过滤器返回拒因必须阻断的回归；最终版本重新全量352项通过。不能把两轮数量相加。

关键修复包括：终态先到缺成交详情时仍查询恢复；方向/交易所订单ID校验；迟到成交不能形成负预留；重复tradeId/异常文本处理；保护许可消费时重验人工余额冲突；已确认锁定订单与free余额不重复扣减。已知锁定的豁免仅限匹配clientOrderId、交易所订单与可信对账快照，未匹配冻结仍保留。

部署前对P4 Guardian、旧LIVE控制、SOL-shadow用SQLite backup做一致副本和隔离恢复，核对integrity与完整SQL逻辑哈希；既有执行库在修订发布前也备份。P3/P4表结构未迁移，新执行库位于 `/var/lib/sol-bot-execution/orders.sqlite`。备份证据 `p5-backup.json` 包含按顺序输出的备份记录；最终独立执行库还原证据在 `p5-acceptance.json`。

最终真实服务器验收：执行服务重启，Guardian资金快照完全一致；执行库备份隔离恢复哈希相同；175个运行包文件逐一哈希匹配；SO_PEERCRED正确execution UID请求成功，原solbot策略UID请求拒绝。execution用户为系统nologin账户，服务用mount namespace屏蔽Guardian数据库/密钥路径；使用运行代码所需的solbot只读组访问，不能把组身份当作IPC调用授权。两个新增服务仍PrivateNetwork及AF_UNIX限制，没有放宽原P4网络隔离。

约10.28秒的重启验收采样中，执行服务MemoryCurrent=11,173,888字节，IPC服务=12,197,888字节；启动后累计CPU时间分别约0.120/0.163秒，不能把跨重启累计计数当作长期CPU利用率。执行库WAL采样前后均0字节，NRestarts均0。这些是短期OFF观测，不证明真实高频成交负载、长期WAL上限或24×7 SLA。

当前执行库恢复测试覆盖本次备份；新增独立状态目录不假定已被旧P4定时备份脚本覆盖，正式实盘上线前必须把该目录纳入受控备份与容量验收。当前空库生产重启不冒称验证了真实部分成交；崩溃outbox/重复成交/UNKNOWN/保护失败采用隔离Mock证据。

未执行Testnet，未执行真实账户授权验收，Cursor/Grok指定外部审计未完成，P0历史SSH凭证风险未独立关闭。保持PARTIAL，不构成实盘上线许可。
