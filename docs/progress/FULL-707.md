# FULL-707 场景模拟器首包

状态：`IMPLEMENTED_BOUNDED_READONLY_COUNTERFACTUAL`；原任务仍 **PENDING**。原要求来自需求追踪表第102行及完整计划1388–1390：可改事实、策略、产品参数，由引擎生成结果，不能直接篡改结果。此首包未代替完整版验收。

已实现独立只读场景页和服务端计算，不修改旧 DemoConsole。GET `/api/v1/scenario-simulation/context` 读取实际固定模拟用户、当前 epoch、当前咨询的原证据/策略/产品/持仓及审计状态，输出90/365原边界和实际可选实体。POST `/compare` 仅接严格参数，重新读取当前同一 RR/READ ONLY 快照，以原 `compute_boundary` / `compute_policy_change_boundary` 同次计算原基线与假设。客户端不能提供用户、时钟、结果、回执、授权或reset。

参数范围：

| 类别 | 可改范围 | 保全边界 |
| --- | --- | --- |
| 事实 | 一个有原银行证据且未关联目标归属的普通CASH账户，delta ±10,000,000整数分，假设余额不得负 | 假设内部不复用实际银行证据证明新余额；不写事实，不称到账收入 |
| 策略 | 一个原边界载入、有原确认证据的 emergency_buffer，amount 0–10,000,000整数分 | 原版本/配置hash绑定，只建立原PolicyChangeAssumption，不创建proposal/版本/确认 |
| 产品 | 一个原固定本金返还条款产品，假设新资金的term/settlement delay各0–365，总≤365 | 更短或更长均是假设；原产品条款、持仓 principal_available_at 不改，假设条款摘要单独标HYPOTHETICAL |

原来源缺失、审计不完整继续保留 `INSUFFICIENT_EVIDENCE/null`，乐观参数不补造成功。金额按整数分精确显示，273/1098点完整三阶段分母核对。产品参数可能只改变占用上限，也可能因当前约束无数值变化；无变化明确说明，不能称收益。页面只展示服务端原HTTP结果，没有前端金额引擎。

原epoch、当前源fingerprint及15项实际引擎文件SHA重新核对；源fingerprint保留本地日、完整原事实/策略版本/持仓/产品与咨询证据的content/hash/status/window，只排除重算时点和生活估计重算摘要。引擎摘要在计算前后核相同，原审计每次重新验证，无跨请求授权缓存。

本页参数修改隐藏旧结果；主动刷新原快照后清本页参数，旧请求不能自动重新绑定新epoch/source/engine。显式重放同原body，只读重新计算，没有自动POST或重试。重置按钮只清本页，不调用正式reset/seed/银行操作。全部 `grants_authority/executes_funds/writes_facts/resets_history/receipt_verified/economic_verified=false`、`execution=NOT_IMPLEMENTED`、未来收入纳入0。

文件：新 domain/service/router `scenario_simulation.py`，直接Python测试，Web `api/scenario-simulation.ts`、`pages/ScenarioSimulationPage.tsx`、两模块测试及明确 `SYNTHETIC_HTTP_ONLY` fixture。Main、dependencies、App、共享生成Schema由Root集成；本包未修改共享入口、旧控台、历史或原失败原件。建议导航 `#simulation` / “场景模拟器”，页面无props。

实际模块检查（证据位于 `docs/progress/evidence/W6`）：

| 原命令范围 | 实际结果 | 原件目录 |
| --- | --- | --- |
| pytest `apps/api/app/tests/test_scenario_simulation.py -q -p no:cacheprovider` | 34 PASS / 1.85s，wrapper3.187627s、exit0、all/scope稳定；一个既存Starlette/httpx弃用warning | scenario-simulation-backend-final-direct-20261005T181352Z-17de60cb |
| strict mypy4新源 | PASS/exit0、all/scope稳定 | scenario-simulation-backend-final-types-20261005T181352Z-edc9169a |
| Ruff4新源 | PASS/exit0、all/scope稳定 | scenario-simulation-backend-final-static-20261005T181413Z-aee0f694 |
| Vitest reader+page2模块 | 45+6=51 PASS /3.60s，wrapper5.439683s、exit0、all/scope稳定 | scenario-simulation-web-direct-20261005T182152Z-6afb3fba |
| 全Web tsc | PASS/exit0、wrapper12.814819s、all/scope稳定 | scenario-simulation-web-types-20261005T182142Z-2afa9455 |
| ESLint5新源 | PASS/exit0、wrapper4.304080s、all/scope稳定 | scenario-simulation-web-static-20261005T182142Z-379dba0f |

