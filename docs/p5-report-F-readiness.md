# P5 报告F：P6集成与未验收项

本阶段停止于P5，生产保持LIVE OFF。当前Codex会话和辅助代理完成开发及独立Codex QA，未声称已经选择任务标题中的特定Max/Extra High产品配置，亦未执行指定Cursor/Grok外部审计。

已实现：签名私有REST协议、WS用户流、受限凭证加载、订单持久状态机、查询恢复、真实成交结算、Guardian认证IPC及一次性批准、原生保护管理、APTS执行桥接、BNB补充、账户握手、影子状态比较视图和OFF部署服务。

已验证范围：本地Mock、隔离SQLite、生产服务器上的无网络全量回归、备份恢复及OFF服务运行。未执行Binance Spot Testnet；未读取交易密钥，未请求生产账户，未验证生产API权限、真实订单、SUI实际保护支持或真实费用折扣。

P6之前保持以下上线阻断：Android正式认证/端到端主动授权尚未验收；真实账户归属与订单恢复未验收；原生保护能力及空窗补偿未通过真实环境验收；外部Cursor/Grok审计未完成；P0历史SSH凭证失效与管理权限风险未独立关闭；P3实证成本和概率限制仍保留。

未来接入必须使用服务器签发授权租约而不是客户端LIVE布尔值，先LIVE_PENDING只读握手，全部检查通过才可ON。当前production transport和LiveSession均采用代码发布门拒绝；需要后续正式验收及受控发布，不能通过修改数据库或Mock授权开启生产。MockAuthorization仅能配内存MockTransport。

限制必须向Android显示：OFF后动态APTS暂停，保留既有已确认原生保护；保护未知不能显示“安全”。保护锁定全部仓位时，缺少额外机器人free数量则拒绝无保护替换，保留旧保护并告警。止损限价有跳空不成交风险，保护订单ACK不代表未来保证成交。

当前结论PARTIAL。开发与Mock通过不构成真实交易许可，不自动开启LIVE或继续P6。
