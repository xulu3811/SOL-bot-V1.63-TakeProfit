# P5 报告C：原生保护、APTS与手续费执行组件

已实现 `execution/p5_protection.py` 的持久保护协调器和 `execution/p5_bnb.py` 的BNB执行/真实扣费资产模型。所有私有操作通过主执行器，必须先通过LIVE授权门。组件没有直接HTTP客户端，也不读取凭证。

保护创建按实时交易对状态、Spot权限、orderTypes和过滤器选择 STOP_LOSS 或 STOP_LOSS_LIMIT；没有必要原生保护能力时拒绝。初始止损复用P3的0.8%数学，价格/数量精度在协调器检查，并由主执行器在发单前再校验完整规则。只有带正确clientOrderId、symbol、exchange orderId及状态的交易所证据才能确认保护；HTTP成功、价格触达均不能冒充成交。部分成交和终态数量保持单调，事件ID去重，超时和断线持久为UNKNOWN并阻断新入场。

APTS更新拒绝降低保护价。只有额外机器人自有且可用数量足够，同时主执行器通过汇总卖出额度检查时，才允许先提交新保护、确认NEW后再撤旧；不能先撤除旧保护来释放资金。本轮没有实现或宣称交易所提供原子保护替换。大多数单仓全部资产被旧保护锁定时更新会拒绝并保留旧保护。趋势衰竭生成经Guardian授权的LIMIT退出请求；旧保护未被提前取消，无法通过库存/预留门时暂停退出并告警。退出提交前持久化责任状态，未知结果不得盲目重试。

LIVE OFF拒绝私有操作、保留已确认交易所订单，本地APTS无法继续动态管理。OFF期间需要新的明确授权才能查询/更改保护。订单被拒、替换失败、取消未知或网络中断不删除保护责任。STOP_LOSS存在滑点，STOP_LOSS_LIMIT可能触发而不成交。

BNB补充执行链路再次核对3/10/20规则、可用与分配资金、硬锁和唯一意图，要求Guardian完整请求哈希绑定，再进入主执行器。UNKNOWN阻断后续补充，失败次数有上限。真实成交费用按commissionAsset扣除对应机器人资产，而非把BNB费误扣USDT；币种估值缺失明确拒绝计算成本。记账依赖唯一交易所trade ID，限制累计成交数量及成交价不能越过原买单。生产统一账本的最终事务由主执行器协调，组件自身的独立记录不能替代主账本核验。

协议查阅：[Binance官方REST接口](https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md)、[官方Trailing Stop FAQ](https://github.com/binance/binance-spot-api-docs/blob/master/faqs/trailing-stop-faq.md)。官方文档描述的OTOCO在working order全部成交后才激活pending保护，因此不能把它当作部分成交立即受保护的保证；OCO/OTOCO等类型仍须实时能力与账户限制验证。trailingDelta使用BIPS并检查SELL侧TRAILING_DELTA区间，不假定SUIUSDT支持追踪。

验证范围：本地Mock测试覆盖初始保护确认、拒绝、超时、部分成交、重复/乱序事件、重启UNKNOWN、OFF不私有调用、单调保护、先新后旧、替换不足库存、原生追踪过滤器、趋势退出、BNB唯一意图/UNKNOWN/Guardian门及实际扣费币种。未连接Testnet、未请求生产账户、未发送真实订单；实时保护可执行性、连续保护替换和实际手续费尚未由真实授权环境验收。
