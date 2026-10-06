# FULL-703 / FULL-105：完整版策略生命周期交互增量

root集成增量（2026-10-05）：主App已接全局recoverFullPolicyOperation/useFullPolicyOperation、跨页面和刷新后的金融写门、FullPoliciesPanel；旧目标/策略/Demo动作与重置被未决FULL请求阻挡，同族原键只读恢复及实际Demo GET保持可用。原App15+新增1与Demo11+新增1两个模块28PASS7.90s/wrapper9.72696s/all+scope稳定，types/lint exit0。原 manifest 为 evidence/W6/app-full-original-request-demo-write-gate-modules-20261005T142757Z-47bd2974；types142749Z-93244f12/lint142750Z-c3613534。下面原“root尚需接App”保留为交付时历史；本增量已接，不表示真实浏览器/最终金融执行验收。终局拒绝原键合同、未来差分和完整动作依赖缺口保留。

2026-10-05，功能状态 `FUNCTIONAL_INCREMENT_IMPLEMENTED / PRODUCT_ACCEPTANCE_PENDING`。原需求追踪表 FULL-703、FULL-105 仍为 PENDING；本批不修改追踪状态、正式模拟历史或原失败证据。依用户“功能优先、最后集中验收”的显式修订推进。

原要求：`钱途有界_完整开发计划_Codex执行版.md:1372–1374、1029–1037`（生命周期、版本、影响预览及关系）；`:1192–1194`（修改前自主资金、目标、持仓、未来动作影响）。追踪表原验收要求见 `docs/spec/requirements-traceability.md:98、59`。原策略地图和MVP生命周期保留；本批独立面板不覆盖旧页面。

## 可运行能力及实际接口

- `FullPoliciesPanel` 读实际 `/api/v1/full-policies`、详情、完整 `/versions` 和 `/commands`。显示持久/有效状态、原审计周期、原版本及hash、有效期、引用有效性、确认摘要、历史筛选、原命令及前序回执hash。缺所选版本显示 UNKNOWN；历史材料不回落当前详情。原响应文本另存，仅结构化筛选视图为派生展示。
- 目录读实际12模板及 FULL_V1 JSON Schema，逐字段JSON候选由 `/policy-templates/validate` 规范化并返回hash；校验没有权限。8类新FULL模板允许独立确认，3类旧MVP保留既存策略入口，LongTermGoal保留目标双hash入口；不把候选目录当作12类均已独立金融执行。
- CREATE、CHANGE、SUSPEND、REVOKE、RESUME 均调用原生产接口。新策略和修改使用服务器规范化配置与已复核hash，明确用户接受和理由；修改绑定原预期版本；暂停/撤销不添加DTO不存在的grant/金额/accepted/configuration；恢复沿当前原配置hash明确重新确认并创建新版本。
- 修改前调用原服务器只读 `/change-preview`，展示真实当前边界、实际引用、changed_fields与候选hash。三个候选金额变化是 `null / NOT_IMPLEMENTED`，没有显示成0；未来行动依赖仍未实现。不用这份预览声称 FULL-105 完整财务差分已计算。
- 原完整body_json、path、kind、键、原版本/周期和复核hash在POST前写入 sessionStorage。组件重新加载只恢复记录；无自动POST或重试。响应丢失和解析失败保留原请求；手动恢复仅重放同一body和键。连HTTP成功也保留pending，需用户只读按原键核对。
- 只读恢复 GET `/full-policies/commands/by-key/{encodeURIComponent(originalKey)}` 允许完整含斜线键，无query。NOT_FOUND 的 `not_found_is_final=false` 保持pending；RECORDED须原封套/body/key/kind/policy/requesthash/命令及回执身份完整匹配才删除恢复记录。服务是owner和原命令链校验来源；客户端只核响应合同和原请求匹配，不把本地记录/展示hash当独立金融验真。
- 新写入同时检查本族pending/busy/storage错误、实际writeflight及恢复后的旧demo请求。坏session、保存/删除失败锁住新写。全局App还需接hook（下面合同），确保其他页面在本族pending期间不继续写资金。
- 所有FULL回执 `bank_authority=false`、`receipt_is_current_authority=false`；专用审计事件和FULL执行适配器未实现。当前权限取决于新读原策略，原回执和历史确认不是当前资金授权。

## 文件与父组件接入合同

本批新增：

- `apps/web/src/components/FullPoliciesPanel.tsx` 与其测试。
- `apps/web/src/api/full-policies.ts` 与其测试（生成合同由root负责）。
- `apps/web/src/features/full-policy-operation.ts` 与其测试：必要生产原请求恢复门。
- `apps/web/src/tests/full-policy-fixture.ts`：显式 SYNTHETIC_HTTP_FIXTURE_ONLY，非真实金融结果。
- `apps/web/src/styles.css` 仅追加本面板手机堆叠、长ID换行、键盘focus与标签样式。

