# FULL805 当前 GENERAL 机制的私有执行消费者

2026-10-06：本包为独立 DEVELOPMENT 生产适配器，原 FULL805 仍 PENDING。旧 P 最优组合、旧 public DTO、旧金融/hash/失败历史和原 selector 均不改。新的银行消费必须由 Root 安装原执行接缝后真实运行；当前银行消费者 **NOT_CONNECTED / ACTUAL_FINANCIAL_NOT_RUN**，B4 原模型仍 MISSING，真实指标仍 null。

## 可调用的真实 Python 入口

`FullExperimentAssetRequest` 只有完整原 `FullAssetPrepareRequest` 和私有 `RegisteredFullMechanismRule(original_path, sha256)`。只能供 owned DEVELOPMENT 的可信服务器 Python 调用；不能加入公开 HTTP 金融 DTO。原 rule bytes / SHA / purpose / owner / epoch / key / version 一一核对；规则不是权限，也不是银行事实。所有财务金额、产品条款、时钟、预留、收入和结果均由原服务读取、原确定性引擎复算。

```python
from app.domain.full_experiment_asset_execution import FullExperimentAssetRequest
from app.services.full_experiment_asset_execution import (
    prepare_full_experiment_asset_execution,
    lookup_full_experiment_asset_execution,
)

# prepare 仅在 Root 原 pipeline 已装 typed 参数和全部 guard 后可调用；
# 缺接缝明确 FULL_EXPERIMENT_ASSET_NOT_IMPLEMENTED，无金融写入。
action = prepare_full_experiment_asset_execution(engine, user_id, private_request, trusted_now)
# lookup 需要 clean RR/RO；只读原键、完整 body/hash/Action/UNKNOWN，不加载新规则或重选。
original = lookup_full_experiment_asset_execution(read_session, user_id, original_key, trusted_now)
```

本地 PostgreSQL 目标必须 `127.0.0.1:54329/bf_test_<32hex>`，并以实际 `SELECT current_database()`、真实模拟 User 和 OPEN epoch 复核；新 prepare 的 OPEN 时间不得晚于 trusted now。没有正式 DB、真实资金或跨请求授权缓存。

## Effect 与共享风险门

PREPARE 使用原 `read_current_general_purchase_selection` 保存完整原规则、原 P planning response、候选/排除产品全分母与 selected proposal。新协议从实际 selected batch 构造 **单个** `PURCHASE_ASSET` Effect，不调用要求 P OPTIMAL 的旧 `build_frozen_portfolio`。本金金额、各现金来源、income fragments、具体产品版本/目录/hash、position/return account、原赎回/到期出口和 15 分钟上界都保持独立原件绑定。

每臂共同保留当前已确认 Full / MVP version、scope、single / total managed cap、全现金/Goal 归属、完整收入与 claims、原 `revalidate_execution` 及 365×3 Full protection。即使 B0/B1/B2/B5 提案自身忽略某项，也不能越过共同生产拒绝门。prepare 强制 `requires_confirmation=True / ASK_ONCE`，Root 原 USER 明确确认仍必需；消费者不会自己制造确认、回执或成功标签。

后阶段不直接调用原 selector 服务去把自身 SUBMITTED/UNKNOWN 当“另一笔未决”。独立适配器读取原 **全部** unresolved Actions/Bank rows（1001 探测界限）、全部目录原件、原 exposure / income / protection；只有原 `load_execution_context(... own_action_id=同一实际 Action)` 已核完整 immutable command 的自身 claim 读视图可以恢复自身资金。原 raw 清单、计数、自身 id、完整调整后的 context 都进入新输入。其它未决或超容量仍拒绝；不删除正式行/预留/分母。

在该当前视图调用未改的纯 `select_current_general_purchase`；只允许它验证原固定 Effect 的产品、版本、金额、cash/income uses、当前 Full/MVP 和完整保护。不能替换原 Effect、移动原到款上界或扩大 expiry。当前选择不同只拒绝，不能沿旧 key 生成新产品或新钱。已经存在原 Bank result 的同键恢复由原 pipeline 首先处理，不能以新策略拒绝已受理回执的恢复，也不能因此产生第二次受理。

## Root 接入的有限接口

常量：`MARKER=full_experiment_asset_execution`，`ALGORITHM=full-experiment-asset-execution-v1`，`GUARDS_VERSION=full-experiment-asset-guards-v1`，原键规范化 `experiment_asset_bank_key(key)`。

