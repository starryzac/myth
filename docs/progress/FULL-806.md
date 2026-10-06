# FULL-806 青年用户研究工具

状态：**MATERIALS_AND_LOCAL_TOOL_DELIVERED / STUDY_NOT_STARTED**；原编号未关闭。匿名工具可运行，真人研究未开展，不能混为研究结果。

## 完成内容与文件

新增 `docs/research/README.md`、`成年模拟用户任务脚本.md`、`知情同意.md`、`匿名问卷.md`、`匿名记录工具合同.md`，以及单一`scripts/research_records.py`和直接测试。依据原完整计划913–941、1420–1422，保留计划18–24成年、合成账户、原六任务、全量确认/静态预算/钱途有界三条件及探索性结论边界。词语按合并产品规格/统一术语说明候选、权限、银行原件、UNKNOWN和人眼可见之间的区别。

## 关键选择

三条件顺序采用六种轮换的作者草案，首次研究前须冻结；没有从参与结果反调。记录未尝试/不支持/退出/未知完整分母，不填0或成功。默认不录音/录像，不收身份映射、真账号或登录凭证；匿名问卷保留拒答与缺失。概念理解评分和真实任务/信任校准分开，不把高信任评分当产品更安全。

## 验证与限制

材料先只读核对原需求/规格/术语；随后必要匿名工具定向运行。没有数据库、浏览器、招募、消息、真实用户回答、研究结果、机构审批、实际同意/计时或效果数字。知情说明中的实际负责人/联系/保存截止日待提供，不能把草稿直接作已批准招募材料。

命令通过原`run_scoped_check.py --task W7`绑定两源：

- `anonymous-research-owned-temp-direct-20261005T223853Z-25fbb62b`：32合成风险PASS/1.62s（wrapper2.7296s），成人同意/原key重放、3×6完整缺失分母、strict金额/评分/bool/未知字段、PII有限模式、原修正/撤回/完整记录校验。不是32名参与者。
- `anonymous-research-namespace-types-20261005T223847Z-34eed038`：`mypy --strict --explicit-package-bases`两源PASS；仅消除namespace重复映射，没有弱化类型。
- `anonymous-research-final-static-20261005T223847Z-7d08a24c` 与 `...final-format-20261005T223847Z-c903cc55`：两源Ruff/format PASS。
- `anonymous-research-cli-help-20261005T223737Z-386e0f7f`：实际CLI帮助exit0，script字节与最终一致。

这些最终proof均scoped/global stable=true。首次32测试setup因Windows临时目录AccessDenied失败、mypy重复namespace映射、Ruff导入空行RED保留 `.runtime/FULL-806-first-anonymous-red-retained-20261005T223845Z-87dc1ae5`；纯本地fixture改用授权临时目录，不运行真实记录/金融。原材料草稿 `.runtime/FULL-806-materials-draft-20261005T222241Z-82203d26` 完整保留。

三条件人类研究界面及六项任务实际前置尚未逐项验证；已有自动实验臂/软件风险测试不能直接更名为用户研究。材料中例题/评分说明仅作者规范，不是参加记录。工具仅记录操作者明确输入，不认证真人、伦理、数据真实性或全部PII。撤回会从导出排除，但私有原件未物理删除，实际删除须数据管理人员另处理。研究状态始终NOT_STARTED。

## 下一依赖

Root复核材料与研究实施信息；真实研究需独立取得合适成年人自愿同意、可用条件界面与隔离初态，在首次采集前冻结材料。不得由自动代理伪造参与者或发送未授权邀请。原编号关闭及材料/研究最终审查由Root集中处理。
