# FULL-606 固定收款关系

状态：独立后端功能已交付，61 项直接检查通过；共享接线由主任务集成，真实金融 PostgreSQL、浏览器及完整验收尚未运行，不关闭原编号。

原要求见 `docs/spec/full-delta-map.yaml:2268`：支持固定收款周期自主；新收款关系必须用户发起并确认。原验收包含周期执行、额度和到期、歧义收款、Agent 自生外付拒绝及角色鉴权。

## 已实现能力

- 新增固定关系预览、签名 USER 发起、明确确认、原命令按键查询、原准备按键查询、动作及原 USER 单次同意读取、真实周期动作准备与执行服务。请求不接受金额、角色、银行事实、时钟、结果或成功标志。
- 采用服务端已验真的 `LocalActorPrincipal`，USER 原发起和确认保存完整请求、身份、范围、原件哈希及真实 `DECISION_RECORDED`。本地签名身份不证明真人身份。AGENT/SYSTEM 仅能消费既有且当前有效的自主固定关系，不能自行创建外付关系。
- Dedicated Full 关系绑定当前 Full 周期版本、当前已授权 MVP 周期版本、原银行唯一收款身份、CNY 现金账户身份、额度、自然月和有效窗口。完整周期配置须相同；Full 单次限额只收紧旧执行范围，不提升原银行权限。原 Full 规划确认仍为 `bank_authority=false`。
- 付款调用原 `prepare_action`、原 `confirm_action` 和原 `execute_action`；银行账单证明、实际已付金额、重复业务键、保护边界、原回执和账本守恒仍由原路径验证。ASK 增加实际签名 USER 同意原件，旧 DemoUser-only 确认不能代替新关系的用户同意。
- 准备原请求在旧准备提交前持久保存。原 Full 绑定尚未完成时，银行接缝明确拒绝按 legacy 权限受理；重放保持原 action、原键和原经济后果哈希。执行依赖默认空值并返回 503，主任务必须先安装实际银行接缝再启用原执行器。
- 已结算原动作只读恢复不把历史确认升级为当前权限；当前无银行原件的新受理必须重新验证关系、版本、额度、窗口及 USER 同意。
- 原银行付款会产生同收款人新消费证据。新增窄适配严格验证原固定身份及全部当前同收款银行证据，允许仅 latest evidence ID 改变；原证据缺失、篡改、归属、账户、收款身份或其它引用变化均拒绝。共享 Full 引用原哈希、原 `CHANGED_OR_UNAVAILABLE` 和原规划 flag 不改写。
- `verified_periodic_transfer_projection_binding` 为年度保护提供当前 dedicated 关系及实际来源 ID/哈希，不调用执行规划或年度投影，不授银行权限。
- `recheck_full_payment_actions` 只处理真实新 Full action/prepare 原件明确关联的动作。版本或状态失效时，原 PLANNED/AUTHORIZED 且完整银行、回执、腿、claims、legacy 分母均为零才 INVALIDATED 并记原审计/NO_EFFECT；在途、UNKNOWN、不完整来源保持原键，验真已结算原件不回滚。其实际数据库接线尚待验证。

## 文件与共享接线

新增三个生产文件：`domain/full_payment_permissions.py`、`services/full_payment_permissions.py`、`api/v1/full_payment_permissions.py`，位于 `apps/api/app/`。新增四个同前缀直接、服务、API、实际集成候选测试。没有新增表、迁移，也没有改写原策略、原动作 effect、原审计 canonical 或旧哈希。

主任务负责以下共享接线，独立模块不自行改变这些文件：

1. 注册 `/api/v1/full-payment-relations`，GET 与 POST `/preview` 使用 clean RR/READ ONLY；注册 LocalActor 真实身份入口。
2. 两个审计读取器支持新算法 `full-payment-relation-v1`，旧算法及篡改负例保留。
3. `execution_bank` 在真实 User 锁内、首次创建 PAY_RECURRING 银行原件/腿之前调用 `enforce_full_payment_bank_scope`，随后启用原 `execute_action` dependency。原已提交身份恢复保持原路径。
4. Full Periodic 保护仅在两个原引用漂移的窄场景使用 fresh verified binding，附实际来源原件，原 flag/hash 保留；其它模板仍按原规则拒绝。
5. Full 生命周期同 User 锁调用 `recheck_full_payment_actions`，只登记已实现 adapter；全局 `action_dependencies_supported` 仍不能宣称全部支持。

