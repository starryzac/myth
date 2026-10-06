# FULL-102 完整证据图新版前端消费

2026-10-06；功能交付范围为独立只读模块。原七根图、旧 reader、旧摘要、正式历史、失败原件均保留。FULL-102 总验收仍 PENDING。

## 可调用接口与接入

- `apps/web/src/api/full-evidence-graph.ts`：`getFullEvidenceGraph(kind, identity, knownAt?, userId?)`，只发 `GET /api/v1/evidence/full-graph/{kind}/{identity}`，可选且唯一查询参数 `known_at`。`userId` 是 host 的只读响应绑定，不向服务端提供 owner 选择或权限。
- 使用真实生成合同 `components['schemas']['FullEvidenceGraph']`；不手写替代金融 DTO。
- `apps/web/src/components/FullEvidenceGraphPanel.tsx` 默认导出 `FullEvidenceGraphPanel`，props 为 `{initialKind?: FullGraphRootKind, initialIdentity?: string, ownerUserId?: string}`。前两项仅初始化表单，不自动请求；host 改变根时应通过 React `key` 重挂。Root 后续接入原证据页；本批没有编辑 App、EvidencePage、通用 HTTP 客户端或 contracts。
- 本模块只有 GET 和本地导航，不建立金融写门/权限缓存；其它族待核对时仍可由用户查询。没有自动重试、修账、金融确认或执行入口。

## 实际消费边界

严格检查 35 类注册表的唯一类型/真实表名/所有权范围、actual/known/captured 整数分母与完整标志，节点 UUID/owner/知识时间、原根/请求时点绑定、展示与期望分母、唯一引用及实际 JSON pointer。USER 只接受当前用户自身；PRODUCT/PRODUCT_CATALOGUE 明确共享范围，不伪加 user_id。

当前原件与历史不可变原件可导航；历史可变原件必须内容/hash 同为 null、原 UNKNOWN 与具体不足齐全，不能用当前余额或状态补过去。时间比较保留微秒，带时区等价时点可匹配；不能用 JavaScript 毫秒截断消除一微秒差异。

服务端 `REFERENCES_RESOLVED` 显示为“引用已解析 · 不代表金融成功”；NOT_CHECKED 仍显示仅导航。审计/银行 VERIFIED 只写“原服务报告”，浏览器没有自行运行银行/审计算法。展示预算不足、来源不完整、银行 UNKNOWN 均保留原分母与具体代码，不改成完成。

SQL 原件是原证据而非浏览器计算输入。结构化分母需安全整数/真实 bool；原字段中的整分、篡改字段或大整数不用于格式化、资金计算或授权。完整 HTTP 原文本独立保留、按原字符展示；避免把 JSON.parse 对 signed64 的舍入值显示成精确金额。浏览器检查摘要格式和关系，不重新计算服务端全库 inventory/row/input SHA，也不据此证明金融成功或因果。

## 定向验证

所有 fixture 明确 `SYNTHETIC_HTTP_FIXTURE_ONLY`，不作为实际 PG、银行、浏览器或业务验收证据。

| 检查 | 真实结果 | 原件 |
|---|---|---|
| 两新文件 Vitest | 38 PASS：30 reader、8 panel；3.41s。wrapper PASSED，scoped/global source 均稳定 | `docs/progress/evidence/W6/full-evidence-graph-web-final-pure-20261006T004847Z-0dab21fb/manifest.json` |
| 五新文件 ESLint | PASSED；scoped/global source 均稳定 | `docs/progress/evidence/W6/full-evidence-graph-web-final-static-20261006T004842Z-89ff02bb/manifest.json` |
| 当前全 Web types | FAILED exit2；仅别方 actual-action-set.test.ts / ActualActionSetPanel.test.tsx 的可选字段错误，没有本五文件诊断；不能称类型门通过 | `docs/progress/evidence/W6/full-evidence-graph-web-final-types-20261006T004842Z-e4e0df16/manifest.json` |
| 临时继承配置窄 types | FAILED TS2688：位于 .runtime 的配置无法解析原 node/vite/jest-dom type library。属于检查上下文失败，停止重试，不作为 PASS | `docs/progress/evidence/W6/full-evidence-graph-web-isolated-strict-types-20261006T005009Z-7e567cc3/manifest.json` |

原首检查全部保留：裸 pnpm 被 Python Popen 找不到（三个 004529Z 目录仅保 source.before/空 output，wrapper 尚无终态 manifest，不能称工具已跑）；原生 node/pnpm 后 Vitest esbuild spawn EPERM 原 FAILED 保留。允许既有本地子进程后首实际 Vitest 35 PASS/2 FAIL，原因是测试查询同时匹配原 HTTP 文本；查询窄到实际问题区后通过。首 type 两个 original_refs 可选数组诊断已窄修。失败前五文件精确字节在 `.runtime/full-evidence-graph-web-first-candidate-20261006T004528Z-9122d668/`，原日志没有覆写。

## 具体未覆盖

主页面集成和 Root 修复其它模块后当前整体 types 尚待；本模块没有实际 Edge UI/生产网络验收，没有测得大图渲染时长或浏览器峰值内存。历史可变状态回放仍由后端明确不可用；不能借引用存在补过去值。图展示受后端 2048 节点/20000 边、注册原件容量限制，不提供全金融成功、完整因果归因或新权限。本模块完成不关闭 FULL-102。

## Root 主页面终检增量（08:56）

Root已在实际 EvidencePage 挂载独立新FullEvidenceGraphPanel，35类来源/账户交易账单Goal/Full政策/银行/audit/真实持久原关系可选。初始deep link只填表且独立手动GET，旧7根视图保留；换根通过key重挂，不自动POST或推定成功。Root同时在PolicyCenterPage接真实actual-v2只读动作集合，原分母/未覆盖/UNKNOWN与原HTTP全文不格式化掉。

当前整体Web类型 `W6/current-evidence-graph-and-action-set-product-host-types-20261006T005214Z-d6a3ce17` PASS。Agent原全types外部新测试诊断及临时tsconfig错误原件保留；Root仅修自己的新夹具可选/tuple类型，并提取共同fixture去除原17次执行中的8重复节点。Root当前9不同新动作集合风险+10旧受影响父页风险，共19 PASS4.62s，`60b272b6`；7源ESLint PASS `c3ff07c3`，此前Agent38图谱风险未改行为不重复运行。Root7源FINAL `.runtime/root-current-graph-and-global-ui-host-final-20261006T0056Z/manifest.json`，hash另记原manifest。真实PG图谱尚NOT_RUN，浏览器/手机/真人未验，FULL102 PENDING。
