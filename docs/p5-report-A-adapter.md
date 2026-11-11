# P5 报告A：真实账户协议适配

已实现 `exchange/`：HMAC REST签名、按账户/标的/操作范围授权、有效时间、时间同步、限流退避、私有读取、订单提交/撤销/订单列表、用户数据流协议、交易过滤器、真实手续费资产估值和延迟凭证加载。实现只使用标准库；未来WebSocket连接路径复用项目已有websockets依赖。

官方文档于2026-11-11读取： [REST API](https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/rest-api.md)、[WebSocket API](https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/web-socket-api.md)、[交易过滤器](https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/filters.md)、[用户数据事件](https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/user-data-stream.md)。当前用户流采用 `userDataStream.subscribe.signature`，未使用旧listenKey路径。执行回报保留累计量、单笔成交ID及扣费资产；账户推送视为部分快照，不能凭单一余额变化判断资产归属。

私有读取：account、openOrders、order、allOrders、myTrades、account/commission。交易：order创建/撤销及OCO/OTOCO请求包装。所有请求在加载凭证前检查会话；每次签名后再次检查。写入超时、服务端5xx及-1006/-1007视为不确定，绝不自动重发。429/418按Retry-After停止继续调用。-1021使时间校验失效，要求重新取得公开时间样本。

凭证通过 `SystemdCredentialLoader` 从未来服务的 `/run/credentials/` 读取，要求正常文件、受限权限和有效结构，不提供仓库/环境变量明文回退；应通过 `LoadCredentialEncrypted` 由管理员在后续授权阶段提供。本轮没有创建或读取真实Binance凭证。Python字符串无法保证物理内存清零，因此仅短生命周期使用且不缓存，不宣称完成硬件级密钥保护。

`HTTPTransport` 和真实WebSocket连接均保留代码级P6验收阻断；只能由后续受控发布改变，无法通过数据库LIVE字段、调用者布尔参数或伪造会话绕过。当前只有精确类型的内存MockTransport可走协议测试路径。生产网络实现存在，但本次未被调用；不把Mock成功称为生产或Testnet验证。

`OrderClient` 要求独立approval_verifier；`EngineClient`适配状态机接口并从approval_provider获取Guardian批准。它们不以请求成功判定成交。生产策略进程仍无凭证与私有网络授权，内部调用者代码不能被当成远程身份认证。

过滤器覆盖SUIUSDT和BNBUSDT，严格检查交易状态、账户permissionSets、价格/止损价格档位、数量、名义金额、百分比参考、订单额度、仓位额度及trailingDelta。未知过滤器、缺失上下文或原生类型不支持则拒绝；订单列表额度与资产级限制未取得可信上下文时不宣称可交易。实时交易对能力尚未通过获授权私有账户核验。

19项适配器Mock测试和7项握手测试通过：OFF零凭证读取/零调用、主动关闭已建立的私有连接和空闲用户流、PENDING仅只读、伪造生产会话拒绝、精确签名、写超时不重试、限流、异常脱敏、提现/划转接口拒绝、批准拒绝、时间错误、用户流授权/断连、费用估值、超过1000笔成交分页及BNB规则。真实账户验证、隔离Testnet验证均未执行；Cursor/Grok外部验收仍缺失。

`PrivateHandshake` 将真实读取协议与本地持久订单/成交身份逐项核对，再调用认证Guardian IPC的reconcile。首次现金必须覆盖2000预算；同币种手动资产仍保留原归属。未匹配挂单、未知状态、累计量差异、成交费率/币种冲突、未送达账本outbox或读取期间余额变化均阻断授权。成交分页从fromId=0开始且限100页，未完成历史不可声称对账通过。保护与过滤器核验依赖独立checker；代码不默认返回通过。

API额外权限通过官方[Get API Key Permission](https://developers.binance.com/en/docs/catalog/core-trading-wallet/api/rest-api/account)只读接口核验；必须具有读取与Spot交易权限，提现、内部/通用转账、保证金、合约和其他非Spot权限必须明确关闭。不会申请或变更任何权限。
