"""Write report, script, Q&A and source-bound registries; never invent measured values."""
from __future__ import annotations
import csv
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

base=Path(__file__).resolve().parents[1]
packet=json.loads((base/'pages.json').read_text('utf8'))
sources=json.loads((base/'sources.json').read_text('utf8'))
source_by_id={x['id']:x for x in sources['sources']}
now=datetime.now(UTC).isoformat()


def write(name,text):
    path=base/name;path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf8',newline='') as stream:stream.write(text)


def dump(name,data):write(name,json.dumps(data,ensure_ascii=False,indent=2)+'\n')


claims=[]
for kind,ps in [('proposal',packet['proposal']),('whitepaper',packet['whitepaper'])]:
    for pn,p in enumerate(ps,1):
        for sn,s in enumerate(p['sections'],1):
            experiment=(kind=='proposal' and pn==11) or (kind=='whitepaper' and pn==28)
            hypothesis='待验证' in s['text'] and ('业务' in s['text'] or '真人' in s['text'])
            typ='实验' if experiment else ('假设' if hypothesis else '设计')
            claims.append({'claim_id':f'{kind.upper()}-{pn:02d}-{sn:02d}','text':s['text'],
                'type':typ,'source_refs':p['refs'],'source_sha256':{r:source_by_id[r]['sha256'] for r in p['refs']},
                'allow_in_body':True,'allowed_wording_only':True,'last_verified_at':now,'owner':'/root/full_map',
                'verification_scope':'TEXT_CHECKED_AGAINST_LISTED_PUBLIC_SOURCE_NOT_HUMAN_RESEARCH_OR_FULL_ACCEPTANCE',
                'current_source_runtime_binding':False,'automatic_final_admission':False})
# Explicit business hypotheses are never converted into measured effects.
for i,text in enumerate(['青年愿意用少量策略确认换取持续资金管理','可审计自主动作可能增强控制感和信任',
    '透明低风险产品安排可能改善触达','减少无意义确认可能改善持续使用','动态目标节奏可能改善规划黏性'],1):
    claims.append({'claim_id':f'HYPOTHESIS-{i:02d}','text':text,'type':'假设','source_refs':['S03'],
        'source_sha256':{'S03':source_by_id['S03']['sha256']},'allow_in_body':True,'allowed_wording_only':True,
        'last_verified_at':now,'owner':'/root/full_map','verification_scope':'ORIGINAL_PLAN_BUSINESS_HYPOTHESIS_NOT_MEASURED',
        'value':None,'automatic_final_admission':False})
dump('claims_registry.yaml',{'schema_version':1,'format':'JSON_IS_VALID_YAML_1_2',
    'scope':'CURRENT_MATERIAL_PARAGRAPHS_AND_EXPLICIT_HYPOTHESES','external_fact_count':0,
    'automatic_effect_claim_gate':'NOT_PASSED','manual_consistency_review':'NOT_RUN','task_closed':False,'claims':claims})

figures=[
('F1','资金分层漏斗','同run实际账本、Goal归属、保护与自主容量','MISSING_NATIVE_INPUT'),
('F2','系统架构图','当前源组件职责与来源分层','TEXTUAL_SOURCE_SCHEMATIC_ONLY'),
('F3','策略版本状态机','原生命周期和明确确认合同','TEXTUAL_SOURCE_SCHEMATIC_ONLY'),
('F4','现金流约束时轴','同run1098阶段点与实际保护输入','MISSING_NATIVE_INPUT'),
('F5','资产可行候选图','同run目录/权限/锁定/到账拒因','MISSING_NATIVE_INPUT'),
('F6','恢复与介入流程','同run消费/无损/损失报价/确认/到账','MISSING_NATIVE_CAPTURE'),
('F7','决策溯源图','同run原事实/版本/候选/约束/回执/审计','MISSING_NATIVE_CAPTURE'),
('F8','实验对照图','冻结输入、真实baseline/消融、独立指标原数据','NOT_RUN'),
]
dump('figures_registry.yaml',{'schema_version':1,'required_mvp_figures':8,'status':'INCOMPLETE',
    'native_screenshot_count_in_final_materials':0,'renderer_does_not_prove_capture':True,
    'figures':[{'id':fid,'title':title,'actual_required_input':inp,'status':status,'final_native_artifact':None,
        'allowed_as_schematic_only':fid in ['F2','F3'],'source_refs':['S03','S41','S01'],
        'owner':'/root/full_map','checked_at':now} for fid,title,inp,status in figures]})

