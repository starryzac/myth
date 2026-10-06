# W1_PROVIDER_FROZEN_CASE_BRIDGE_V2 / W1_ORIGINAL_RULE_DYNAMIC_RESOLUTION_V2

已安装私有生产入口；PURE_STATIC_VERIFIED / ACTUAL_V2_SERVICE_PG_NOT_RUN。V1各入口保留，默认没有V2注册时不会添加观察SQL。此执行修订只接上冻结Case原图与原五臂GENERAL管道，不关闭MVP-503、正式24×5或FULL，不改变原rawFalse/经济等级。旧两源与候选冻结manifest先逐bytes归档于 `.runtime/W1-registry-v2-integration-20261005T095138Z/originals`。

## 正式本机接口

`SimulationContext.parse` 同时接受旧 `mvp-arm-isolated-context-v1` 与新 `mvp-arm-isolated-context-v2`。V2在原13bindings之外要求 `root_registration_ref={path,sha256}`，它纳入原context digest；真正原INPUT/oracle/design/RULE/SOURCE完整byteSHA仍在13bindings，不能用wrapper替代。V2仅MVP_FROZEN/FULL_FAMILY_FROZEN+SERVICE_INTEGRATION，本机127.0.0.1:54329/generated bf_test。公共Web/HTTP DTO没有新字段或入口；本文件示例中的占位值不构成注册/冻结输入。

四生产module函数签名保留：verify_simulation_context(context)、prepare_arm_action(context,request,candidate_selector=...)、confirm_arm_action(context,action_id,effect_hash,actor_registration_ref)、execute_arm_action(context,action_id)。V2 `_invocation`每调用构造一个 `FrozenInvocationBridge`，真正调用原prepare_frozen_case/revalidate_archive/FrozenDraft取完整冻结图；当前全source及原源码归档校验、四loaded production funcs CodeType门、原endpoint/user/OPEN epoch/genesis/seed原SQL门仍在。bridge生命周期仅本调用，finally再复核字节；每相位/Probe重读所有原件与current源/loaded pure funcs和当前SQL owner/epoch。请求内复用readonly parsed graph，禁止跨请求授权缓存。

外部不可覆盖root注册protocol=`mvp-arm-provider-registry-v2`，实际字段见下面占位schema。每当前Scenario step/service invocation各用新文件+外部SHA。全部artifact从FrozenDraft真实input_ref/oracle_ref/rule_refs[arm]/global source_ref/design_ref取原bytes，SOURCE.files不重写为旧implementation_files，global SOURCE/DESIGN不补case标签。current clock必须是当前已冻结Step.at同一aware瞬时。完整14指标、独立oracle、source/seed、24配额和development差量继续由原corpus图核。

```json
{
  "protocol": "mvp-arm-provider-registry-v2",
  "bindings": "<exact original thirteen-key object>",
  "database_name": "bf_test_<actual32hex>",
  "corpus_directory": "<actual relative immutable corpus directory>",
  "manifest_sha256": "<external original manifest byte SHA>",
  "typed_execution_sha256": "<original validated typed Scenario SHA>",
  "source_inventory_sha256": "<canonical original source inventory SHA>",
  "scenario_step_id": "purchase",
  "operation": "PREPARE",
  "opportunity_id": "purchase-op",
  "previous_step_originals": [
    {"step_id": "declare_asset", "ref": {"path": "<absolute actual retained file>", "sha256": "<original byte SHA>"}}
  ],
  "previous_step_inventory_sha256": "<canonical complete entries SHA>",
  "actor_original_directory": "<owned actual actor-original directory>"
}
```

Root driver必须将本run/case/arm/user/epoch此前实际service返回写成不可覆盖raw：protocol=mvp-raw-observation-v1，kind=SCENARIO_SERVICE_STEP_RESULT，原13bindings，payload=step_id/kind/at/capture_origin=PRODUCTION_SERVICE_CALL/result=原实际返回dict。root注册原完整列表外部SHA提供可信清单锚；桥不凭origin文字独立证明服务曾执行。未来/current/跨owner-arm-run-epoch、失败/缺result、重复/重排/缺原件/byte漂移拒绝。原resolve_inputs的1MiB/深度/节点/strict backward pointer门保持，原值$ref仅数据。

