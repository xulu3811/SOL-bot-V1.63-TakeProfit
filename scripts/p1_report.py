"""Build local acceptance reports from recorded execution evidence."""
from pathlib import Path
from datetime import datetime, timezone
import json

root=Path(__file__).resolve().parents[1]
raw=(root/'data/p1-final-evidence.txt').read_text(encoding='utf-8')
state,_=json.JSONDecoder().raw_decode(raw.lstrip())
samples=[json.loads(line) for line in (root/'data/p1-soak.jsonl').read_text().splitlines() if line.startswith('{')]
assert len(samples)==11 and samples[-1]['elapsed_s']>=600, 'Observation not complete'
assert all(x['ready'] and x['service']['ActiveState']=='active' for x in samples)
cpu=(int(samples[-1]['service']['CPUUsageNSec'])-int(samples[0]['service']['CPUUsageNSec']))/1e9
cpu_percent=cpu/(samples[-1]['elapsed_s']-samples[0]['elapsed_s'])*100
rss=[int(x['service']['MemoryCurrent'])/1048576 for x in samples]
peak=max(int(x['service']['MemoryPeak']) for x in samples)/1048576
wal=max(x['file_bytes']['market.sqlite-wal'] for x in samples)
def utc(ms): return datetime.fromtimestamp(ms/1000,timezone.utc).isoformat(timespec='milliseconds')
rows=[{'interval':i,**v} for i,v in state['storage']['intervals'].items()]
acceptance=[
 ('P1-01','PASS','项目分层、锁定18个依赖、低权限 systemd 服务已部署'),
 ('P1-02','PASS','官方 time、exchangeInfo、bookTicker HTTP200；SOLUSDT TRADING/SpotAllowed'),
 ('P1-03','PASS','前30完整UTC自然日及当日已完成K线，五周期可查询'),
 ('P1-04','PASS','收到全部五周期实时消息；独立采样验证一根1m收盘与REST/DB一致'),
 ('P1-05','PASS','唯一键、重复消息测试、生产重复数0；合成10万次写入仅5000唯一记录'),
 ('P1-06','PASS','头部/中部/尾部缺口、首次发现时间和修复状态测试通过'),
 ('P1-07','PASS','分页中断恢复、内部缺口补齐、REST缺少确认时保持不可用'),
 ('P1-08','PASS','WAL、写入回滚、并发/取消、在线备份恢复及完整性检查通过'),
 ('P1-09','PASS','五个只读接口可访问；非法参数422；故障503；仅127.0.0.1:8000'),
 ('P1-10','PASS（限定窗口）','600秒11次采样均就绪；隔离合成突发及稳态测试通过；长期观察延期'),
 ('P1-11','PASS','公开GET端点白名单和市场WS，未使用交易凭据/订单接口/LIVE'),
 ('P1-12','PASS','SSH/NTP/既有定时器运行；既有备份和健康检查执行成功')]
text=['# SOL-bot P1 验收报告','',
 '任务 SOL-P1-DATA-FOUNDATION。P1开发结论 **PASS（本轮功能与有界测试范围）**。',
 f"最终数据快照 {state['sample_utc']}。本地工程 D:/SOL-Bot，远端 /opt/sol-bot。",'',
 '## 报告A　代码与模块交付','',
 'backend/src/solbot/storage 提供规范化模型、六张表、WAL迁移、单写通道、查询、备份与缺口历史。market/rest.py、backfill.py、collector.py 分别提供官方REST、历史与缺口补齐、五流WS与健康指标。main.py、config、api/routes.py 提供配置、生命周期和只读API。使用简洁模块名整合了任务书建议的多个文件。',
 'deploy/solbot-market.service 以 solbot 运行，代码由 root 管理，数据库位于 /var/lib/sol-bot/database/market.sqlite。服务单进程、内存上限850MiB、每300秒最多5次启动，失败15秒后重启。只重启了新建行情服务；既有备份及健康检查另行执行验收。',
 '依赖来自官方PyPI并通过CPython3.14二进制包及SHA256锁定，完整列表在 backend/requirements.lock。关键版本 FastAPI0.162.4、httpx0.28.1、websockets17.4、uvicorn0.54.0。pip check通过。',
 '脚本 scripts/package_p1.py、p1-deploy.sh 提供版本化发布与测试门禁。部署前发现发布目录组权限不足，修正为root:solbot后通过。前次测试SQLite连接警告已修正，最终测试无该警告。回滚步骤见 docs/operations.md。','',
 '## 报告B　行情数据结果','',
 '时间均为UTC毫秒来源转换后的UTC显示。五周期直接来自官方原生K线，字段保留十进制字符串。REST最终值优先，冲突留有事件记录。',]