original_metrics=json.loads((base/'source-originals/docs/spec/mvp-metrics.json').read_text('utf8'))
mvp_metrics=[{'id':x['id'],'name':x['name'],'status':'NOT_RUN','value':None,'numerator':None,'denominator':None,
    'original_denominator_definition':x['denominator'],'original_oracle_definition':x['oracle'],
    'raw_refs':[],'source_ref':'S39','reason':'Formal complete 24-case five-arm independent results not accepted'}
    for x in original_metrics['metrics']]
full_groups={
 'SAFETY':['硬约束违反率','未授权动作率','目标资金误用率','流动性短缺率','有损赎回误自动率','未来收入错误计入次数','旧策略版本使用次数','重复副作用次数','未知状态误判次数'],
 'AUTONOMY_EFFICIENCY':['安全可自动动作覆盖率','每任务人工介入次数','不必要问题数','关键问题遗漏数','动作完成时间','因过度保守而闲置的可安全资金','目标资金配置偏差','安全恢复时延'],
 'CAPITAL_EFFICIENCY':['确定性净模拟收益','资金闲置天数','资产切换次数','提前支取损失','流动性冗余'],
 'EXPLANATION_AUDIT':['决策可追溯率','关键证据完整率','解释与真实约束一致率','审计链验证率','失败定位时间','用户是否能正确理解Agent权限']}
full_metrics=[{'id':f'FULL-{group}-{i:02d}','id_basis':'LOCAL_MATERIAL_INDEX_NOT_RENUMBERED_REQUIREMENT',
    'name':name,'group':group,'status':'NOT_RUN','value':None,'numerator':None,'denominator':None,
    'definition_frozen':False,'raw_refs':[],'source_ref':'S03','source_lines':'854-896',
    'reason':'Full family/variant/arm/ablation denominator and oracle not accepted'}
    for group,names in full_groups.items() for i,name in enumerate(names,1)]
dump('metrics_registry.yaml',{'schema_version':1,'source_bound':True,'status':'NOT_RUN',
    'mvp_required_14':mvp_metrics,'full_original_measures':full_metrics,
    'comparison_guard':'same safety/authorization; no relaxed constraint yield advantage',
    'historical_w0':'S35, each cell n=1, mixed results, not a current benchmark',
    'human_participants':0,'human_records':0,'human_status':'NOT_STARTED','current_performance':'NOT_MEASURED'})
dump('results/observations.json',{'protocol':'MATERIAL_OBSERVATION_PLACEHOLDERS_NOT_EXPERIMENT_OUTPUT',
    'status':'NOT_RUN','simulation':True,'native_experiment_run_id':None,'metrics':mvp_metrics+full_metrics})
out=base/'results/observations.csv'
with out.open('x',encoding='utf-8-sig',newline='') as stream:
    writer=csv.DictWriter(stream,fieldnames=['scope','metric_id','metric_name','status','value','numerator','denominator','run_id'])
    writer.writeheader()
    for scope,items in [('MVP',mvp_metrics),('FULL',full_metrics)]:
        for x in items:writer.writerow({'scope':scope,'metric_id':x['id'],'metric_name':x['name'],'status':'NOT_RUN',
            'value':'','numerator':'','denominator':'','run_id':''})

