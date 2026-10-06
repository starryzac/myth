# FULL-507 当前观察前端消费者

功能差量已实现；原 FULL-507 仍 PENDING。使用 Root 从真实 Main 生成的 `InterventionView` / `CurrentQuestionObservation`，不另造接口、后端语义或审计计算器，不增加问答/两条 Trace 的重复 GET。

## 可运行行为

`api/interventions.ts` 严格消费当前来源绑定：ORIGINAL_MESSAGE 要原题目与原 payload 一致；CURRENT_OBSERVATION 要完整新观察、原 OBSERVE 回执、owner/epoch/message/payload/key/envelope/body/session/revision/source run/hash 全部对齐。新问题可以有不同 UUID、不同会话，其有限题目选择与影响字段仍须一致。原 OBSERVE 命令按已有实际 canonical JSON / SHA256 验证；原消息 payload hash 继续只对不可变历史消息计算，不替换成当前问题。semantic_key 同原消息，并明确是服务器完整世界语义核验的报告，浏览器不声称独立金融/审计验真。未知或缺新 proof 不认作 CURRENT。

LEGACY_TERMINAL_SOURCE 必须保持 stored/effective INVALIDATED 且 pending=false；即使服务器核出有效观察，也不复活历史消息/Inbox，不提供领取、收阅或可操作问题。UNKNOWN/STALE/ARCHIVED 不显示可提交的当前问题。原 claim/ACK、原完整请求保存与按原键恢复门保持，不增加自动投递、回答、确认或重试。

`InterventionCenterPage` 仍使用 `mutationBlocked?:boolean`；本批无 App 接口变化。独立展示原 epoch/session/revision/run/trace/payload hash，以及新观察 run/hash/session/revision/source run/hash/原键/request hash/服务端 semantic key。历史终态观察使用独立标签，当前问题入口要求当前有效且 pending；页面提示到问答页核对实际会话与版本后再回答，不把历史 session 替换成新页权限。

`api/local-actor.ts` 类型窄改为生成的 `LocalSessionResponse` 与 principal.role；运行行为未改变，原42直接检查仍可复用，类型检查包含面板及原测试/夹具。本批未读取、生成或输出 Root 配置的任何凭证。

## 源文件与保全

修改六源：`api/interventions.ts`、其直接测试、`pages/InterventionCenterPage.tsx`、其直接测试、`tests/intervention-fixture.ts`、`api/local-actor.ts`。新增 fixture 仍明确 TOOL_ONLY：只验证真实 DTO 检查器与 HTTP 恢复逻辑，不是服务端实际执行、浏览器呈现或真人已见实证。

精确修改前六源保存在 `.runtime/FULL-507-current-proof-ui-before-20261005T205301Z-9431051b/manifest.json`（SHA ee13ea69c8745de5ad0f0310cc97cdfd2dc47fc04084129ff7363899c28c5440）。原失败、原观察消息/receipt/hash、原存储 operation 测试与正式历史保留。本批没有修改 API 金融源、Main/deps/App/共享 HTTP/generated 合同。

## 已运行的检查

全部通过现有 scoped runner 保存原 argv、日志、退出码、源 before/after 和 SHA：

| 检查 | 原目录 W5 | 结果与范围 |
|---|---|---|
| reader48 + 原 operation6 + page9 | intervention-current-proof-ui-first-direct-20261005T205645Z-98f03702 | 63 PASS / 5.95s，wrapper7.112978s；global/scoped均稳定 |
| 六修改源与 local面板/测试依赖 strict TypeScript | intervention-current-proof-ui-first-types-20261005T205623Z-03214dbf | exit0；scoped稳定；两个独立后端文件变更，global=false |
| 六修改源 ESLint | intervention-current-proof-ui-first-static-20261005T205623Z-eb7d84a9 | exit0；scoped稳定；同两独立后端源变化，global=false |
| 历史终态标签最后增量3节点 | intervention-current-proof-terminal-label-direct-20261005T205812Z-9bcc2ebc | 3 PASS / 2.94s，6未选；不把未选称通过，与前63有重叠 |
| 两页面源最后 strict types / lint | intervention-current-proof-terminal-label-types-20261005T205753Z-58c234fd；intervention-current-proof-terminal-label-static-20261005T205753Z-335f7e4f | exit0，完整稳定性见各原manifest |

原63通过后仅把终态观察 aria 标签从“当前”改为“历史终态”并增加对应直接断言；未改变 API reader、原操作恢复门或数值。选择受影响3节点复核，其余未变结果复用，不宣称66个独立用例或全量成功。

## 未覆盖与下一前置

- 本批未运行真实 PG、浏览器、cookie 登录、观察 producer、投递、收阅或真人呈现；Root 已运行的后台结果必须按其独立原 manifest 记录，不能由本前端 fixture 升格。
- 完整审计/世界语义验真属于服务器；前端只核 DTO 内部绑定与原命令 SHA，不声称观察 trace hash 为浏览器重算结果。
- 原问答页面链接仅导航，必须再次核对真实 session/revision；不会把观察原件当答案或授予任何银行/执行权限。
- 全局边界订阅仍 NOT_IMPLEMENTED；边界单动作原入口不代表完整全局动作集合。
- 原编号仍 PENDING；Root 接线及最终代码真实浏览器/原完整验收继续集中推进。