for row in rows:
 text.append(f"- {row['interval']}，{row['count']}根，首根开盘 {utc(row['first_open_time'])}，末根开盘 {utc(row['last_open_time'])}，末根收盘 {utc(row['last_close_time'])}，未修复缺口 {row['gap_count']}。")
text += [f"数据库 {state['file_bytes']['market.sqlite']:,} 字节，快照WAL {state['file_bytes'].get('market.sqlite-wal',0):,} 字节。全库规范化验证非法行0、未收盘行0、重复0，integrity_check=ok。",
 f"实际服务重启保留55661个既有键，丢失0，重复0，14.66秒重新就绪。首次补齐采用低并发和120请求权重/分钟预算，第一条持久健康快照记录补齐耗时 {state.get('initial_backfill_duration_ms')} ms；后续耗时由健康快照记录。",'',
 '## 报告C　系统运行情况','',
 f"最终版本持续观察 {samples[-1]['elapsed_s']:.1f} 秒，共11次采样全部就绪。常驻服务内存 {min(rss):.2f}–{max(rss):.2f} MiB，cgroup峰值 {peak:.2f} MiB；CPU累计 {cpu:.3f} 秒，约占单核 {cpu_percent:.3f}%（由累计CPU差值/墙钟时长计算）。",
 f"观察中WAL最大 {wal:,} 字节，自动重启计数 {samples[-1]['service']['NRestarts']}，WS重连计数 {samples[-1]['collector']['reconnects']}，错误缓存 {len(samples[-1]['collector']['errors'])} 条。采集处理最大耗时 {max(x['collector']['max_processing_latency_ms'] for x in samples):.3f} ms。",
 '主机2vCPU、1962MiB内存，验收时可用约1.3GiB，根盘可用39GiB。Swap约4GiB，少量约76KiB已用，swappiness20。详见最终原始证据。',
 '隔离合成测试包含10万次行写入尝试、5000唯一记录、约30秒稳态重复送达、WAL备份及重开验证。最终实测值保存在 data/p1-live-load-final.txt。测试流量仅在本地，未对Binance施压。','',
 '## 报告D　QA测试','',
 '最终服务器Python3.14.4执行44项单元测试和12项API集成测试，56/56通过。模拟超时、429/503、官方限制状态、断连重连、加速生命周期轮换、重复K线、数据库失败与取消。生产采样与模拟故障分别保留证据。',
 '独立评审发现的REST确认遗漏和缺口首次发现时间重置均已修复并有回归测试。独立评审范围及限制见 docs/p1-independent-qa.md。']