report='''# FULL-903 当前实验报告

状态：DOCUMENTARY_REPORT_DELIVERED / EXPERIMENT_ACCEPTANCE_INCOMPLETE。报告正文、指标空表与真实定向证据索引已经可读；没有新运行实验，不关闭FULL-903。所有资金模拟，没有真人收益或银行业务效果。

## 1. 原实验范围，没有缩成初版

原完整计划15.1、15.6、14.2及FULL804/805要求：至少50场景族，每族多变体，族级开发/验证/冻结split；B0手工预算、B1余额阈值、B2固定预算+固定产品、B3全量确认、B4仅模型置信度、B5忽略流动性的收益优先优化器、P完整方案。八消融分别移除证据等级、策略版本、动态生活准备金、多目标约束、流动性过滤、最小问题选择、安全恢复、审计链。全部正式实测结果待取得。

初版另有24案配额6/6/4/4/2/2、B0—B3/P及14指标，不能替代Full50族和七机制。作者/开发完整flow新颖性、原冻结真值、独立oracle、合法实际不同策略adapter均需原件。局部provider风险或MODEL_ONLY候选不能重标成完整SERVICE_INTEGRATION。

## 2. 统计口径与缺失

安全比较使用相同事实、初始权限和保护网关，分别列unsafe candidates、gateway拒绝、实际bank/app后果。拒绝不是资金违规；外生消费缺口不当作Agent造成。safe-auto机会、截止checkpoint、required Evidence/audit目标与失败步骤在运行前登记，未尝试或失败不删分母。

`results/observations.csv/json`是MATERIAL_OBSERVATION_PLACEHOLDERS，不是原实验输出：14初版+28完整计划指标均NOT_RUN，值/分子/分母/run_id为空。零分母须NOT_APPLICABLE/null，不能写100%；未完成恢复保右删失/缺失。当前完整指标分母与oracle尚未冻结，不能拿本表当预登记成功。确定性净模拟收益仅在同样安全授权约束下比较，不能以放松安全换收益。合成B0 actor不是真人耗时。

## 3. 可引用的工程定向运行

| 原件 | 原结果 | 可引用范围和限制 |
|---|---|---|
| E01 固定付款AUTO/ASK | PASSED，2个明确parameter节点，exit0，wrapper851.602285s | 相关scope稳定true、全源false；pytest848.02s为整批时长，非单动作或benchmark。原四银行腿/同key协调/只读尾部以日志为准 |
| E02 审计/问答/对账三节点 | PASSED，exit0，wrapper3734.677602s | 607节点在整批中通过；没有单项时延。并非全部九类对账或最终浏览器 |
| E03 整组资产节点 | 原PASSED，2826.45s | DIAGNOSTIC_WITH_RELATED_SOURCE_CHANGE：运行中相关保护源变化；原状态不改，但不充当前稳定验收 |
| E04 GLOBAL新批 | FAILED，exit1，wrapper10.730693s | 原日志/source保留；后续未执行节点不补PASS，独立actual-v2新源不等实际验收 |

环境、command、HEAD、source.before/after、wall_seconds与log SHA来自所列原manifest；具体数值不得扩成产品收益。HEAD相同也不表示dirty源码相同，必须读该run的实际源字节和scope。

## 4. 历史W0性能，不能外推当前

W0每路径n=1，同输入原结果复核。short API0.385204→0.327347秒；long API101.635072→19.960296秒；expanded API112.629382→138.810674秒变慢。长链Python peak249640231→280724062字节，扩大353670321→392620660字节亦增加。API、服务、profile、SQL和工作集是不同计量层；工作集含夹具导入，不能计算原生请求内省内存结论。

当前Full性能NOT_MEASURED，没有P95/P99、重复置信区间、当前SLA或总体优化倍数。详见S35完整原测量及方法限制，本报告不重标旧FAILED。

## 5. 失败样本和当前缺口

E03前置prepare500是历史fixture clock早于实际OPEN opened_at，0013守卫拒绝；新candidate只用本节点actual server UTC，未改trigger/hash/正式历史。当前源稳定的资产整链仍待。E04真实失败与旧204容量/来源误引用导致UNKNOWN保留，不能将UNKNOWN显示globalComplete。Root报告的新307preview可读但prepare完整引用核验拒绝修复中，当前无银行执行成功；这项报告状态只是负责人最新交接，未作为原生效果数据。

102 Full图v2已登记35业务表，31不同纯/HTTP风险检查是工具/合同，actualPG NOT_RUN；查ID不代表金融成功。新604/307/105实际集中节点、Full50族七机制八消融、全部并发/强杀/重启、当前性能和原最终全量未完成。

## 6. 研究与结论

原计划拟18—24成年参与者、合成账户、全量确认/静态预算/P任务比较，仅探索性。现在真人0、records0、NOT_STARTED。无访谈、问卷评分、理解正确率或信任校准效果。当前结论仅为已实现模块与所列定向证据范围；不作收益、优势、显著性、普遍安全或总体青年推断。

来源：S03（802—941、1412—1430）、S04、S06、S32、S34、S35、S39、S40及E01—E04。自动材料内容/效果门尚未通过，人工一致性审阅NOT_RUN。
'''
write('experiment-report.md',report)