纯风险覆盖：原域引擎相同输入重放/无原件突变、三参数复算、原held本金无返还不因假产品期限变现金、365完整三阶段、UNKNOWN不补0、epoch/source/engine错配拒绝、外部实体/版本/目标账户拒绝、数字小界限、extra字段注入拒绝、实际router注入422/空query、dirty/nonRR/nonRO事务在金融读取前拒绝；Web原body/hash/身份/版本/参数元数据、金额关系/完整曲线/null、主动刷新/同body重放/本页reset及不自动提交。所有夹具是合成检查，不是银行、PG、Edge或产品效果实证。

原失败保留：第一Python域测试 `scenario-simulation-domain-direct-20261005T180947Z-64ae4dcf` 为5 FAILED/25 PASS（新夹具漏信用卡账户、错误赋值冻结context）；first types `scenario-simulation-backend-types-20261005T180948Z-aff9d3d3` 为FAILED。修复只在新夹具，后 `scenario-simulation-domain-repaired-20261005T181107Z-5f569bb0` 30 PASS，追加直接风险后为34 PASS；未将旧报告重标。

具体未覆盖：

- Root生产路由/RRRO入口和主导航集成、真实PG请求（全表前后不变/tenant权限/epoch变化/正式历史保全）、实际浏览器操作与完整版本验收仍待完成。本包未运行PG/浏览器/全量。
- 当前事实只普通现金delta，策略只原MVP应急缓冲；尚不支持账单、未来收入、12类FULL配置的反事实变化。
- 产品只假设占用期限。原boundary不含 lock/risk/完整optimizer适配；不可把较短假设期限当原产品可购性、真实返还承诺、报价或收益。原 minimum_purchase 未被此边界函数消费，不声称调整其值已改变规划。无完整组合优化、未来借记/收益/损失或执行能力。
- 当前审计卡是原服务本次报告，浏览器未独立重算审计链。源清单明确为本引擎15文件，不宣称全部仓库HEAD代码独立验真。基线、假设都是当前来源上的条件曲线，未来点不是已结算现金。
- 原编号保留PENDING；其他页面、性能、真人研究及全产品可访问性不由此包关闭。

下一前置：Root接入 GET和POST 的RRRO、注册router并生成真实合同后，以owned独立测试库验证无金融写入和权限边界，再集中实际浏览器/最终验收。低负担模块检查已完成，不重复全量。


## 请求 UUID 的窄修订（2026-10-05 18:44 UTC）

原9源 FINAL `.runtime/FULL-707-simulation-final-20261005T182726Z-af5bb456/manifest.json` 与全部旧检查保存不变。Root审查发现 `BoundaryModel(strict=True)` 的裸UUID不能接FastAPI解析后的JSON字符串；此前34项只有非法请求422检查，不能据此证明合法HTTP body已到计算。此次仅5个请求UUID字段改为原 `UUIDReference`，响应模型、严格bool/整数金额/extra、来源hash、历史、服务及router不变。

新增真实FastAPI GET context→JSON字符串UUID的POST compare，调用原 `compare_current_scenario`、`finalize_financial_context` 和确定性引擎；仅金融来源载入、审计读取和RR/RO session使用明确SYNTHETIC doubles，没有PG/银行实证。完整结果、原请求hash、当前真实引擎源hash、原件不变和三参数金额均验证；坏UUID、bool/字符串金额、嵌套回执、伪结果及query额外字段仍422且不进入来源读取。

首轮 `scenario-simulation-json-uuid-direct-20261005T184411Z-7910da96` 保留为34 PASS/1 FAILED（实际GET及POST均200，新增期望漏原finalize生成的source_digest）；修正期望以原finalize函数生成后，新 `scenario-simulation-json-uuid-repaired-direct-20261005T184457Z-822d7502` 为35 PASS/1.99s。`scenario-simulation-json-uuid-repaired-types-20261005T184458Z-710928bb` strict4 PASS，`scenario-simulation-json-uuid-repaired-static-20261005T184458Z-7d0f1c4a` Ruff2 PASS，均实际exit0且all/scope稳定。旧首轮types/static也保留，不重标失败。

本次5个Web源字节不变，前述51项Web模块/type/lint结果按原SHA复用，不重复运行。真实生产RRRO入口、owned PG完整快照保全和浏览器仍NOT_RUN；编号仍PENDING。
