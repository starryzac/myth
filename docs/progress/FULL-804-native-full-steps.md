# FULL-804 真实 FULL 操作执行接缝

记录时间：2026-10-06 11:00 北京时间。功能优先修订二；本包为完整实验输入执行真实功能所需的接缝，不增加通用调度、CLI、导出或验证框架。

可运行库入口：`FullNativeSteps(engine, DEMO_USER_ID, expected_epoch_id, aware_now)` 的上下文内调用 `dispatch(kind, inputs, step_at)`。只接受固定真实 API 路由；POST 输入恰为路由 UUID 身份字段与原 DTO `body`，GET 恰为 UUID 身份字段。原服务/API负责完整 DTO、版本、金额、签名与银行守门。正式库、非本机 54329、非生成 `bf_test_<32 hex>`、非原模拟用户、非 OPEN 原 epoch、时钟倒退均拒绝。每个请求重新核对真实 owner/epoch；仅真实身份 cookie 存在于本次调用，不缓存金融许可或原账本。

`LOCAL_USER_LOGIN` 输入必须为空，内部使用已经配置的原服务器凭证调用原登录 API；无配置时明确 `LOCAL_ACTOR_NOT_CONFIGURED`。不允许病例传角色、principal、密码、headers、任意 URL 或签名；不构造 USER 身份，不安装凭证。返回实际 HTTP 状态、解析后原 body、原 response text 与 SHA256；密码、cookie 和 headers 不进入结果。

23 项固定路由/输入/目标隔离直接风险 `W8/full-native-fixed-route-and-isolation-risk-20261006T025327Z-22f5e1e3` PASS，2.10 秒。首四源 strict `872fd01d` PASS；窄补原 HTTP bytes 保留、上下文重复打开拒绝和实际测试的原 `content_hash` 字段后，最终四源 strict `W8/full-native-original-http-bytes-and-target-types-20261006T025954Z-07828905` PASS。四源 Ruff/format 通过。唯一真实候选 `test_actual_native_login_full_validate_and_metadata_confirm_use_original_routes` 仅 `W8/full-native-actual-candidate-collection-20261006T025805Z-7f607cf5` collected 1，NOT_RUN；不会把收集与类型检查说成银行或真实 PG 成功。

四 Native 源与两新当前季节绑定源快照：`.runtime/root-new-independent-functional-final-20261006T0301Z/manifest.json`，SHA `38f80cc7ac08276cf9ca16544bf8b294c2e895b7e9540969b0b462ac577befa8`。前一个 0300 复制目录在重复 mkdir 时中止，无 manifest，不是最终源冻结。

具体未覆盖：50 族原始数据及 mixed 原 MVP/FULL 执行调用正在接；38 种实际路由的银行全链没有逐一实测；新 history 预览路由尚待注册。FULL 的 `DROP_BANK_RESPONSE` 和 `FAIL_APPLICATION_PROJECTION` 种类对尚未实现，明确拒绝，不伪造原银行/投影故障。B0—B5/P 的完整同病例选择、确认、执行与八消融全场实际结果未运行；FULL 28 指标仍 NULL，独立真值审核/冻结未完成。

旧 MVP runner、v1 输入/结果协议与哈希不变；无 reset 或正式历史迁移，无真实资金接口。FULL-804 仍 PENDING，两版集中验收 NOT_RUN。