segments=[
 {'start':0,'end':20,'topic':'问题与模拟边界','chain':None,'screen':'标题及真实当前dashboard','say':'钱途有界回答的不是余额有多少，而是哪笔钱受到保护、今天允许什么动作、何时需要我确认。所有账户、产品和动作都是模拟，未来工资不会扩大今日可安排金额。','required':'当前epoch/source、账户原金额与保护解释','gap':'当前主演示同run画面MISSING'},
 {'start':20,'end':70,'topic':'工资到账 / A','chain':'A','screen':'原工资入口、保护、Goal分配、真实T+1候选和行动回执','say':'工资的原流水入账后，先保护房租、账单和生活准备金，再按已确认目标分配新增来源。资产选择要满足原授权与到账时刻。T+1本金必须按真实可用顺序显示，不能今天提前算现金。','required':'原income/posting、Goal源分、原边界/产品版本、T+1原Action/receipt/available_at','gap':'当前完整A的T+1原执行/到账画面与证据尚缺；不能替T0并改标签'},
 {'start':70,'end':120,'topic':'自然语言目标 / B','chain':'B','screen':'原句→结构化候选→用户改字段→明确首次ACTIVE→新增工资分配','say':'输入真实原话，系统只生成候选。这里由用户修改目标金额、日期、优先和允许范围，再明确确认同配置。目标资产容忍度如果来自另一份授权，必须单独展示那次确认。随后只有新增已到账资金才按当前版本归属。','required':'同run原text/config差异、首次confirmation/version、额外授权次数、后续income与Goal effect/receipt','gap':'最终当前source整条B录屏MISSING，旧模板或旧三轮不能冒NL链'},
 {'start':120,'end':180,'topic':'消费收缩 / C','chain':'C','screen':'普通消费→边界缩小→无损原授权恢复→固定损失报价/ASK确认','say':'消费是一项账户事实。边界收缩后只在原授权内恢复能及时到账的现金。切到固定产品，提前支取的损失和报价必须明确展示；没有对这一笔效果的确认就不会自动承担损失。确认后再看原到账和回执。','required':'原消费posting、前后保护、T0/T1恢复可用时刻、quote/loss/fee、明确effect确认、银行与app状态','gap':'当前同run完整无损及有损画面MISSING，实际结果不由脚本给定'},
 {'start':180,'end':215,'topic':'证据回溯与UNKNOWN','chain':'EXCEPTION','screen':'原Trace、版本、约束、候选、回执与原键只读协调','say':'每个结论能回到当时来源、策略版本、候选淘汰和银行回执。响应丢失时保留原行动与原键，银行可能已结算而应用尚无回执，不能再扣一次。对账只读，UNKNOWN不写成零或失败。','required':'同run全部原trace/source/hash，异常原HTTP/银行key/receipt，最终完整audit','gap':'最终当前源同run异常与证据回溯镜头MISSING'},
 {'start':215,'end':240,'topic':'实验与边界','chain':None,'screen':'原对照指标/失败图；当前先显示待实验','say':'基线和消融必须真实不同机制、同样安全授权、独立真值。当前正式对照、真人研究和新性能还没有完整结果，因此这里不展示提升百分比。下一步完成集中验收，真实银行接入另需独立业务安全评估。','required':'实际Full50族七机制八消融或初版明确范围原CSV/JSON和同run图','gap':'效果图NOT_RUN；目前只能诚实展示待实验，不能称原对照演示完成'},
]
dump('demo-shot-list.json',{'protocol':'bounded-funds-240s-planned-script-not-recorded-v1','planned_seconds':240,
    'actual_duration_seconds':None,'status':'NOT_RECORDED','native_run_id':None,'segments':segments,
    'original_mvp_time_slots':[30,55,55,65,45,30],'original_mvp_sum_seconds':280,
    'explicit_revision':'SCRIPT_TIMEBOX_REVISION_20261006_KEEP_ALL_CONTENT_240_SECONDS',
    'revised_time_slots':[20,50,50,60,35,25],'recorded_video':None,'audio_requirement':'NO_MANDATORY_AUDIO_TRACK_FOUND_IN_ORIGINAL_PLAN',
    'business_complete':False,'task_closed':False})
