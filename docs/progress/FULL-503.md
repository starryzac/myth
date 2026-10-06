# FULL-503 动作稳定性判断

实现状态：有限范围生产能力已实现；原编号验收状态：PARTIAL，未关闭。

## 已实现：完整经济签名共识及UNKNOWN完整性

复用原economic_signature，比较动作类别/金额/资金源与目的账户/goal归属/产品条款/权限版本/费损/结算和本金到账后果，并加入原分类、财务评估、授权状态、确认要求、validation状态/拒因与投影风险。生成的operation/business/quote临时身份按原经济签名合同排除。所有世界KNOWN且完整签名一致才STABLE；同ASK标签但不同金额/归属不一致。UNKNOWN/UNSUPPORTED仍计原分母，stable/should_ask=null，不给最优问题。全部原金融拒绝单列ALL_WORLDS_BLOCKED，拒绝不是资金成功。稳定不取消原确切确认；总execution_eligible=false。


## 生产接口和文件

`POST /api/v1/finite-planning/analyze`，operationId `analyze_original_finite_planning`，需由根任务注册 router 和 RR READ ONLY POST 依赖。

请求仅 `base_action_id` 和 `variables`；没有client now/user_id/bankfacts/authority/confirmation/world results。单变量示例结构：

```json
{"variable_id":"amount","field":"TRANSFER_AMOUNT","source":"USER_REQUEST","choices":[{"key":"low","value":{"kind":"money","amount_cents":100}},{"key":"high","value":{"kind":"money","amount_cents":200}}]}
```

以上数字仅请求Schema示例，不是金融效果。

实现：`apps/api/app/domain/finite_uncertainty.py`、`services/finite_uncertainty.py`、`api/v1/finite_uncertainty.py`。直接测试：`test_finite_uncertainty.py`、`test_finite_uncertainty_api.py`、`test_finite_uncertainty_integration.py`。旧MVP finite amount入口/classifier/economic signature未替换，共享models/migration/main/dependencies/contracts由根任务集成。

## 必要检查及当前证据层级

日志 `.runtime/FULL-501-506-finite`：首domain16纯PASS1.92s；新增来源/API与原autonomy直接风险后81PASS3.09s，均是纯夹具/API doubles，不是金融实验。原第一次类型1错误（list invariance）及第一次全类型5个测试注解错误、格式前Ruff长行结果保留。修复后六文件strict mypy/Ruff通过；最终检查和源SHA归档见该目录source-final manifest。

实际唯一PG候选：`test_finite_uncertainty_integration.py::test_actual_multivariable_worlds_reuse_original_engine_without_consent_or_writes`，仅collection1.74s，金融PG当前NOT_RUN，根任务单链执行。候选覆盖实际旧base确切确认、4个金额×owned现金/GOAL目标世界的原2ASK/2金融BLOCKED、原确认不继承、新世界不是旧action、minimax固定tie、foreign世界2UNKNOWN/分母4、BANKproof不充用户声明、owner404、全表零写。尚无实际结果时不写PASS。

## 明确未覆盖和下一依赖

功能优先执行修订二有效；实现状态与原验收状态分开。本包是旧MVP五动作的有限规划合同，FULL新多目标/多资产联合优化、新模板真实执行、原计划所有不确定变量语义和全面世界组合未覆盖，不能据旧文件复用关闭编号。REGISTERED_EVIDENCE读取合同已实现，新的持久声明生产入口未在本包新增；无符合原件则UNKNOWN。

FULL-505回答持久状态机/回答校验重放/到期/并发新事实与回答后新run、旧候选及旧确认失效；FULL-507持久介入键/节流/跨进程重启去重；前端逐问流程、完整确认基线实际对比及版本全量验收尚未完成。需要根任务持久底座后接入，不把本只读suggestion当已存问题或确认，不产生银行grant/自动执行/真实资金接口。没有跨请求授权缓存。

## 2026-10-05 UTC 原 finite 真实PG节点追加

根任务实际执行 `test_actual_multivariable_worlds_reuse_original_engine_without_consent_or_writes` 已 PASS。它属于原两节点批次 `docs/progress/evidence/W3/actual-boundary-action-events-and-finite-worlds-real-pg-20261005T152630Z-7693a74d/manifest.json`：整批 FAILED，1 failed / 1 passed，pytest总206.14s、exit1、scoped_source_stable=true/all_source_stable=false。另一 Boundary 事件回执 completeness 节点失败，原 FAILED/source/output 不更改。本处只登记唯一 finite 节点通过，没有另造单项耗时或把整批重标成功。六源 HOLD 已由根任务解除；上述此前NOT_RUN为当时状态，本追加提供真实差量。全编号原要求/未覆盖和FULL验收仍保留。