for number,status,detail in acceptance: text.append(f'- {number} — {status}。{detail}。')
text += ['', '既有备份任务生成mode0640的market.sqlite备份，隔离恢复55661行且完整性ok。备份/健康检查Result=success，SSH、NTP及两项定时器均active。',
 'DEFERRED：24小时以上持续观察、真实24小时连接轮换、长期磁盘增长及异机灾难恢复。当前测试不能承诺7×24零故障。', '',
 '## 报告E　安全与P2准备','',
 '本轮仅采集公开市场行情，未访问Binance账户、未使用真实API Key、未创建交易订单、未开启LIVE、未新增公网入站端口、未修改5%最大回撤硬限制。HTTP只监听127.0.0.1:8000，P1未修改SSH或UFW。',
 '历史SSH口令风险仍标HIGH。用户已确认更换口令并成功使用控制台，但本轮未独立验证旧口令失效。公网上线前须完成风险关闭及云安全组复核。',
 'P2离线指标和影子策略研发具备数据底座条件。继续研发须保持数据就绪门禁，确定长期历史保留及异机备份策略。30日窗口外旧缺口保持显式未修复，须另定修复/归档策略；拆分缺口旧记录的resolved代表旧范围被替代，当前剩余片段仍open。',
 '本次停在P1，公开行情服务按授权保持运行，未进入P2。','',
 '## 证据索引','',
 '- data/p1-deployment-tests.txt — 最终部署与56项测试',
 '- data/p1-live-load-final.txt — 最终实时一致性、隔离负载与恢复',
 '- data/p1-restart-backup-final.txt — 服务重启及继承备份任务恢复',
 '- data/p1-soak.jsonl — 最终版本持续采样',
 '- data/p1-final-evidence.txt — 版本、数据范围、全库校验、端口与系统状态',
 '- backend/requirements.lock — 全部版本与安装哈希',
 '- docs/operations.md — 运行、恢复、回滚和保留策略',
 '- docs/p1-independent-qa.md — 独立评审意见']
(root/'docs/P1-验收报告.md').write_text('\n\n'.join(text),encoding='utf-8')
canvas=Path(r'C:\Users\xl246\.codex\visualizations\2026\11\10\01a1c621-70b7-78a1-8208-753e80123cf7\SOL-P1-report.canvas.tsx')
data={'snapshot':state['sample_utc'],'rows':rows,'acceptance':acceptance,'memory_mib':[min(rss),max(rss)],'cpu_percent':cpu_percent,'seconds':samples[-1]['elapsed_s']}
canvas.write_text('const data='+json.dumps(data,ensure_ascii=False)+';\n'+'''export default function Report(){return <main style={{maxWidth:1100,margin:'auto',padding:24,lineHeight:1.6}}><h1>SOL-bot P1 · PASS</h1><p>公开行情底座已运行。56项自动化测试通过。快照 {data.snapshot}</p><h2>五周期已收盘数据</h2><table cellPadding={8}><thead><tr><th>周期</th><th>根数</th><th>首根开盘 UTC</th><th>末根收盘 UTC</th><th>缺口</th></tr></thead><tbody>{data.rows.map(r=><tr key={r.interval}><td>{r.interval}</td><td>{r.count}</td><td>{new Date(r.first_open_time).toISOString()}</td><td>{new Date(r.last_close_time).toISOString()}</td><td>{r.gap_count}</td></tr>)}</tbody></table><h2>持续运行实测</h2><p>{data.seconds.toFixed(1)} 秒观察；服务内存 {data.memory_mib[0].toFixed(1)}–{data.memory_mib[1].toFixed(1)} MiB；单核平均CPU {data.cpu_percent.toFixed(3)}%。此观察窗口不能证明长期可靠性。</p><h2>逐项验收</h2><table cellPadding={8}><tbody>{data.acceptance.map(r=><tr key={r[0]}>{r.map((v,i)=><td key={i}>{v}</td>)}</tr>)}</tbody></table><h2>继续研发的边界</h2><p>SSH历史凭据风险保持 HIGH，用户已报告换密但旧凭据失效尚未独立验证。API仅127.0.0.1访问。长期观察、异机恢复及历史保留决策延期。具备P2离线研究基础，本轮已停在P1。</p></main>}
''',encoding='utf-8')
print(json.dumps({'snapshot':data['snapshot'],'counts':{r['interval']:r['count'] for r in rows},'memory_mib':data['memory_mib'],'cpu_percent':cpu_percent,'soak_seconds':data['seconds']},ensure_ascii=False))