script=['# FULL-904 四分钟演示脚本与镜头清单','','状态：SCRIPT_DELIVERED / NOT_RECORDED / NOT_TIMED。计划240秒不是实测时长；没有新视频、真实配音或现场演练。',
    '', '## 显式时段修订','', '原初版14.1六时段30/55/55/65/45/30相加为280秒，与标题“四分钟”及追踪表240秒不一致。此次明确登记SCRIPT_TIMEBOX_REVISION_20261006：20/50/50/60/35/25=240秒，只压缩解说/切换时间，原六类内容、T+1、NL修改与首次确认、无损/有损、原Trace/异常和对照都保留。尚未实际演练，不能保证按此计划完成。原计划字节不改。',
    '', '原计划未发现强制音轨验收；以下是可口述稿，不声称已配音。若无旁白，需真实字幕/指示而不覆盖金融金额或状态；时长与业务内容分别核。', '', '## 现场步骤与逐段口述','']
for s in segments:
    script += [f'### {s["start"]:03d}—{s["end"]:03d}秒　{s["topic"]}', '', f'画面：{s["screen"]}。','',s['say'],'',f'原件必须：{s["required"]}。',f'当前缺口：{s["gap"]}。','']
script += ['## 备用与录制约束','', '备用需完整三条真实黄金链，本地离线使用已核current source/image与容器出口、原browser只loopback请求捕获。主片/各备用片保native run_id、UA、HTTP、Trace、原视频SHA、完整decode/时长及全表checkpoint/audit；原片保留。剪辑如采用非连续cuts/变速，必须EDITED_FROM_NATIVE并列原片SHA、逐段时间范围和速度，不能把示意或derived PNG充原生截图。',
    '', '所有reset只在owned隔离库并先归档/seal；不能为现场重置正式模拟历史。按钮失败或UNKNOWN仍按原身份停住，切备用只能切真实已记录素材，不伪造成功。当前最终录制、完整decode、<=240s实测、三链/异常人工内容审阅、当前离线备用均MISSING/NOT_RUN。',
    '', '历史三轮素材及后验完整decode只证明旧run；不将其升级为CURRENT_MVP或新Full录屏。出处S41:1029—1043、S04 FULL904、S03:1448—1450，原媒体登记/失败保持。']
write('demo-240s-script.md','\n'.join(script)+'\n')

qa=[
('为什么不是余额阈值？','阈值只观察当前余额，本方案还核保护截止、目标归属、产品可用、原授权和来源完整性；这只是机制差异，现无对照优势数据。','S01 S06 S07'),
('AI决定了哪部分？','有限自然语言候选与受约束解释；金额、权限、状态、安全和执行由确定性代码处理，候选不自动ACTIVE。','S01 S11'),
('已经接工行了吗？','没有。账户、银行与产品全部模拟，工行闭环是设计范围/落地假设，无真实准入或合作证据。','S01 S03'),
('有用户研究结论吗？','真人0、记录0、NOT_STARTED。18—24成年合成账户任务是原计划，不是已招募数量。','S03 S32'),
('1098是什么？','初始日加365未来日期，每日三个阶段，366×3=1098阶段点，不是1098天。原90日执行视图另保留。','S02 S12'),
('未来工资和预计回款能支持今天吗？','未到账收入今日计入0；条件本金/赎回未实际可用前不是现金。规划展示和银行事实分开。','S01 S07 S12'),
('策略改变会改旧hash吗？','不改，原版本/命令/回执留存。新动作重验当前权限，历史已接受/结算按原身份协调。','S10 S21 S23'),
('响应丢失会重扣吗？','保原action/key和UNKNOWN，查询原bank/effect并协调原投影；需要真实节点检验该范围，不能只靠UI提示或所有“重试”标签证明。','S21 S22 E01'),
('为什么NOT_FOUND还不允许换key？','只读快照未找到不是持久终败，新键可能形成第二动作；客户端保原完整请求，手动exact lookup/同原body重放。','S10 S19 S27'),
('目标资金会自动借给房租吗？','跨Goal默认拒绝；专用scope明确确认及具体effect/来源分重核后才能按实际支持路径处理。声明不是已回拨。','S13 S15'),
('固定损失可自动承受吗？','不能借一般资产权限承担新损失。原quote/effect及费用必须展示并对本动作明确确认，过期拒绝。','S21 S22 S30'),
('一次一问答完就可付款吗？','答案只筛世界，重新计算当前动作；不是金融确认。通知ACK/关闭也不授资金权限。','S18 S19 S20'),
('对账MATCHED证明什么？','本RRRO快照的已核分母账目相符，不证明每个准备动作已执行，也不证明现实经济效果；原bank/app/receipt分别显示。','S24 S31'),
('已经有实验提升百分比吗？','没有。初版24×5/14指标，完整版50族多变体、B0—B5/P及八消融均需正式结果；当前观测表空白。','S03 S06 S39'),
('是不是已完成全部需求？','正式21/92、FULL逐项PENDING。源码可调用、局部证明、全量验收、真人与材料审查分别登记，页数不是关闭依据。','S04 S05 S38'),
('性能优化是否稳定？','W0每格n=1结果混合，扩大会变慢，内存有增加；新当前Full性能未测，不作SLA。','S35'),
('四分钟视频在哪里？','本包只有240秒修订计划和镜头清单，NOT_RECORDED。当前同run三链、T+1/NL/异常/对照及离线备用仍缺原件。','S41 S04'),
('下步如何试点？','先完全模拟及成年人合成账户研究；原计划再考虑只读影子、小额沙箱，真实动作须机构业务、消保、安全与合规独立审查。现无准入。','S03 S26'),
]
deck=['# FULL-905 10页答辩与问答','','状态：EDITABLE_DECK_DELIVERED / PRODUCT_ACCEPTANCE_INCOMPLETE。可编辑PPTX结构校验及库导入已执行；Microsoft PowerPoint实际打开/现场呈现未执行。','']
for i,s in enumerate(packet['slides'],1):
    deck += [f'## 第{i}页　{s["title"]}', '',s['headline'],'']+['- '+t for t in s['body']]+['',s['note'],'','来源：'+', '.join(s['refs'])+'。','']
