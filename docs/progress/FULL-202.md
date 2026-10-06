# FULL-202 五集合自主包络

实现状态：原 MVP 五动作的可运行只读评估已实现；原 FULL 编号验收状态：PARTIAL，未关闭。

显式执行修订沿用 `docs/spec/execution-amendment-v2-functional-priority.md`，功能先行、后集中验收。本包未修改原金融哈希、授权、失败证据和正式模拟历史。

## 可运行能力

`POST /api/v1/autonomy-envelope/assess` 接受原五类小意图或已有 FULL 规划记录的有限模板选择器；`GET /api/v1/autonomy-envelope/actions/{action_id}` 读取原已准备行动。固定实际用户和可信业务时钟，在同一 RR READ ONLY 快照读取原事实、策略版本、证据、当前 typed audit、原 effect 和执行评估。客户端不得提供事实、余额、grant、confirmation 或结果。

FinanciallySafeSet、UserAuthorizedSet、LiquidityCompatibleSet、EvidenceSufficientSet、SupportedActionSet 分别提供 IN/OUT/UNKNOWN、原拒因和实际来源引用。仅对原支持行动调用原 `classify_autonomy` 与真实执行验证结果作交集，未运行的分支保持 UNKNOWN。ASK 的确切确认不变成 AUTO；已提交、银行已受理或 UNKNOWN 行动不得重新分类获得新权限。FULL 八个规划模板尚未支持的银行动作集合为空，金额及金融结果为 null。评估不授予权限、不生成 effect 或银行副作用。

## 必要验证及原失败

70 项纯/API/原自主分类相关测试 PASS（3.33s），六文件严格 mypy、Ruff、format PASS。纯夹具不是实际金融效果。原首次纯测试因 DashboardAuditCard 夹具缺字段 2 FAIL、类型检查首次测试调用原 execute 接口错误均保留在 `.runtime/FULL-202-envelope`，未改成功。

根任务唯一金融链首轮实际 PG：3 PASS / 1 FAIL（54.49s），wrapper FAILED/exit1。证据：`evidence/W3/actual-five-set-envelope-original-actions-real-pg-20261005T142706Z-6f7a10ba/manifest.json`。scoped_source_stable=true；all_source_stable=false，变化来自新 FULL-206 文件及无关 Web 测试，不能称全仓冻结。

已实际通过：原 purchase 五集合及零写；暂停授权和损坏银行证据拒绝；已确认 FULL 规划记录不授予未实现银行动作。transfer ASK 与确切确认检查已通过，故障节点错误地假设丢响应直接返回 UNKNOWN；原服务实际在独立银行提交后抛 `TimeoutError`。仅测试窄修为接住真实异常，然后只读原行动、同一银行键/操作及无回执，禁止再执行；再分类必须 409。原失败源码保存在 `.runtime/FULL-202-envelope/failed-integration-original-20261005T1428Z`，原日志、断言和核心代码不改。

窄修后 PG 测试 SHA256 `141662f06bc3290659caf2e3e99586b4bc0db437ed4067eae88fab43d86f2a67`，严格类型/Ruff/format 已通过。失节点实际重跑结果待根任务追加，不能以纯检查声称故障通过。

## 具体未覆盖

FULL 新金融动作适配、全动作/多目标/多资产的七项不变量、FULL-205 新生成器、边界事件持久化、前端实际 E2E及版本全量验收尚未完成。本 API 只查询原支持能力，未启用真实资金接口，未创建跨请求授权缓存。

## 实际修复链追加（原失败保留）

第二轮仅原 transfer 失节点实际 1 FAIL（16.60s），证据 `evidence/W3/actual-five-set-original-transfer-response-loss-repaired-real-pg-20261005T143836Z-46938907/manifest.json`。原 TimeoutError、只读原 SUBMITTED/UNKNOWN、银行 SETTLED、无回执、原 effect 和唯一 BankOperation 均已经过；测试仍错误地将用户 prepare key 当作银行幂等 key。第二轮原测试 SHA `141662f06bc3290659caf2e3e99586b4bc0db437ed4067eae88fab43d86f2a67` 已独立归档 `.runtime/FULL-202-envelope/second-failed-integration-original-20261005T1439Z`，FAILED 不改。

逐字段核原服务后，仅修测试为银行 key 等于实际 ActionPlan.idempotency_key；并保留原 owner/intent、ActionPlan.request_hash、bank.action_plan_id、bank.request 等于原 action.request.execution、bank.request_hash、business_key/effect_hash 的全部绑定及零写/409 断言。原核心金融接口未改。最终测试 SHA `16bdd6d455ee852b1a93469235cbf747de4b07d341e74c0723d588873150c78b`，strict mypy/Ruff/format PASS。

第三轮仅该节点实际 **1 PASS（15.96s）**，wrapper 17.296092s、exit0、all/scoped_source_stable=true，证据 `evidence/W3/actual-five-set-original-transfer-bound-bank-key-real-pg-20261005T145049Z-2fd48dcd/manifest.json`。当前实际范围为首轮三个通过节点和修复后一个通过节点，各自原分母保留；没有再跑“四项新整批”，没有跑全量。相关源 HOLD 已解除。
