# FULL-605 已知行动适配器的生命周期重查

状态：PARTIAL_IMPLEMENTATION，原编号PENDING。用户修订二允许功能先于集中验收，原hash、旧回执、正式历史不变。

## 完成内容与源

新增`domain/full_policy_action_rechecks.py`、`services/full_policy_action_rechecks.py`及direct/integration候选，Root在`services/full_policy_lifecycle.py::_record_command`同原User writer锁接入新CrossGoalReallocationPolicy回拨和PeriodicTransferPolicy固定付款。606导出固定关系精确原prepare/binding重查。原replay在_record_command前返回，原stored result/hash不改；现FullLifecycleResult已有invalidated/inflight列表只登记实际关联原Action，action_dependencies_supported继续False表示不涵盖其他adapter。

回拨只在完整零银行/回执/posting/所有claims且PLANNED/AUTHORIZED时INVALIDATED并追加原ACTION_STATE_CHANGE、NO_EFFECT exposure；原Trace COMPLETE/VALID必要，银行分母包括同原ActionUUID及跨owner异常关联，不把缺数据当零。固定付款额外核原prepared/binding/旧redemption/所有claims和真实原结算。SUBMITTED/UNKNOWN或有实际效果一律保留原身份与原key，已验真的SETTLED不撤回、不创造新收入。新版本/状态仅停止新受理，银行前仍另fresh查真实授权。

## 检查

25回拨pure风险及606 finite风险各模块通过；Root相关135directPASS11.54s、6strict与14static通过，原first types/static失败及精确源码保留。新增实际未提交撤销node `test_full_policy_action_rechecks_integration.py` strict通过、NOT_RUN。Root受影响cash UNKNOWN撤销恢复node先前67092 PASS，但本次新lifecycle hook后须定向实跑，不能复用作新hook证明。首606实际node因shared只读snapshot接线漏项FAILED14.72s，未达605，保留原FAILED；Root窄补后等待新唯一链。

## 尚存限制/下一前置

整组资产批次、其他FULL模板依赖尚未接；所有版本时间/并发竞争故障矩阵、真实新605阶段、最终完整验收尚缺。没有银行撤回承诺、跨请求授权缓存或真实资金接口。下一按实际单链取证后接资产已持久批次重查，不增加验证工具框架。

## 资产依赖服务与共享生命周期接线（2026-10-06 06:25）

新 services/full_asset_action_rechecks.py 已FINAL，53合成来源直接PASS8.99s、strict/Ruff/format通过。Root已在原 _record_command 的 AssetAuthorizationPolicy 分支接该服务：原User锁与事务内、新生命周期command落库之前执行。返回invalidated/inflight身份写入本次原result，异常传出让原调用事务回滚；旧replay不进_record，原stored result/hash不改。action_dependencies_supported仍False，未宣称所有模板依赖适配完成。

Root hook两直接风险PASS1.81s（W4/actual-asset-lifecycle-hook-direct-20261005T222440Z-88749385），核exact owner/policy/epoch/newCommandID和inflight保留、服务失败不落假成功result；1源strict/static通过。共享5源严格类型及static在 registered-account-proof-and-605-compiler-shared-final-types/static-20261005T222236Z 通过。direct均合成，无PG/银行效果证据。

资产真链诊断 W4/actual-whole-asset-current-open-clock-20261005T213247Z-dadd11d5 终态1PASS2826.45s，但新605hook是其后接线；该原运行两相关FullProtection源曾变化，因此不把scoped稳定当全相关冻结。本次605资产真实策略撤销/版本竞争节点尚NOT_RUN，待下一唯一金融批。任何bank原件、SUBMITTED/UNKNOWN、来源异常保原键/占用，不臆造撤回。最终全部提交前后竞争矩阵和全量仍缺。