## 已运行检查与原件

以下四个原 wrapper 均 exit 0、`scoped_source_stable=true`、`all_source_stable=true`，不是全版本验收：

| 实际命令范围 | 结果 | 原 manifest |
| --- | --- | --- |
| 三个直接 pytest 文件 | 61 PASS，3.17s；wrapper 4.946485s | `evidence/W4/full-payment-final-direct-20261005T205231Z-ecd5ab05/manifest.json` |
| 七个源文件 mypy | PASS；wrapper 4.667439s | `evidence/W4/full-payment-final-types-20261005T205231Z-8769c6d9/manifest.json` |
| 七个源文件 Ruff | PASS；wrapper 0.076098s | `evidence/W4/full-payment-final-static-20261005T205232Z-7126f3a5/manifest.json` |
| 实际 PG 候选 `--collect-only` | 仅 2 个参数化节点收集；未运行数据库 | `evidence/W4/full-payment-actual-risk-collection-only-20261005T205232Z-a926b2c9/manifest.json` |

直接测试包含真实 FastAPI JSON 与真实本地 HMAC 验签，但业务服务使用明确 doubles，不能据此称银行执行已测。纯测试覆盖金额/extra/非法身份/窗口/原引用、同银行身份新证据、冲突或篡改证据、准备和关系键绑定、缺绑定银行拒绝、ASK 原 USER 同意、真实原件模型以及重查完整分母。Ruff format 七文件均 unchanged。

首次 type/static、六源 type、七源 type 的失败日志和旧 draft 均保留在相应 W4 evidence 与 `.runtime/FULL-606-first-draft-20261005T201729Z-77dc29b0/`。初次非 wrapper 格式检查有 scratch RED，只存在工具输出，没有完整原日志，不把它描述为正式失败证据。最终通过未覆盖或改标上述失败原件。

## 实际候选与未覆盖

`test_full_payment_permissions_integration.py` 的 AUTO/ASK 两节点待主任务串行执行：新建隔离库/default seed、实际银行空付款历史观察、真实 Full/MVP 确认、HMAC USER、原准备/用户复核/银行回执/三条腿、原键重放、全物理表只读零写、付款后同银行收款身份新证据仍保持原 dedicated 范围。

尚无上述金融实际结果；没有实际浏览器、丢响应/UNKNOWN 故障、到期与新版本竞争、重查数据库负例或真人证据。当前支持建立与银行已经唯一证明的收款身份的新用户关系；未知或歧义银行收款身份明确拒绝，未实现外部新收款账户创建或身份核验。服务端自主角色凭证发行和完整旧应用角色迁移尚未完成，不能把本地 USER 凭证称全部角色实测。

当前月/原单次额度由真实 MVP 付款 adapter 核验；后续月份必须获得当期实际银行观察并重新准备，不能以首月未付观察推定后月已付为零。没有真实资金接口，未改变正式模拟历史。下一步先完成主任务共享接线，再运行两个实际节点，保留失败并按直接受影响范围修复；原 FULL-606 关闭仍由完整验收证据决定。


2026-10-06 07:41 北京时间：固定付款原AUTO与ASK真实节点终态PASSED2/848.02s，wrapper851.602285s，W4/actual-fixed-payment-original-four-legs-auto-and-ask-20261005T231509Z-34a7fbee。相关scope稳定true，全源false仅独立新模块/UI/命令变化；原四条银行经济/负债分录、同原键恢复、完整审计与物理零写尾部已通过。此前prepare拒绝/expect3等FAILED及精确源保留，未改生产银行或旧记录。相关金融HOLD已解除，Root继续单一共享负责人。

