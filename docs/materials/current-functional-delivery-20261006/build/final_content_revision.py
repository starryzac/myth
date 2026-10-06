"""Preserve reading draft and make explicit Full experiment and demo-scope corrections."""
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from author_materials import markdown_pages

base=Path(__file__).resolve().parents[1];root=base.parents[2]
saved=base/'build/revision-2';saved.mkdir(exist_ok=False)
for name in ['pages.json','proposal.md','whitepaper.md','build/render_pdfs.py','build/render_deck.mjs','build/pdf-structure-reading.json']:
    dest=saved/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(base/name,dest)
packet=json.loads((base/'pages.json').read_text('utf8'))
packet['proposal'][2]['sections'][2]['text'] += ' 完整版还必须实际比较B4仅模型置信度与B5忽略流动性的收益优先优化器；当前二者没有正式效果结果。'
packet['proposal'][10]['sections'][1]['text'] = '初版24作者案例×B0—B3/P及14指标完整oracle结果未纳入验收。完整版另要求至少50族、每族多变体，B0—B5/P七机制及移除证据/版本/动态生活/多目标/流动性/最小问题/恢复/审计八消融；全部效果待实验。真人0，当前新性能未测，无效率、收益或理解改善结论。'
packet['proposal'][9]['sections'][0]['text'] += ' 当前Full证据图v2登记35张业务表并有31不同纯/HTTP检查，实际PG仍NOT_RUN；不是银行效果或完整审计集成证明。'
packet['proposal'][9]['refs'].append('S40')
packet['whitepaper'][26]['sections'][3]['text'] = '原FULL804要求至少50族、每族多变体与族级split/真值；FULL805要求B0—B5/P七机制及八消融，FULL807另核并发/重启/UNKNOWN/规模性能。初版24案不能替代Full范围。全部原结果未纳入验收，缺数据不画柱形、不写百分比、不作显著性结论。'
packet['whitepaper'][4]['sections'][3]['text'] += ' Full图v2新登记35表原件与typed链接，31不同纯/HTTP检查只说明合同；actual PG尚未运行，历史可变状态不能重建时保持null。'
packet['whitepaper'][4]['refs'].append('S40')
packet['whitepaper'][24]['sections'][3]['text'] = packet['whitepaper'][24]['sections'][3]['text'].replace('现security-check扫描Ruff S规则报告106项','已记录的security-check原run扫描Ruff S规则报告106项')
packet['slides'][7]['body'][2] = '初版24×5；FULL50族×7机制 / 八消融 / 真人 / 当前性能：待实验'
packet['slides'][8]['body'][2] = '原安全扫描106项告警；全量/渗透/手机实测均未完成'
packet['layout_revision']='FINAL_CONTENT_V3_FULL_SCOPE_EXPLICIT_NOT_NEW_RESULTS'
(base/'pages.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
(base/'proposal.md').write_text(markdown_pages('钱途有界：12页企划书当前功能版',packet['proposal']),encoding='utf8')
(base/'whitepaper.md').write_text(markdown_pages('钱途有界：30页技术白皮书当前功能版',packet['whitepaper']),encoding='utf8')
index=json.loads((base/'sources.json').read_text('utf8'));snapshot=datetime.now(UTC).isoformat()
for sid,name in [('S40','docs/progress/FULL-102.md'),('S41','钱途有界_初版开发计划_Codex执行版.md')]:
    raw=(root/name).read_bytes();dest=base/'source-originals'/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
    index['sources'].append({'id':sid,'repository_path':name,'absolute_original_path':str((root/name).resolve()),
        'captured_copy':dest.relative_to(base).as_posix(),'sha256':hashlib.sha256(raw).hexdigest(),'size_bytes':len(raw),
        'captured_at':snapshot,'source_type':'PUBLIC_SOURCE_DOCUMENT_OR_CODE','binding':'DOCUMENTARY_CAPTURE_ONLY_NOT_FULL_RUNTIME_FREEZE','original_status':None})
(base/'sources.json').write_text(json.dumps(index,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
print('Full 50-family/7-arm/8-ablation scope explicit; new graph remains PG_NOT_RUN')