deck+=['## 可能问答','']
for q,a,refs in qa:deck += [f'### {q}','',a,'','证据：'+refs+'；见evidence-index.md。','']
write('defense.md','\n'.join(deck)+'\n')

index=['# FULL-906 当前材料来源与证据索引','','状态：SOURCE_LEDGER_DELIVERED / FINAL_CLAIM_GATE_NOT_PASSED。来源索引是公开文档/代码的明确子集，不宣称全仓冻结或全证据验收。外部事实/市场数字实际引用为0；业务价值均是假设。','',
    '## 登记规则','', '正文每页与每段来源已记录，`claims_registry.yaml`的正文许可只针对原限定措辞，automatic_final_admission=false；不是自动效果审查通过。`figures_registry.yaml`明示八图缺口，没有native截图。`metrics_registry.yaml`保初版14项与原Full28项，值均NOT_RUN/null；CSV空列不是真结果。','',
    '当前已有 `scripts/evidence_check.py` 只把显式真实原请求交给原 `export_evidence.py`；缺请求exit2，MISSING/UNVERIFIED/INCOMPLETE非零。原exporter的complete_mvp_documents/eight_figures/manual_consistency_review等未支持内容审查不会因为本台账存在变VERIFIED。没有调用其完整出口，也没有新增验证框架或弱化门。FULL808效果数字/外部来源与CI自动准入、真人研究和人工一致性仍未完成。','',
    '## 精确原件','', '| ID | 原路径 | SHA256 | 证据范围 |','|---|---|---|---|']
for s in sources['sources']:
    index += [f'| {s["id"]} | {s["repository_path"]} | {s["sha256"]} | {s["binding"]}'+(f'; original {s["original_status"]}' if s['original_status'] else '')+' |']
index += ['', '完整原字节副本位于source-originals；sources.json记录absolute原路径、捕获时刻、byte size。COPY不是重新生成金融证据，HISTORICAL_CONTEXT不参与当前源最终成功验收。相同HEAD不能代替dirty字节一致。', '',
    '## 22章节覆盖到实际30页','', '1摘要→p1；2术语→p2；3青年情景→p3；4原则→p4；5事实→p5–6；6DSL→p7–8；7NL编译→p9；8包络→p10；9现金流→p11–12；10多目标→p13–14；11冲突/修复→p15；12资产/定存→p16–18；13最小介入→p19–21；14执行恢复→p22；15审计解释→p23–24；16安全隐私→p25；17实验→p27；18结果→p28；19真人研究→p29；20失败→p29；21工行假设→p30；22限制后续→p30。p26补实际工程与部署边界。', '',
    '## 自动/人工边界','', 'PDF实际页数由pypdf，渲染由Poppler；PPTX结构/几何/字体与Artifact Tool import由技能finalizer。它们不证明事实内容、实验效果、真人理解、现场使用或原生截图。AI版面审阅单列build/visual-review.json，人工逐claim审查NOT_RUN。原旧材料、FAILED日志、视频与正式模拟历史没有覆盖。']
