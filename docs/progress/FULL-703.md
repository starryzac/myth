# FULL-703 策略地图：当前合同的功能增量

2026-10-05。状态：`FUNCTIONAL_INCREMENT_IMPLEMENTED / ACCEPTANCE_PENDING`。原需求仍为 PENDING，本次未修改追踪表、STATUS 或正式模拟历史。

原要求见 `钱途有界_完整开发计划_Codex执行版.md:1029–1037、1372–1374`：时间轴、依赖图、生效与到期、动作依赖、修改影响、版本历史和撤销；验收追踪见 `docs/spec/requirements-traceability.md:72项 / FULL-703`。执行次序按用户“功能优先、最后集中验收”的显式修订，不因可复用文件而关闭任务。

## 已实现的可运行能力

- 策略中心新增策略地图，直接展示 `GET /policies` 返回的当前版本、有效状态、授权条件和到期时点，可按名称/原编号和生命周期筛选。选中节点后读取真实 `GET /policies/{id}/versions`，以原版本号及确认、生效、到期时点绘制版本时间线；可筛选有确认时点的原件并选择历史配置。
- 有向关系只来自实际配置字段：`goal_saving.asset_policy_id` 指向资产授权策略；实际存在的 `must_not_reduce_policy_ids` 逐项指向明确保护策略；`asset_authorization.goal_id` 保留“目标归属”身份。后者不等于策略编号，当前接口缺映射时明确标为未解析。`priority` 的最低保护/降低/延期字段只展示本策略约束，不凭空生成依赖。
- 关系可选中查看原字段、来源版本和目标原编号。目标缺失、缺版本、错误类型、未生效、授权条件不足分别显示；坏引用和身份冲突不生成成功关系。历史配置的出边按所选原件显示，关联策略仍明确是当前读取版本。未取得所选历史版本时不回落为当前版本关系。
- 地图进入“修改当前策略并预览影响”调用原编辑器和服务端91日 `change-preview`，继续要求复核规范化配置、明确接受、原版本及同一重试键。原暂停/撤销复核、在途/UNKNOWN说明与版本历史入口保留。历史查看不恢复策略、不授予权限。
- 地图专用版本 reader 校验 policy_id 和版本ID/版本号唯一性；它使用独立的身份校验缓存键，普通历史缓存不能绕过此校验。该缓存仅用于阅读展示，实际金融执行仍由原后端即时核验。

主要文件：`apps/web/src/components/PolicyMap.tsx`、`components/policy-map-model.ts`及对应测试；`pages/PolicyCenterPage.tsx`及对应测试；`api/policies.ts`及对应测试；`src/styles.css`新增本页样式。金融API、模型、App、其他页面和正式记录未改。原修改前五个文件完整字节及SHA保存在 `.runtime/FULL-703-map-20261005T120323Z/source-before/`。

## 已运行的定向验证

使用现有 `scripts/run_scoped_check.py --task W6` 记录实际命令、源前后SHA、退出码和原日志：

| 命令范围 | 实际结果 | 原件 |
|---|---|---|
| `pnpm --filter @bounded-funds/web typecheck` | 退出0，最终全部源稳定 | `docs/progress/evidence/W6/full703-types-final-20261005T121553Z-0e7cfe73/manifest.json` |
| 8个相关 TS/TSX 的 `eslint ... --max-warnings 0` | 退出0，最终全部源稳定 | `docs/progress/evidence/W6/full703-lint-final-20261005T121554Z-2e143afa/manifest.json` |
| `vitest run` 页面、地图、模型、API四文件 | 17 PASS；范围内源稳定，另一个API测试文件由父任务并行变化 | `docs/progress/evidence/W6/full703-related-vitest-r2-20261005T121329Z-015fe17b/manifest.json` |
| 最后地图缓存身份隔离增量后，仅重跑地图/模型两文件 | 9 PASS，其中新增1项缓存拒绝；最终全部源稳定 | `docs/progress/evidence/W6/full703-map-risk-final-20261005T121558Z-212cf517/manifest.json` |

本批相关测试共18个不同测试已通过；17和9不能相加称26个不同测试。HTTP夹具是组件/reader单测，不是PG、银行、Edge或资金效果实测。首次类型检查的空值闭包失败、首次Vitest的sandbox `spawn EPERM` 原件均保留；Vitest仅在获自动批准的窄范围提升后运行，没有弱化断言。

## 未覆盖项和依赖

- 当前接口没有完整“哪些动作依赖所选策略”的查询合同，地图明确未提供动作清单；也没有目标/持仓/安全恢复的完整版综合影响响应。原91日财务预览保留，不能称完成365日影响或所有执行风险展示。
- 新12模板的持久化/Schema/生命周期由后台负责人推进；本页未知类型保留原类型和完整JSON，未在本批扩写新模板编辑表单或虚构字段。`must_not_reduce_policy_ids` 当前若不存在就不画边，不能称原计划所有保护依赖已有真实业务证明。
- 策略列表和版本分别读取，缺少同一事务时间点的完整依赖快照。版本追溯摘要展示不等于执行了哈希链验证。
- 恢复生效、完整生命周期与动作依赖的一致性、关系图/预览的真实浏览器测试、最终产品全量验收尚未运行。下一步需冻结相应后台合同并在最终源上补上述真实集成和浏览器节点。

无数据库迁移或种子改动：本批是现有API的前端消费者。

## 2026-10-05 14:17 UTC 独立FULL生命周期前端增量

新增完整目录/Schema候选、8类策略原生命周期、只读修改预览、连续版本/命令、session原body/key持久门与手动by-key核对。完整实现、42个相关Vitest/类型/静态原件和具体缺口见 [FULL-703-lifecycle-ui](FULL-703-lifecycle-ui.md)。本段补充早前“新模板编辑尚未接入”的批次边界；原策略地图及失败原件完整保留。root尚需接App全局恢复门及面板，真实浏览器、FULL候选金融差分和动作依赖仍未验收，FULL-703不关闭。
