# FULL-505 / FULL-705 一次一问前端

状态：生产前端功能增量已实现；原编号仍 PENDING。执行依据为功能优先修订二。不是初版、完整版全量验收，也不是金融服务或浏览器实证。

## 实现和文件

新增 `apps/web/src/api/question-workflow.ts`、`features/one-question-operation.ts`、`pages/OneQuestionPage.tsx`，以及对应三个测试和 `tests/question-fixture.ts`。未修改 App、导航、其他 singleton、后台、生成合同、数据库、历史、原需求追踪状态或失败记录。使用现有 field / card / readonly 样式，没有改全局 CSS。

页面提供实际 START、读取会话、精确 ANSWER、主动 REFRESH、显式 CLOSE 与两个原键 GET。开始输入原尚未提交银行的准备行动 UUID 和完整有限变量，实际 user / epoch 来自只读账户、dashboard、demo 状态。页面不自动 prepare / confirm / execute 原资金行动，不自动发现候选。有限输入提供整数分示例，明确只是可修改输入；必须由用户主动提交。

reader 使用当前生成的 QuestionStartRequest、QuestionAnswerRequest、QuestionRefreshRequest、QuestionRevision、QuestionWorkflowResponse 和 QuestionCommandLookupResponse 外壳。验证原 owner / session / epoch / base action、revision / previous run、原变量唯一性、选项类型与证据来源、完整剩余世界与未计算分母、逐分配唯一性、经济签名集合、各 minimax 分区完整性和本轮最坏签名数。只显示顶层当前有效问题，UNKNOWN / STALE / ARCHIVED / CLOSED 不暴露旧回答按钮；CLOSED 保留历史 evaluation，不允许新的 fresh observation。第16轮仍可显式关闭并读取保留的第17轮关闭回执。

每轮逐世界后果以服务端原 JSON 展示，资金字段必须是可精确表示的整数分。本 reader 不独立重算原金融算法、经济签名或完整审计链，原服务核验仍是前置；“服务端报告完整”不能替代独立银行验真。一步 minimax 不等于整棵提问树总问题最少。

## 原请求持久与恢复

每个 POST 先保存 exact body / body_json / kind / path / stable key / user / epoch / base action / 变量 / previous run，以及原 command envelope 的 SHA-256。canonical 与真实 Python `configuration_hash` 对齐：UTF-8、排序键、无额外空白、禁止不精确数字。独立 TOOL_ONLY golden 保存于 `.runtime/FULL-505-705-question-ui-20261005T1647Z/canonical-golden.json`，实际 Python 函数所得 START SHA 为 `88252db764fd1c2f67631d379d0d8cf89cb956e65317914479531c30e72ece63`；没有伪造产品金融 hash。

HTTP 成功、网络中断、解析错误、4xx 均保留原请求。新键、不同答案、不同配置和不同操作不能绕过 pending。刷新只恢复原记录，不自动 POST；用户可明确手动重放完全相同的原请求。自身原键 GET 不受自身 pending / 其他族 mutationBlocked 的只读门阻挡。

- START：`GET /finite-planning/sessions/commands/{epoch}/by-start-key/{key}`。
- ANSWER / REFRESH / CLOSE：`GET /finite-planning/sessions/{session}/commands/by-key/{key}`。

只有 RECORDED 的 exact original_command / 原完整 body / request_hash / owner / epoch / key / session / 原 receipt revision / previous run 都匹配，才解除本请求门；current_revision 可以已推进，不能用 later receipt 替换原命令结果。NOT_FOUND_NOT_FINAL 不是金融未发生或终局拒绝，不清门、不换键。

匹配回执后独立保留 user / epoch / session 的只读定位引用，刷新重新 GET 当前服务状态。它不含授权，不作为当前问题、金融金额或结果缓存。存储坏数据、读取失败、保存失败、原记录删除失败都锁住新写入并保留原件。begin 同时检查 demo / full-policy / full-goal / onboarding 的 pending、busy、storage_error 和原 write-flight；App 还需把本族 gate 加入其全局门。

