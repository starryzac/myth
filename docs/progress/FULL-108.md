# FULL-108 公共节日临时准备金候选

## 实现和验收状态

2026-10-05：功能优先执行修订二下交付有限只读生产功能，原 FULL-108 仍 PARTIAL。原计划410—420行：公共节日窗口＋过去相似本行消费生成临时准备金候选，展示依据并首次确认；不能因节日自动提高所有用户准备金。

GET `/api/v1/policy-suggestions/seasonal`，operationId `suggest_seasonal_reserve`。共享 router/READ ONLY 接线见 FULL-107。固定用户/服务器时钟，客户端仅选服务端真实 `window_id` 并收紧参数：lookback≤1096日（默认1096）、minimum_historical_windows≥2（默认2）、quantile_bps≥8000（默认8000）、essential_categories 为 food/transport/daily_necessities 的唯一子集、adjustment_cap≤500000分（默认500000）。不接受公共年表、日期、银行汇总、coverage、结果、now、权限或自动确认。

## 官方窗口来源和边界

2024—2026三份国务院办公厅原通知的有限人工结构化提取：

- [2026年：国办发明电〔2025〕7号，2025-11-04](https://www.beijing.gov.cn/zhengce/zhengcefagui/202511/t20251104_4258873.html)。官方有效转载全文已实际读取；中国政府网原路径 `https://www.gov.cn/zhengce/zhengceku/202511/content_7047091.htm` 当前工具请求403，未宣称获取原网页bytes。
- [2025年：国办发明电〔2024〕12号，2024-11-12，国务院公报2024年第33号第23页](https://www.gov.cn/gongbao/2024/issue_11726/material/gwygb202433.pdf)。官方搜索提取逐段核验了窗口，直接PDF请求403；发布日期同时核官方政府转载检索记录，不声称原PDF下载/bytes归档成功。
- [2024年：国办发明电〔2023〕7号，2023-10-25](https://www.beijing.gov.cn/fuwu/bmfw/sy/jrts/202310/t20231025_3286455.html)。官方全文已读取。

共20个精确通知窗口，每项返回year/start/end/notice_reference/notice_date/source_url、`OFFICIAL_NOTICE_MANUAL_EXTRACTION`。`calendar_extraction_hash` 仅是实际结构化窗口规范SHA，不是官方网页/PDF byteSHA。核验日期2026-10-05（UTC；交付时本地日期已为2026-10-06）；这是静态有限版本，不承诺以后自动更新。业务参考日期早于通知发布日期时不提供未来通知知识；无2027年通知，2027选择明确 UNKNOWN。2024元旦/端午通知仅明确当天放假，未自行扩周末。2025中秋国庆合并保留一个组合窗口，不拆出两份假历史，不与2026独立中秋/国庆当同节日比较。只支持中国 Asia/Shanghai 日期合同；其他时区UNKNOWN。

## 金额和来源算法

每次 fresh 读取当前用户原全交易/账户/证据，调用原 coverage verifier 完整复算原证书整个周期；即使服务仅请求最近闭日校验，也没有省略证书全交易/账户scope。只对同 holiday_code、已结束、lookback内、全账户完整覆盖的官方旧窗口计算；每个缺窗口、缺类别、非完整日期单列 MISSING 原因，不能用空数组当零。历史长度不足 minimum_historical_windows 时 `INSUFFICIENT_HISTORY`，金额/rank/candidate 均 null。

每个节日以其前置等长普通日期窗口为比较基线，基线碰到另一已知公共节日则不比较。银行经济角色必须原 CONSUMPTION/借方；类别必须有唯一、有效、hash/owner/time匹配的原 USER_DECLARED 确认。只求选定已确认必要类别；包含真实一次性必要消费，不用可编辑 one_off 标志删除历史。计算 `max(0,节日总额-基线总额)/原节日日数` 的精确整分分数，按目标剩余本地天数向上取整；对完整旧窗口的结果做最近秩 `ceil(quantile_bps*n/10000)`，无概率、无LLM推测金额。已开始但未结束的目标保留完整官方原窗口，候选仅从今天到其结束，过去天数不再保护。

返回每个旧窗口/基线、原交易IDs、消费与用户类别确认原源、覆盖原证据、规范输入/source digest、未截断 required_adjustment_cents。cap更紧时同时给 proposed_adjustment_cents 和 cap_limited，不把被cap截断金额称全部需求。完整可计算才构造原 `SeasonalReservePolicy` Schema候选，`advice_only=true`/`requires_confirmation=true`；未采纳时 `hard_protection_changed=false`/`bank_authority=false`。

READY仅表示该有限建议字段完整且有规范覆盖来源；不是全链审计/独立经济效果或银行授权。完整且零交易的原证书可证明零消费差额；缺原件没有该结论。参数提高minimum或缩lookback可能得到不足历史，不能缩小未覆盖分母。

## 检查和未覆盖

直接checks/原RED/实际PG候选与 FULL-107 共用 `.runtime/FULL-107-108-patterns`。纯手算两春节窗口800/2400及800前置基线，九日目标得到900/1800、q=4/5取1800；使用真实ORM/evidence/coverage形状的 in-memory来源桥正例同值，缺证/foreign/future/类别冲突/删行负例均保null；这些是模块测试，不是实际银行实验成功。当前原seed仅60天，不具两个旧同节日窗口，实际PG必须验证不足而非伪造历史成功。根唯一PG执行前 NOT_RUN。

尚未将候选持久确认/编译/UI采纳与 FullSeasonal 原生命周期连接；没有修改硬保护或金融执行。未支持用户旅行/活动声明的实际持久窗口、更多年份/地区日历、真人旅行/研究、完整FULL验收。未知日历和缺历史仍留缺口，不按运行结果补年表或调阈值。原 FULL-108 不关闭。

## 2026-10-05 UTC 真实必要节点追加（原批次失败保留）

最终135相关纯/API风险 PASS3.40s；七文件 strict mypy、Ruff、format --check PASS。实际 PG 仅collection：1 test collected1.74s，尚未运行。原首次类型9错、第二类型1错和格式前Ruff RED日志保持。运行命令、当前原源码bytes、精确SHA和日志归档见 `.runtime/FULL-107-108-patterns` 的 final-source manifest。原编号仍PARTIAL；这些检查不构成全量验收或金融实证。
