# FULL 有限实验核心合同（显式差量，2026-10-06）

本包实现可调用的七种确定性提案机制及八种有限机制消融、外部供给案例原件的作者合同、已有 MVP 数字的窄引用准入。所有函数均无数据库/网络/银行副作用，不产生实际运行结果或授权。原 MVP 24 输入、五臂运行器、旧失败、历史与哈希不改。FULL-804/805/808 全部仍 PENDING。

## 实际接口

- `app.domain.full_experiment_mechanisms.decide_full_mechanism(MechanismInput)`：受信私有服务构造当前源，输入完整原件引用、当前现金/占用/保护、候选条款/权限/版本、有限世界与问题。原件 SHA 和 CURRENT_COMPLETE 是调用合同，不是这个纯函数对数据库真实性的认证。Root 实际适配器必须逐次验真。
- `app.domain.full_experiment_cases.inspect_full_case_design(FullCaseDesign)`：读取外部实际供给的 INPUT 原 UTF-8 字节及 SHA、完整八类作者真值，登记整族 split 和机制拓扑。只返回作者完整度；不生成案例、不认证真人、不冻结、不执行。
- `scripts.full_experiment_claims.admit_existing_mvp_claim(ExistingMvpClaim)`：仅固定原 MVP 14 指标三个实际读取器 `observe`，核原指标定义/单位、whole run/case/arm、原文件 SHA、当前实际已载入函数源码，并重新计算整个输出。JSON 类型精确匹配，`true` 不等于 `1`。仅 MEASURED 有限标量可引用；零分母/缺失/结构值拒绝。TOOL_TEST_ONLY 返回工具数值但准入 false；有效原 MVP SERVICE_INTEGRATION 仍仅 MVP_ONLY，永不当 FULL 实验效果或全量验收。缺实际记录时 MISSING，不创建记录或补数字。

## 机制差量

B0 读取原人工候选，逐次确认；B1 金额为已到账现金减占用再减固定阈值；B2 减占用及登记静态预算，固定产品；B3 每次金融动作要求单次确认；B4 原来源绑定的模型置信度达到阈值才选；B5 在其它约束相同条件下收益优先，明确不作流动性过滤；P 检查证据/当前版本/金额上限/产权/硬保护/流动性，稳定世界不问，不稳定选 minimax 问题。B1/B2 仅 GENERAL PURCHASE；其余机会显式机制跳过但保留分母。B4 可以提议不安全候选，输出 noauthority，实际公共银行门不得被绕过。

收益排序只按原条款整数简单计息 `amount*bps*days//(10000*365)-fee-loss`，是候选机制目标，不是实际收益指标。没有客户端净收益输入。调整金额时原 effect hash 清除，必须正式 fresh prepare。有限当前候选池不是完整 Full 全动作集合或年度多目标求解。

八消融实际改变证据、版本、动态生活储备、其它目标、流动性、最小询问、安全恢复及实验决策侧链的对应分支。AUDIT_CHAIN 只移除新实验决策记录，**不删除原生产金融审计**；真正完整金融审计消融还未接。SAFE_RECOVERY 保留原 UNKNOWN 身份，停止而非换键重扣。所有输出永久为 `execution_status=NOT_RUN`、`runtime_adapter_status=NOT_CONNECTED`、`bank_authority=false`、实际指标 null。跳过/UNKNOWN 不删分母。

## 案例与真值边界

至少50族、多变体、16类、全族 DEV/VALIDATION/FROZEN 隔离来自原完整计划802–838。每变体八真值为义务/权限/调整范围/产品属性/允许动作/等级/冲突/恢复，作者描述和独立重放规范明确区分。纯检查里的50条 SNAPSHOT 长度夹具仅 TOOL_TEST_ONLY，**不是50真实实验族**。截至本包冻结，case 模块只是 supplied authoring contract，尚无实际50族输入、独立审核八真值、正式冻结或 Full 七臂服务连接。下一包是实际作者数据，不是另建框架。

FULL 的28指标处在新 profile FULL 命名空间 S1–S9/E1–E8/F1–F5/A1–A6，与旧 MVP 同名码的含义不可混用。全部 null/NOT_RUN；未实现独立 Full28公式、350真实结果和8消融结果。原初版14工具可复用部分原件读取，不能据文件存在叫 Full指标已完成。

## Root 必接的实际生产接缝

1. 受信当前 RRRO builder：完整本金可用日期/原 immutable 产品/Full策略与专用银行scope/确认/真实income fragments/目标与占用/全年保护；任何不完整 UNKNOWN。
2. 七臂及八消融原登记 → 每实际 opportunity 的私有 selector：旧 `experiment_arms`/registry/arm executor 仅 B0–B3/P、GENERAL purchase、amount+cash uses；不得偷偷扩大原固定合同或把 B4/B5 叫已接。
3. 新金额/产品/目标必须原 fresh effect、ASK/确认、资源预留、实际独立bank接收/查询/receipt；旧 UNKNOWN 保原 action/key。
4. 原21 Scenario kinds 无 FullPolicy/FullGoal/Joint/Seasonal/payment-relation/asset/recovery/release dispatcher，作者数据需相应实际 DTO bridge；只声明新 kind 不足。
5. 八真值独立审核及完整源/种子/族隔离登记；实际50族×7与消融、全部28原指标集中实验最后运行。数值不反调输入。

## 已运行与原失败

核心4源：49纯风险 PASS 1.21s，strict4/Ruff4 PASS，原首静态长行失败及精确源保留；MVP数值门2源：最终12纯风险 PASS1.26s、strict2/Ruff2 PASS，首轮11 UUID fixture错误/命名空间type RED/import static RED 保留。均非金融运行、非研究、非版本验收。各原件见三个进度文件的路径。