write('evidence-index.md','\n'.join(index)+'\n')

write('current-owner-gap-addendum.md','''# 最新负责人交接缺口（不是原生实验结果）

本材料编写期间Root报告：102新35表完整引用图Backend已FINAL，31不同pure/HTTP检查，actualPG NOT_RUN；新307 actualpreview可读但prepare409，完整exposure额外Evidence引用接缝正在严格修复/核验，未银行执行；旧204v1多次UNKNOWN涉及原seed容量及引用不存在ledger_heads表，新actual-v2正在冻结。本文只记录负责人状态，不把该消息替代原run/log或成功证明。

正文采用保守共同边界：102缺actualPG；307无完整执行验收；204不称globalComplete。后续Root的正式日志/冻结源码/终态若到达，应在新修订版本绑定，保本版与旧失败，不静默升级本版claim。当前Root独占实际金融链，材料任务没有参与PG/Browser/正式数据变更。
''')

readme='''# W8 当前功能材料交付

新目录交付，旧材料/失败/正式历史保留。正式关闭仍21/92；FULL901—906全部PENDING。本包提供可审阅成品与来源，不将INCOMPLETE升级为全验收。

- `outputs/钱途有界_当前功能企划_12页_交付版.pdf`：真实12页与proposal.md。
- `outputs/钱途有界_当前功能白皮书_30页_交付版.pdf`：真实30页与whitepaper.md，覆盖原建议22章。
- `outputs/钱途有界_当前功能答辩_10页_交付版.pptx`：实际10页可编辑文字、notes来源，defense.md含18问答。
- `experiment-report.md`：保初版/Full完整基线/消融/原定向失败与分母；results空值明确NOT_RUN，非效果数据。
- `demo-240s-script.md` / demo-shot-list.json：显式280→240秒时段修订，NOT_RECORDED/NOT_TIMED，完整内容未删。
- 三registry、sources.json、evidence-index.md及source-originals：公开原字节/SHA/状态/用途，效果自动门NOT_PASSED。

## 构建与检查（仅文档，无LO/安装/金融）

已安装bundled Python+ReportLab/真实msyh中文字库生成PDF；已安装bundled Node `@oai/artifact-tool`生成PPTX，技能finalizer以Python核包/几何/字体并实际导入。Poppler把全部42页PDF转PNG，所有10页PPTX渲染审阅。没有依赖安装、LibreOffice、PG/Browser/Docker/seed/reset或真实资金。

生产build-proposal仍是旧HTML源审阅入口，未修改tasks/CI；本包的builder只新目录。author_materials.py和两次content/layout revision保存原版，不覆盖成品。复建须复制本new source到新独立目录、保ROOT路径引用；render_pdfs/refuse-existing、finalizer/refuse-existing防旧成品覆盖。运行Node须设置RUNTIME_NODE_MODULES为load_workspace_dependencies给出的实际Node包路径；最终校验子进程在sandbox中首EPERM失败保留，后授权仅文档子进程成功。

## 未覆盖

正式Full50族/多变体/七机制/八消融、完整独立指标/统计、真人18—24计划（实际0）、新当前性能、最终两版全量、最终当前三黄金链/异常/离线/同run原生截图与<=240秒视频、实际PowerPoint打开/投影、人工业务/消保/安全与逐claim一致性审查都未完成。原八图缺数据/原画面，示意文字不冒native capture；旧W0性能和旧资产diagnostic只historical context。

当前自动材料出口仍INCOMPLETE；manifest的actual_pages/slides仅结构事实，不关闭任何FULL原项。
'''
write('README.md',readme)
print(json.dumps({'claims':len(claims),'sources':len(sources['sources']),'mvp_metrics':len(mvp_metrics),
    'full_measures':len(full_metrics),'questions':len(qa),'planned_duration':sum(x['end']-x['start'] for x in segments),
    'actual_recording':'NOT_RECORDED','acceptance':'INCOMPLETE'},ensure_ascii=False))
