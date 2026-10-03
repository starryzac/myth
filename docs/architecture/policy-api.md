# MVP 策略接口合同

接口前缀 `/api/v1`，绑定当前合成演示用户。请求不得传入 user_id、status、actor、confirmed_at、as_of 或服务端哈希。时间由服务端可信时钟提供，测试通过依赖注入替换时钟。

| 接口 | 合同 |
|---|---|
| GET `/policy-proposals` | 返回当前用户候选及标准化 configuration、configuration_hash、validation_ready；不完整候选展示原始结构且 validation_ready=false。 |
| POST `/policies/discover` | 无请求体或 `{}`；以服务端时间读取最近 60 个本地日期的历史事实，返回新建/复用候选 ID、规则版本和跳过原因。只产生待审核候选及历史观察证据。 |
| POST `/policies/compile` | `text` 为 1–2000 字符非空文本，`engine` 默认为 `rules`；返回持久 compilation_id、原始解析、结构化配置/摘要以及候选 ID/当前状态。不完整结果保留草稿和问题，不建候选。 |
| POST `/policy-compilations/{id}/revise` | 完整 `configuration`；从当前用户的原编译记录读取固定日期基准，校验用户修订后产生可复核候选。原文和原始解析不改写。 |
| POST `/policy-proposals/{id}/confirm` | `accepted` 必须为布尔 true；`reviewed_hash` 必须对应用户查看的标准化候选。 |
| GET `/policies` | 当前数据库状态、effective_status、最新版本和 version_authorized；有效时间判断不依赖后台扫描。 |
| GET `/policies/{id}/versions` | 版本号升序，包含结构、确认记录、证据 ID、配置摘要及前一配置摘要。 |
| PATCH `/policies/{id}` | 完整 configuration、accepted、reviewed_hash、expected_version_id、reason（1–1000字符）及 idempotency_key（1–160字符）；追加确认版本。 |
| POST `/policies/{id}/suspend` | expected_version_id；停止持续授权并处理旧未提交动作。 |
| POST `/policies/{id}/revoke` | expected_version_id；撤销而不删除历史或自动回拨资金。 |

`validation_ready` 仅表示结构完整且通过 DSL 校验，不表示来源证据或引用必然有效，也不表示已授权。`version_authorized` 进一步检查当前版本、生命周期与确认依据，但仍不代表具体资金动作满足全部安全约束。

发现接口拒绝客户端时间、用户和执行权限字段。相同事实复用同一候选，关闭的候选不会因重复请求复活；已确认对象不再重复发现。新修订或历史模式失效会将旧待确认候选标记为 EXPIRED，确认后的策略不受发现操作修改。候选须通过独立确认接口，默认自动执行关闭。发现规则与局限见 [候选发现](policy-discovery.md)。

自然语言编译和结构化修订也不授予权限。编译的相对日期基准由服务端按用户时区确定并持久化；修订沿用原基准，同时按当前时间拒绝已经过期的日期。同源新修订使旧待确认候选过期，重放旧修订不能复活它或撤销更新的候选。已有确认的同源候选必须通过策略版本修改接口变更。客户端需展示完整配置、原文、解析问题和默认假设，随后用对应摘要单独确认；业务界面在 MVP-402 实现。

`engine=llm` 仅是可注入候选提供器的可选接口。默认 `LLM_ENABLED=false` 返回 `LLM_DISABLED`（422），即使开关开启，未配置提供器也返回 `LLM_UNAVAILABLE`（503）；当前工程没有外部网络提供器。规则模式不调用模型。LLM 结果仍经独立结构及范围校验，不能写 ACTIVE、确认记录或资金事实。部署时不能将仅开启环境变量误当成已经接通模型。

确认和修改的幂等重放返回首次命令的历史结果。因此响应的状态和版本应作为命令回执；界面每次操作后必须重新 GET `/policies` 获取当前状态，不能用历史 ACTIVE 回执绕过当前撤销、暂停、到期或版本检查。实际执行器以后必须再次调用授权资格检查。

变更结果包含 invalidated_action_ids、inflight_action_ids 和 requires_recompute。前者表示未提交旧动作已失效；后者要求后续对账处理，不代表系统已经完成对账或允许重新付款。requires_recompute 是待办语义，当前任务尚未实现资金引擎。

普通参数错误返回统一 422，陈旧版本、错误复核哈希或幂等内容冲突返回 409，对象不存在或不属于当前用户返回 404；所有错误包含 request_id，日志不打印原始请求配置。

`make policy-refresh` / `.\make.cmd policy-refresh` 使用可信当前时间实际落库处理生效/到期变化；可以重复执行。即使该命令尚未运行，资格检查和 GET effective_status 也不会继续承认过期授权。自动调度、暂停后恢复和完整在途对账留给后续任务。

金额及日期模板详见 [配置说明](policy-configuration.md)，服务状态和不可变性边界详见 [生命周期说明](policy-lifecycle.md)。
