# S1 契约

策略代号 S1，版本 `s1-short-1`。它与 T1（`p2-research-1`）并行，不替换 T1，不改 T1 的门槛。

## 本阶段接线

- `solbot.strategy.evaluate()` 仍只返回 T1 与 R1。
- Guardian 继续只放行 `strategy_id == T1`。S1 不能得到下单许可。
- 实盘保持关闭。S1 不调用 Binance 下单接口。
- R1 继续永久关闭。

## 频率验收

在 2025-11-10 07:25:59 UTC 至 2026-11-10 07:24:59 UTC 的 SOLUSDT 现货上计数。

- 机会：每个 UTC 日的 S1 接受候选数。中位数落在 3–5，并且至少 80% 的日子不少于 1 个。
- 成交：研究账本的 ISO 周成交笔数，中位数落在 7–10。
- 持仓硬顶 24 小时，到点按研究规则平仓。
- 机会大约是成交的 3–4 倍。第二层筛选只用时间、仓位槽位或信号到期，不用事后盈亏。

计数只做一次。结果落在区间外时交回漏斗，不循环修改 RSI、RVOL、振幅或时间桶去凑笔数。

## 收益封印

2026-11-10 07:25 UTC 之前的扣费收益只作记录。这段收益不能用来比较后宣布 S1 替换 T1，也不能作为打开实盘的依据。可与现金、以及同一费率下的 T1+APTS 比较的行情，从该时刻之后才开始。

费率沿用回测准则：BNB 抵扣后吃单 0.075%，来回 0.15%。

## 允许新增

- `docs/s1-contract.md`
- `docs/s1-market.md`
- `docs/s1-audit.md`
- `backend/src/solbot/strategy/s1_short.py`
- `backend/src/solbot/strategy/s1_execution.py`
- `backend/src/solbot/mathematics/s1_sizing.py`
- `backend/src/solbot/storage/s1_store.py`
- `scripts/s1_count.py`
- `tests/unit/test_s1_short.py`
- `tests/unit/test_s1_sizing.py`
- `tests/unit/test_s1_execution.py`
- `tests/unit/test_s1_store.py`
- `data/s1_frequency.json`（计数输出）
- `data/s1_research.sqlite`（独立研究库，可由脚本生成）

## 禁止修改

- `backend/src/solbot/strategy/t1_pullback.py`
- `backend/src/solbot/strategy/r1_reversal.py`
- `backend/src/solbot/strategy/signal_scoring.py` 的权重
- `backend/src/solbot/strategy/__init__.py` 的 `evaluate()` 返回集合
- `backend/src/solbot/mathematics/apts_exit.py` 的 `update_protection`
- `backend/src/solbot/mathematics/profit_trail.py` 的 1.5% 启动与 0.5% 回撤
- `backend/src/solbot/risk/guardian.py` 的 T1 限制
- 生产 LIVE 开关