面板 props 为 `{ blocked?: boolean }`，无owner/金额/权限props。root在原策略资金fieldset外放 `<FullPoliciesPanel blocked={otherFamilyBlocked} />`；blocked仅传其他族demo pending/busy/storage错误或writeflight，不能把本族pending本身作为blocked，否则无法手动原请求恢复。只读lookup仍可用；blocked禁止新写与重放。

App挂载时调用 `recoverFullPolicyOperation()` 并订阅 `useFullPolicyOperation()`（snapshot `{pending,busy,storage_error}`），将本族恢复门加入其他资金写入入口。只订阅但不挂载恢复函数，会在尚未访问策略面板的刷新场景漏读session。旧demo/sharedpending/http和App由root保持单一集成，不在本批修改。

原始样式及首次失败源码完整字节留在 `.runtime/FULL-105-703-policies-20261005T134808Z/`；最终源清单与HANDOFF同目录。本批没有迁移、种子、银行或金融API改动。

## 已运行的模块检查

使用原 `scripts/run_scoped_check.py --task W6` 保存实际argv、源前后SHA、退出码、耗时和原日志。最终三条全部exit0、全source/scoped稳定：

| 实际命令 | 实际结果 | 原manifest |
|---|---|---|
| `pnpm.cmd --filter @bounded-funds/web typecheck` | 退出0 | `evidence/W6/full105-703-policy-types-final-20261005T141603Z-0f212384/manifest.json` |
| `pnpm.cmd --filter @bounded-funds/web exec eslint` 本批7 TS/TSX文件 `--max-warnings 0` | 退出0 / 3.878294s | `evidence/W6/full105-703-policy-lint-final-20261005T141604Z-30a65fee/manifest.json` |
| `pnpm.cmd --filter @bounded-funds/web exec vitest run src/api/full-policies.test.ts src/features/full-policy-operation.test.ts src/components/FullPoliciesPanel.test.tsx` | 42 PASS / Vitest4.71s / 原wrapper6.520445s | `evidence/W6/full105-703-policy-vitest-final-20261005T141609Z-350b51f9/manifest.json` |

42是8个reader、24个原请求恢复、10个组件测试的真实不同分母；不和以前的39或其他页面结果相加。风险覆盖：原键/body完整重放、NOT_FOUND保留、错误键/body/kind/owner格式/周期/版本/hash/时间/序号拒绝清除、坏session与存储失败、跨族旧请求恢复、无自动POST、明确复核/编辑后丢弃候选、历史缺版本UNKNOWN、金融null及权限false。

第一次typecheck因纯测试fixture将可选previous_command_hash赋必填字段而失败；首次Vitest有2个测试断言错误（同步拒绝误写rejects、开着创建编辑器时按钮已经变“收起”），原日志及原source均保留。没有缩减金融/身份断言。r2全部39 PASS；补跨页面恢复门与缺版本UNKNOWN后的最终42 PASS是当前交付源。本批只有本地HTTP夹具，无PG/Edge/全量或产品真实成功的声明。

## 尚存限制与下一前置

- root尚需在最终App/PolicyCenter源接全局门、面板，并在最终产品源实际运行创建→修改→暂停→恢复→撤销、原响应丢失/按键恢复和版本筛选浏览器链。本批合成fixture不代替这些实际金融路径。
- FULL候选自主资金、目标分配、持仓本金和未来动作差分仍未实现；空行动依赖数组不是已覆盖全部执行风险。现独立FULL策略不能生成银行权限或金融动作，FULL-105/703整体不关闭。
- 独立FULL策略尚未并入原MVP关系图；此面板显示实际引用和原JSON，没有伪造跨模板依赖边。需真实完整依赖响应/动作依赖合同后再整合地图与多期影响。
- 持久回执链和版本链由原服务校验；本前端检查结构/链接和原请求匹配，不重新计算服务器canonical hash，也不执行独立银行账本验真。多个GET不是一份RR事务快照。
- `NOT_FOUND`没有终局语义：即使原请求被拒绝且未持久化，也不能本地“放弃并换新键”。该情况会保持锁定；安全释放需要后续实际最终拒绝记录合同，不能凭HTTP错误或本地按钮伪造。
- JSON编辑器依赖服务器Schema及跨字段校验；未提供12类逐字段向导。列表/历史reader的10,000条安全容量超出时拒绝显示为成功，未删原分母。手机/键盘样式只覆盖本面板，真实可访问性及全产品体验验收未运行。

原FULL编号和原验收内容保留；后续实际产品验证仍由root统一调度。