RULE冻结前须真正登记opportunities：每项 `{opportunity_id,step_id,operation,inputs}`。PREPARE/CONFIRM/EXECUTE分别对应原PREPARE_ACTION/CONFIRM_ACTION/EXECUTE_ACTION。当前请求必须同时等于原该RULE机会及原当前Scenario Step.inputs经可信previous原件解析的结果；缺schedule/其它意图明确NOT_IMPLEMENTED，不偷偷修改原输入。例inputs可以在policy_id使用 `{ "$ref": {"step_id":"declare_asset","pointer":"/result/policy_id"} }`；这仍是原INPUT/RULE中的动态引用，解析后原bytes和SHA不变。

## 新原RULE factory

V2 B0/B1/B2使用 `candidate_selector_v2(context, original_rule, opportunity_id)`；P/B3返回None走原实际planner，B3每动作仍走原exact-effect actor确认。factory和closure必须是新冻结mvp_arm_executor实际源码中的FunctionType/CodeType，不能输入任意callable或P输出重标签。原RULE需包含原mvp-arm-rule-v1 algorithm（直接或arm_algorithm）及arm/case/purpose/seed。原candidate_selector完整保留供V1。

新bridge只解析本机会所需cash IDs、B0当前manual intent、B2 fixed-policy-version IDs，保原完整RULE digest；其它未来manual机会不提前解析。B0 amount_cents与B1 threshold_cents必须冻结字面非负整数，B2 timezone/horizon必须冻结字面时轴，不能指向P输出。`ARM_RULE_RESOLUTION`独立原件含原RULE valueSHA、resolved algorithm valueSHA、原row/pointer/valueSHA refs、13bindings及外部root_registration_ref。RAW_PLANNING_VIEW携带这份实际源绑定解析，candidate保原RULE digest并额外给rule_resolution_binding_sha256；执行core只收到原允许的amount_cents+cash_uses，income funding/immutable effect/revalidate/reserve/银行三阶段均原样。

每个V2 producer raw封套附同本调用外部root_registration_ref，OriginalReader/_original拒绝其它调用注册拼接；actor原review也需该字段（confirm调用的注册）。actor implementation_source_ref使用原SOURCE.files格式path+sha，由FrozenDraft精确映原归档source；实际action/hash/full effect/actor/user/时刻仍核，V2允许同一aware瞬时Z/+00表示，原bytes不改。

## 验证与界限

当前新测试+原V1 executor纯测试110PASS2.50s、严格types4/Ruff/format通过；原13 provider风险+7 phase风险20项仅collection。首次109PASS/1FAIL为新测试误设原Fixture手工金额100（原登记200），原日志与四源码归档后只纠正测试；首次mypy1error及Ruff原日志保留。纯数据/源码门测试不是金融实证。

未跑真实V2 PG、未有实际24完整冻结图constructor/driver全链实测；root必须补actual Step原件捕获与清单登记，冻结最终新增源/RULE schedule，再核V1默认、各GENERAL baseline动态IDs/网关拒绝/None、原income归属、B3实际actor、source/epoch漂移及3phase故障。完整独立phase/economic/oracle证明另行核验；无银行路径原NOT_IMPLEMENTED和历史新调用未到phase缺口保留。GOAL baseline真实lot/其它意图未扩大，正式24/五臂/14指标及真人研究未完成。

## W1_PROVIDER_OWN_ENGINE_FROZEN_FAULT_V2

原scenario_runner._fault按Engine对象身份和原user/ContextVar隔离。V2 provider自建Engine，外层driver的rootEngine注入不会命中。新接线仅在execute_arm_action的原observation_scope内，读取本调用已核FrozenInvocationBridge.step.fault，在provider实际own Engine上包原四位置execution.execute_action；只支持冻结EXECUTE_ACTION的DROP_BANK_RESPONSE/FAIL_APPLICATION_PROJECTION。没有公共fault参数、外来callback、Effect/grant输入。

V1/NONE不安装fault hook、不增SQL；原V1/source gate断言保留，新增helper也绑定原加载code。非NONE时当前已登记scenario_runner原bytes及两个原contextmanager/code/closure完整一致才调用；fault scope退出恢复原hook和marker。RUN_RECOVERY仍root Engine原路径，不在provider里重造恢复。bank/projection/recovery/core、原UNKNOWN/历史hash/观察phase flags保持。

新12纯风险加V1 executor/registry/Scenario相关共135 TOOL_TEST_ONLY PASS3.38s，strict types2/Ruff/format通过。候选12PASS与static RED、原provider8cb归档及安装proof留在`.runtime/W1-provider-own-engine-fault-*`。纯Engine/Session从不连接，不构成金融实证；root仍需实际DROP银行已commit→UNKNOWN、FAIL阶段3rollback与原同action重放风险验证。原economic/phase证明False不升级。B3恢复每动作银行前actor确认仍未接线，保NOT_IMPLEMENTED。
