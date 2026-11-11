# P5 报告B：订单状态与一致性

执行核心为 `execution/p5_engine.py` 和 `p5_store.py`。独立执行库不覆盖P3/P4数据库。请求绑定账户、完整意图哈希、稳定clientOrderId及资金预留；CREATED、RISK_APPROVED、SUBMITTING在网络提交前持久化。超时/断线进入UNKNOWN，重复提交只返回已有记录，不重发。恢复首先查询原clientOrderId，并分页获取实际成交。

REST与executionReport统一处理，核对symbol、side、exchangeOrderId，累计成交数量和事件时间不允许回退，终态不被旧NEW重开。终态先到而成交明细缺失时，仍保留冻结；恢复包含这些终态订单，补齐tradeId、费用与数量后再结算。未知订单只查清，不自动复投。撤单请求单独持久化，真实终态且所有成交详情记账后才释放预留。

P5执行库与P4资金库采用持久outbox和资金事件唯一ID连接。先保存订单事件/成交及outbox，再经认证Guardian IPC结算，再标记交付。任意阶段崩溃可重放，资金端幂等去重。冲突成交、迟到成交超过已冻结数量或费用归属不明会留在待结算，暂停新入场，不产生负冻结或伪造成交。

IPC为本机AF_UNIX，双方校验内核SO_PEERCRED，限定方法与64KiB消息。独立execution UID与原策略用户分离；Guardian保持PrivateNetwork，执行服务也默认网络隔离。批准由Guardian服务内密钥签名，绑定完整交易参数、有效期和一次性消费审计；最终发送前重新验证当前风险，篡改和重复消费拒绝。P6发布门在加载生产凭证前继续拒绝，不能由数据库LIVE_ON或调用者布尔值解除。

独立QA已复现并修复终态缺明细恢复遗漏、方向错配、迟到成交负预留等缺陷。Mock证明本地状态与事务行为，不是交易所真实执行验收。
