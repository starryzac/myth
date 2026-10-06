# 模拟完整配置声明与独立确认

显式执行修订 `W1_GENERAL_DECLARATION_ORIGINAL_SERVICE_V1`。原候选4文件及当前接入前3文件的实际字节与SHA保存在 `.runtime/W1-general-declaration-root-integration-20261005T074447Z-5e309fc4`。旧模板、字符串corpus、失败原件和正式历史保持原样。

私有Scenario新增 `DECLARE_POLICY`，输入仅 `configuration`、`idempotency_key`、`expected_epoch_id`。完整配置走原strict配置验证；ASCII幂等键1–160字符，时钟来自Scenario服务上下文。真实PostgreSQL/127.0.0.1:54329/generated bf_test、实际数据库名称、模拟用户、唯一当前OPENepoch均复核。原审计guard/用户行锁/epoch行锁下原子保存USER_DECLARED原证据和PROPOSED原提案，没有策略版本、动作、资金或权限。

新 `CONFIRM_POLICY` 输入仅 `proposal_id`、64位原reviewed_hash、严格布尔 `accepted`，调用原confirm_proposal。False仍由原服务拒绝，True也必须通过当前资源、时刻和原配置核验。真实结果引用用 `{"$ref":{"step_id":"declare","pointer":"/result/configuration_hash"}}`；原返回内容不被替换成预期结果。公共Demo/RPC未开放任意配置上传。

同epoch/key/完整配置重放核对原件身份、来源、首次时刻、状态、内容实际摘要和独立首次确认，不修改旧哈希。首次版本额外重跑原strict schema和实际configuration_hash，拒绝bool/int相等造成的类型混淆。已确认提案返回历史状态且grants_authority=false，不声称当前策略仍有效；当前金融执行继续原授权/版本重验。

接入后216项相关纯风险PASS/1.98s（3真实节点deselected），5源strict mypy、6源Ruff通过：`general-declaration-installed-pure-20261005T074734Z-eb545d35`、types `074735Z-c4c4251b`、static `074735Z-73d50bb5`。首次声明13节点真实PG为10 PASS/3 FAIL，43.36s，原 `general-declaration-installed-real-pg-20261005T075721Z-30e9deb1` 保留。失败是假设原False确认状态为409（实际422）、假设可通过ORM bool/int相等值或修改摘要形成已损坏版本；原数据库不可变触发器阻止真实UPDATE。

只修三失败测试，生产代码不变；两真实Core UPDATE篡改被原不可变触发器拒绝、全23表零写并读取原首次确认；Scenario声明→原422拒绝→独立原确认→原配置/摘要一致、账户/持仓/银行/动作/回执不变。`general-declaration-three-red-real-pg-20261005T080302Z-5d7d4a2d` 3 PASS/9.44s，10已通过节点未重复运行。新增两个首次版本异常纯元数据负例后25 TOOL_ONLY PASS/1.27s、2测试types/Ruff PASS；合成元数据不是实际金融结果。不存在绕过或停用原不可变触发器。

待证明：真实并发同key、新用户/新epoch、目标/房租完整资源确认仍未覆盖。24×5案例、完整阶段独立重放、14指标、初版全量均NOT_RUN；不据文件复用关闭编号。
