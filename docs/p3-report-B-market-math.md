# 报告B：盘口、微观结构及数学模型验证

验收日期：2026-11-11。范围为公开行情与影子研究，所有数学输出均 `live_authorized=false`。

## 已实现及复用

复用此前完成的 depth snapshot/diff 桥接、U/u 连续性、失步重建、aggTrade 窗口和 bookTicker。SOLUSDT、SUIUSDT 分别采集、分别入库；盘口只有同步且覆盖范围有效才可用。bookTicker 无交易所事件时间时保留 null，不推造时间。公开微观样本约每5秒保留一次，最多7天/120000行，和必须长期保存的决策审计分离。

入场消费者按决策时刻重新检查深度事件/接收时间不超过2秒、成交流15秒、ticker接收5秒；不能依赖持久化的 ready=true。来源未来、陈旧、失步、未完成预热均拒绝新入场。原始快照保存在 source_snapshot，原 input_hash 可独立重算；消费者降级与所用闭合K线另存，不覆盖原始来源。

已有持仓保护的最小条件是新鲜、同步、有效深度；成交流预热或K线健康失败不单独阻断旧止损。完全过期或失步时保留保护状态但不生成成交。此分离已通过独立保护SELL测试，BUY仍禁止。

## 数学与证据

金额、价格、数量、费用、风险及输入哈希采用 Decimal；不受控 float、布尔金额、NaN、Infinity 均拒绝。P2 score/score_factors 在桥接副本转为字符串；完整原始 payload 序列化及其 SHA256 保留。accepted_after 额外添加的 decision_id 不混入原 P2 payload hash。运行状态中的旧诊断分数单独规范化，不放宽数学序列化。

独立参考覆盖非十进制 tick/step 的整数取整、手算微价格/价差/不平衡、Wilder ATR、费用与EV、动态仓位、保护价格和阶段边界。QA自编32项、回放工具9项分别通过；最终本地全量219项，部署版本服务器全量218项。数量差异是一项部署后补充的真实 Scheduler 分钟边界独立测试，运行源码相同。

## 审计存储

新增每标的独立 math-audit SQLite：signal identity 唯一键，INSERT OR IGNORE，不可用相同ID覆盖结果。zlib 压缩保存完整规范化信号、原始P2 payload、来源快照、账户、模型输入/结果、数据/决策时间、参数版本和拒因。保护审计在同一账本事务保存关键输入、所用趋势证据及K线、保护调整、模拟成交。

没有仓位/挂单时不逐深度事件生成保护审计。拒绝信号的研究诊断按策略及5分钟候选去重；不能因压缩或去重丢弃实际订单/保护决策输入。已有持仓时审计量会增加，当前短期资源验收不能代表长期持仓写入容量证明。

## 限制

可见深度不等于真实排队位置，主动成交比例不等于未来涨跌概率。撮合参与率、滑点、费率、成交概率均为研究假设；当前没有独立校准的胜率/成本样本，因此价格模型保持 INSUFFICIENT_EVIDENCE，未为产生订单放宽限制。真实样本和合成边界测试分别报告。

证据：data/p3-micro-observation.txt、data/p3-independent-local-tests.txt、data/p3-final-tests-v2.txt、data/p3-final-local-unit.txt、data/p3-final-local-integration.txt、data/p3-final-observation.txt。
