# FULL-106 / FULL-701：用户结构化策略候选

2026-10-05 功能增量已实现；原 FULL-106/701 保持 PENDING。本批解决首次引导不能直接提交结构化候选的问题，不替代自然语言完整编译、独立确认或完整产品验收。

## 可运行能力

`POST /api/v1/policy-declarations` 接收原 MVP 严格 DSL 的 configuration、原 idempotency_key、expected_epoch_id 和可选 source_proposal_id。仅追加实际 `PolicyProposal(PROPOSED)` 与 `EvidenceItem(USER_DECLARED)`；不能直接创建有效策略、执行动作或银行权限。原 `POST /policy-proposals/{id}/confirm` 仍要求另行明确接受和复核候选 hash。

`GET /api/v1/policy-declarations/{epoch_id}/by-key/{key}` 使用真实所有者和干净 RR READ ONLY 事务，返回原完整请求、request_hash、候选/证据身份和当前候选状态。同键异体拒绝；NOT_FOUND 的 not_found_is_final=false，不能用来绕过未决请求。确认后仍返回原 PROPOSED 请求，同时明确 current_proposal_status=CONFIRMED、receipt_is_current_authority=false。

来源编辑只复制同用户实际原候选快照，标为用户声明；保留原来源行、hash、元数据和证据等级。客户端不能输入用户身份、时钟、有效状态、accepted 或银行权限。实际 160 字符键对应有界 129 字符来源引用。旧 scenario_policy_declaration 的 bf_test/回环隔离 guard 保留。

新增服务、路由及两个测试文件：`services/user_policy_declaration.py`、`api/v1/user_policy_declaration.py`、`tests/test_user_policy_declaration.py`、`tests/test_user_policy_declaration_api.py`。root 将路由及只读事务接入 main/dependencies，并重新生成 OpenAPI/TS 合同；没有迁移或正式库写入。

## 实际检查

| 实际范围 | 结果 | 原 manifest |
|---|---|---|
| 请求/金额/身份/原键范围纯测试 | 10 PASS / 1.19s | evidence/W2/structured-user-candidate-request-pure-20261005T143725Z-76d75c2c/manifest.json |
| 六文件严格类型 | PASS / exit0 | evidence/W2/structured-user-candidate-types-20261005T143726Z-56311ed2/manifest.json |
| 最终静态/格式 | PASS / exit0 | evidence/W2/structured-user-candidate-static-import-repaired-20261005T143821Z-4359bf53/manifest.json |
| 两个隔离真实 PG 节点 | 2 PASS / 8.27s；wrapper10.185359s；all/scoped source stable=true | evidence/W2/structured-user-candidate-original-confirmation-real-pg-20261005T144122Z-418b5478/manifest.json |

PG 实际检查新候选仅两表增行、其他表不变；同键重放与原键读取零写；原单独确认后的不可混淆状态；过期周期、同键异体与来源 hash 篡改拒绝；旧来源完整保留。采用现存 run_scoped_check/pytest/mypy/Ruff，未扩展验证工具。首 main 导入顺序静态 FAILED 及原源 `.runtime/W2-user-policy-declaration/main.first-static-red.py` 保留，没有改成 PASS。

## 未覆盖及下一前置

自然语言完整12模板编译、LLM 来源脱敏/差异解释、FULL 专用声明审计尚未完成；这不是 FULL-106 整体完成。这里只接受现有 MVP DSL；8类 FULL 新模板使用独立 FULL 生命周期。原候选证据不提供跨多表同时恶意改写后的独立外部验真。

首次引导页面正在接实际候选提交和原键恢复，浏览器尚未验收。确定失败但没有持久终局拒绝记录的请求仍保留门；重置后原行不存在也不伪造最终 NOT_FOUND。后续需要真实最终拒绝合同、页面接入及集中产品验收，才能关闭原需求。