## 检查证据

日常只跑七新文件直接 lint、整体 Web 类型和三个相关 HTTP / React / store 模块，不跑 PG、浏览器、Docker或全量。

| 实际命令 | 原 W6 证据目录 | 结果 |
| --- | --- | --- |
| `pnpm.cmd --filter @bounded-funds/web exec vitest run src/api/question-workflow.test.ts src/features/one-question-operation.test.ts src/pages/OneQuestionPage.test.tsx` | `one-question-ui-final-resume-vitest-20261005T165452Z-6e512ef5` | 47 passed / 6.04s；wrapper 7.851909s；all / scope stable。 |
| `pnpm.cmd --filter @bounded-funds/web exec eslint` 加七新 TS/TSX 文件和 `--max-warnings 0` | `one-question-ui-final-resume-lint-20261005T165419Z-da180ce8` | exit 0；wrapper 3.811771s；all / scope stable。 |
| `pnpm.cmd --filter @bounded-funds/web typecheck` | `one-question-ui-final-resume-types-20261005T165419Z-3b8155d9` | FAILED：非本包 `api/full-joint-planning.ts:74:600` 语法错误；原记录保留，待其 owner 修复后最终重查。 |
| 同一整体 Web 类型命令，另一模块 owner 窄修语法后 | `one-question-ui-final-types-fulljoint-repaired-20261005T165742Z-ecafc42f` | exit 0；wrapper 10.491146s；all / scope stable；七文件最终源码保持。 |

首44模块和早期 types / lint 均已通过，保留原 manifest，不冒充后来新增只读恢复定位后的最终结果。全部测试夹具明确 TOOL_ONLY / SYNTHETIC，未调用实际数据库、银行或浏览器。真实金融工作流 PG 由根任务统一排程；本页不借这些夹具宣称后端成功。

本包三份最终 manifest 的 `source.after.json` 与 `.runtime/FULL-505-705-question-ui-20261005T1647Z/source-final.json` 七文件 SHA 相同。冻结目录保留七文件完整原字节与本地 HANDOFF，不覆盖旧失败证据。根任务注册生成的 OpenAPI / TS 已只读引用，未自行生成或修改主合同。

## 根任务接入点与未覆盖

建议路由 `#questions`，标题“一次一问”；默认导出 `OneQuestionPage({ mutationBlocked?: boolean })`。根任务拥有 App / 主导航：导入 `recoverOneQuestionOperation` 和 `useOneQuestionOperation`；将 pending / busy / storage_error 加入其他族全局新写 / reset 门。页面 mutationBlocked 只传其他族门，自身只读 GET 与同原请求恢复继续可达。刷新后的 `session` 只是只读定位。

尚未覆盖真实浏览器、键盘/手机完整流程、实际用户问题负担、所有 FULL 新金融动作与不确定变量、无限状态空间、整棵树最少问题、全产品 FULL-705 决策中心、提问后的新候选行动独立复核 / 执行。原五动作最多3变量、128实际世界和16问答轮次的容量界限保持；超容量原分母不截断。

已提交/UNKNOWN/终态 base action 仍由原服务拒绝继续金融规划；用户可明确关闭问答，关闭不会撤销原行动。reset 封存后缺少旧问答归档检索时仍可能 NOT_FOUND_NOT_FINAL；不存在终局拒绝回执协议时某些 4xx 后新写门将持续保守锁住。本页不静默清除这些记录。当前 epoch 取得依赖既有 demo 状态；不能取得时 START 禁用，但已保存原键只读恢复仍可用。

下一前置：根任务接入全局 gate / 路由，真实窄 PG 与浏览器验证旧回执重放、来源变化 REBASE、关闭和丢响应恢复；版本全量留到最终验收节点。FULL-505 / FULL-705 不因此关闭。
