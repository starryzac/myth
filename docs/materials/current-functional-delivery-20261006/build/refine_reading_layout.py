"""Preserve the first documentary version and add source-specific protocol tables."""
import json
import shutil
from pathlib import Path
from author_materials import markdown_pages

base=Path(__file__).resolve().parents[1]
saved=base/'build/revision-1'
saved.mkdir(exist_ok=False)
for name in ['pages.json','proposal.md','whitepaper.md','build/render_pdfs.py','build/pdf-structure.json']:
    target=saved/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(base/name,target)
packet=json.loads((base/'pages.json').read_text('utf8'))
tables=[
 [('项目','本书口径'),('功能状态','可调用模块 / 逐范围证据'),('原项关闭','21/92；FULL均PENDING'),('材料状态','页数实核；内容验收INCOMPLETE')],
 [('术语','实际含义'),('Fact / Policy','来源状态 / 确切意图与权限'),('Action / Receipt','经济身份 / 原范围证明'),('UNKNOWN / null','结果未知 / 数值未知，不补0')],
 [('使用任务','必须保留的约束'),('收入安排','已到账原来源与保护截止'),('目标储备','用途归属与当前月资格'),('产品配置','实际本金可用时刻')],
 [('不变量','验收关注'),('现金保护','硬义务截止、占用、Goal归属'),('权限不扩大','失效版本 / 未来收入 / 单次确认'),('经济身份','UNKNOWN同key、同effect')],
 [('来源字段','核验职责'),('owner / status','主体及当前来源状态'),('valid / observed','适用时间及系统知悉时间'),('content / reference','原内容hash和引用完整性')],
 [('账本口径','保持独立'),('收入source-fragment','来源可分配余额与保护'),('Goal ownership','现金/本金的用途归属'),('asset position','放置、可用、实际回款')],
 [('配置层','实际行为'),('Schema校验','候选合法字段/类型'),('config hash','确切规范化配置'),('explicit confirm','后续独立用户动作')],
 [('原命令','留存要件'),('CREATE / CHANGE','原body、版本、key/hash'),('SUSPEND / REVOKE','当前权限与未提交零效果'),('by-key RECORDED','原回执匹配，不借latest')],
 [('编译阶段','输出边界'),('有限规则','配置、片段、缺项与假设'),('可选provider','默认关闭；不添加新事实'),('用户确认','实际服务确认后才生版本')],
 [('集合','缺失处理'),('安全 / 流动性','BLOCKED或UNKNOWN'),('授权 / 证据','不得AUTO'),('支持动作','具体NOT_IMPLEMENTED')],
 [('时点','阶段语义'),('BEFORE_PAYMENT','硬付款前观察'),('AFTER_PAYMENT','付款后观察'),('AFTER_PRINCIPAL','本金实际可用后观察')],
 [('响应','保留原语义'),('PROJECTED','before / after / delta已计算'),('UNKNOWN','after / delta为null'),('current delta=0','只代表预览没有写事实')],
 [('规划数据','解释'),('Goal分母','当前原版本/属性/余额完整清单'),('income eligibility','确认/有效起点后的合格来源'),('projected allocation','计划，不是已归属')],
 [('动态请求','server核验对象'),('Goal / policy / model','实际原身份、版本、Evidence hash'),('epoch / key','当前期与同原请求'),('lookup dual hash','client六字段 / server完整request')],
 [('修复类型','确认边界'),('目标属性修改','原Goal/模型hash复核'),('资金回拨授权','专用原scope确认'),('真实回拨','具体effect、来源分与三腿')],
 [('产品约束','核验来源'),('期限 / delay / lock','原目录条款及版本'),('risk / minimum / amount','原用户范围与optimizer'),('maturity receipt','旧回款，不是新权限')],
 [('阶段','固定身份'),('prepare / confirm','原portfolio / whole hash'),('execute-next','expected batch + action'),('UNKNOWN','保原批/key，后批停')],
 [('恢复数据','原件要求'),('position / quote','原持仓与fee/loss/可用规则'),('deadline','服务器实际时刻及原上界'),('consent','确切用户与原effect')],
 [('世界状态','计入分母'),('可执行 / 拒绝','都保存原来源与拒因'),('UNKNOWN','不剔除，不填安全'),('candidate effect','内存候选，不继承旧确认')],
 [('问答命令','恢复对象'),('START','原session与exact start receipt'),('ANSWER / REFRESH','原revision、source、命令hash'),('CLOSE','旧历史保留，current为空')],
 [('对象','语义'),('original payload','历史immutable身份'),('current observation','当前run/trace/question proof'),('claim / ACK','一次副作用，终态不复活')],
 [('所属层','状态例'),('应用','PLANNED / UNKNOWN / SUCCEEDED'),('模拟银行','ACCEPTED / SETTLED / REJECTED'),('原回执','具体效果和投影核验')],
 [('验证范围','不得替代'),('EXACT / PREFIX','仅原指定链范围'),('LEGACY_UNAUDITED','不回填现代genesis'),('clean RRRO scope','不缓存跨请求授权')],
 [('报告字段','当前含义'),('difference','应用减bank，缺项null'),('MATCHED','当前范围账目相符'),('MANUAL_REVIEW_REQUIRED','只读诊断，没有修账')],
 [('主体/检查','未证明边界'),('local USER','模拟身份，非KYC'),('Ruff S告警','规则诊断，非全部漏洞结论'),('provider privacy','有限脱敏，非通用PII保护')],
 [('产物','状态边界'),('API / UI','合同/模块证明分列'),('CI config','未实际Actions，不填成功'),('offline / browser','必须当前image/source及原capture')],
 [('证据字段','统计要求'),('numerator / denominator','预登记完整机会/时点'),('raw_refs / run','原数据与代码/环境绑定'),('null / censored','缺失、零分母、未完成分别留存')],
 [('结果','可引用范围'),('E01 / E02','原定向PG范围，非全产品'),('E03','相关源改变，只diagnostic'),('E04 / W0','原失败保留 / n=1旧性能')],
 [('案例','后续核验'),('prepare500','当前OPEN固定serverclock专用'),('bank settled/app unknown','同key原投影协调'),('研究空白','0真人，工具不充人群证据')],
 [('下一依赖','当前状态'),('最终两版全量','未完成'),('实验/研究/录屏','分开取得原件'),('真实资金准入','未启用，机构独立评估')],
]
for p,rows in zip(packet['whitepaper'],tables,strict=True):p['table']=rows
packet['layout_revision']='READING_LAYOUT_V2_PROTOCOL_TABLES_NOT_NEW_RESULTS'
(base/'pages.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
(base/'proposal.md').write_text(markdown_pages('钱途有界：12页企划书当前功能版',packet['proposal']),encoding='utf8')
(base/'whitepaper.md').write_text(markdown_pages('钱途有界：30页技术白皮书当前功能版',packet['whitepaper']),encoding='utf8')
print('Reading tables authored; no numerical results added; first bytes retained')