1. `prepare_action` 新 **私有** `_full_experiment_asset_request` 参数，排斥 dynamic/recovery/旧 callback 混用；验证实际 owned simulated database/user 后，先 exact key 原查回。已有原件调用 `verify_full_experiment_asset_prepare_replay`，必须完整 private body（含原 path/SHA）一致，不能重读新规则后覆盖旧行。新实例调用 `produce_full_experiment_asset_effect(...) -> effect, proof, marker`。
2. 原 load context 后明确 `requires_confirmation=True` 并录制实际 context；不能保留 generic AUTO 默认。原 prepare payload 放 marker，原 `record_execution_trace` 的新协议集合加入此 marker，并按现 dynamic/recovery 方法保全完整 `action_request`；收入 `_epochs` 原处理保持。
3. 确认/phase1/首次 bank accept 的原 source 读链调用 `has_full_experiment_asset_binding` 识别原 marker / prefix / retained trace（剥 marker 不可 fallback），以及 `recheck_full_experiment_asset_proof(...)`。原 USER 确认、原 reservations、完整 bank source / projection / receipt、UNKNOWN 原 key 恢复路径全部继续适用。
4. 两个原 pipeline 模块仅在完整接缝安装后发布同 `FULL_EXPERIMENT_ASSET_GUARDS_VERSION`。shared recorder / `_stored_trace` / domain audit 的新算法调用 `read_frozen_full_experiment_asset_proof(trace)`；该 helper 是纯完整输入重算，不读 DB、当前作者文件、parent trace 或 recursive audit。
5. 原 capture 新字段 `planning.full_experiment_asset_execution={inputs:<完整 FullExperimentAssetExecutionInput>,proof:<完整 FullExperimentAssetExecutionProof>}`。marker 保存原完整 request/hash/effectHash/originalProof；其它阶段 original marker/proof 不改，新的 current proof 留在该阶段 capture。sources union 原 recorder 源与完整 current input source copies，当前证据副本不匹配即拒绝。保原 Trace 10 MiB 边界，不截断大历史输入；触顶须原样报告失败。

## 未覆盖

当前无新 public HTTP 入口或 UI，不是完整七臂/八消融执行，更不证明 350 场景/28 指标或基线优势。GOAL、支付/恢复、ladder、多产品组合、不确定世界、模型 confidence、正式冻结作者规则和全部机制均未覆盖；B4 / 四个无实际对照消融继续 MISSING。共享接缝、实际银行交易/响应丢失/当前 source 漂移的完整 PostgreSQL 证据、性能与 final full acceptance 均 NOT_RUN。历史 sealed epoch 原读能力只沿原服务，不冒已验证。这里的纯 synthetic 风险只证明有限 checker/数据接缝行为，不计经济效果。

## 本轮直接证据

`current-mechanism-selected-effect-final-direct-20261006T042134Z-70aaa423`：24 个纯 synthetic 直接风险 PASS，86.72s、0 skip，owned source stable=true；global=false 因独立 joint producer 两源变化。包含 B0/B1/B2/B3/P 的真实原 pure selector 不同提案、共同资金/权限门、完整 original P 保留、source UNKNOWN / content / 分母拒绝、完整原 effect 不可重选、闭合 marker / original key / source copies 及重新算 hash 后仍拒绝假历史。不是银行/真实臂完成。

随后只改一处测试局部 `marker` 注解为 `dict[str, Any]`，没有修改测试 body、产品/金额、断言或生产源；原 types 两诊断保留于 `current-mechanism-selected-effect-final-types-20261006T042134Z-2ae52ad0` 和 `.runtime/FULL-805-execution/fixture-type-red-20261006T042335Z`。修后 strict 三 owned 源 `f9aa7602`、Ruff `3c536f50`、format 均 PASS；24 行为证据依据该单行 type-only 精确差量复用，没有重复跑整批。早期19PASS和所有首 static/type 日志、源码原件也保留，未改原状态。未收集/运行本包实际金融候选；Root 后安装原 pipeline 才能排唯一 owned PG。

## 2026-10-06 12:38 北京时间：Root 原执行消费者已连接

Root在原 execution / execution_context / execution_sources / execution_bank / decision_recording / decision_trace / audit_chain 安装新私有typed请求、独立original key、完整marker、same-key回读、ASK context和CONFIRM/RESERVE/BANK_ACCEPT 当前完整来源复验。新增纯 full_experiment_asset_execution_trace 对完整原proposal、实际原effect、每阶段原财务决定及原USER确认Evidence逐项重算；不借旧AUTO标签、receipt或session缓存作权限。原未标记经济执行协议保留。新私有入口先验证actual owned bf_test数据库再查User，错误数据库不能进入旧表查询；公众HTTP schema仍没有rule/money/newrole。

`W7/selected-mechanism-root-frozen-consent-and-owned-hooks-20261006T043234Z-d8a2d1cc` 12风险 PASS140.50s，scope stable。四阶段原件正链、重hash后的AUTO/context/outcome/缺确认/异effect拒绝、context不改原对象/无SQL、SQLite私有目标早拒绝均使用明确synthetic来源，不作金融实证。8 shared strict37c89c81、当前4 strictffd47695、10 static e0dcaf47 PASS。原旧negative缺pipeline测试因现在guard已安装，Root仅显式monkeypatch移除一项guard来保原not-installed负例，原行为/断言不删；该节点已本批实际通过。其余原24 pure未改行为，按同批差量复用。

状态更新为PRIVATE_GENERAL_SINGLE_EFFECT_CONSUMER_CONNECTED / ACTUAL_FINANCIAL_NOT_RUN。七机制/八消融、B4、模型confidence、全部350场景和28指标仍未证明，不改旧NOT_RUN记录。当前唯一maturity PG在跑，现有shared源冻结；该consumer的实际金融/响应丢失/重试/来源漂移节点随后串行。没有扩新的验证工具。
