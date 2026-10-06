# FULL-307 / FULL-704 联合目标固定批次 USER ASK 前端差量

状态：PENDING，生产消费者已实现，集中验收、真实浏览器和本包金融证明尚未运行。旧单目标动态执行、联合只读规划、旧前端及所有失败原件保留。

## 实际合同与功能

仅消费已注册 `/api/v1/joint-goal-actions` 原接口：POST `preview` / `prepare` 的四个实际身份字段、原计划整组 `confirm` 的明确接受/hash/epoch/key，以及 `execute-child` 的原整组hash/epoch/固定子编号/action。GET原plan与原准备/确认key始终可达。类型来自Root实际Main生成的 `FullJoint*`，不输入钱、bank facts/results、clock、role或授权。

服务器完整计划保留2—8个实际目标和1098保护节点、全部原收入和来源；前端展示完整父计划、所有固定子command/effect/hash、原model证据、银行原键、整组同意和逐子原服务状态。整组未保留资金，只有原子动作使用原资金占用，跨子原子性/整体回滚不可用。预览和整组同意不等于金融完成，只有用户逐次明确执行固定子；前子UNKNOWN/SUBMITTED仅能恢复该原子，后子禁止推进。

每次真实POST前独立GET当前Signed Local USER状态，并由原服务器再次核验签名/owner/时间及当前财务/权限。身份不会作为跨请求授权缓存，也不进入客户端金融body。UI身份不是真人研究或银行授权。

## 原请求与恢复

POST之前严格保存完整body、原body JSON、实际SHA、父身份/owner/epoch/hash/准备时间，以及全部固定子command、原model ID/hash和range hash。网络、解析、4xx、成功POST都保留pending。只有独立原GET的实际原body/hash/kind和固定身份完整匹配才解除该请求；NOT_FOUND_NOT_FINAL永不清门或换key。EXECUTE仅指定原子的原服务已结算回执才释放，不以另一子或latest代替。原工作区未全部终局时禁止替代父计划或新key。

完整合成reader夹具8,125,236字节，说明把whole原计划重复塞sessionStorage会碰常见容量限制。本包采用经Root明确认可的紧凑原定位协议：保存**全部**固定子和原POST，服务器完整来源/1098分母未截断。完整原HTTP封套可在本次GET展开/下载。重载仅恢复定位，不自动GET/POST、不当已复核或授权；必须手动完整原GET匹配后才能继续。sessionStorage拒绝/坏数据/写失败锁住新请求并保留旧记录。停止/封存且未证明全部原服务回执的工作区继续未决，当前没有安全取消整个计划的原协议，不假关闭。

原完整任意Evidence/Run JSON允许有限float，JS解析不能恢复Python `1.0` 数字token。保留服务器原plan_hash，明确 `SERVER_ORIGINAL_HASH_NOT_INDEPENDENTLY_RECOMPUTED`。本地结构比较不是审计、optimizer或经济验证；小请求和固定整数金额经济效果SHA使用原规则核对。完整数学/来源/审计/当前权限由实际服务的frozen/current verifier负责。

## Root宿主与全局门

组件 `FullJointGoalExecutionPanel` props `{ userId?:string, epochId?:string, mutationBlocked?:boolean, showOriginalRecovery?:boolean }`；最后一项默认false以避免重复。named export `FullJointGoalOriginalRecoveryPanel` 无props、仅GET、没有确认/执行/POST，可由Root放全局financial fieldset外。mutationBlocked仅其他族写门，主执行组件的本族原请求手动POST恢复仍受这个门限制。适合独立联合规划/目标宿主挂载，旧GoalsPage/App由Root所有。

操作模块导出 `recoverFullJointGoalOperation` / `useFullJointGoalOperation` / `getFullJointGoalOperation`，状态 `{ pending, workspace_reference, original, pending_read_verified, busy, recovering, storage_error }`。Root必须将 `pending || workspace_reference || busy || recovering || storage_error` 纳入其它族写、reset/logout门。`original`仅本次内存完整server读；`workspace_reference`是持久原定位，不是当前权限。`pending_read_verified`是本次独立原GET核对标记，不持久、不授权限，发送同原请求后即失效；非终局GET只允许随后用户明确恢复同一body，不自动POST。`isFullJointGoalWorkspaceUnresolved()` 返回定位是否仍在；每次独立完整GET全子原服务回执终局后才释放定位。只读API不需当前列表/version/cookie完成原件恢复，写入仍必须实际USER。

## 检查与未覆盖

最终三个相关模块44测试PASS、0skip、19.83s；7个TS入口及实际imports沿原项目严格配置typecheck退出0，7源ESLint退出0。原终态分别保存 `docs/progress/evidence/W6/joint-goal-ui-thin-final-direct-20261006T053220Z-9211762e`、`joint-goal-ui-thin-final-types-20261006T053217Z-e2d9a16f`、`joint-goal-ui-thin-final-static-20261006T053217Z-dc6cedc4`。三个manifest均 `all_source_stable=true, scoped_source_stable=true`；只是当次直接检查稳定，不称wholeWebtypes或全量验收。Root集中做宿主wholeWeb类型。

首轮41直接PASS及静态PASS保留；类型首红5处、第二红3处收窄/10条mock未使用变量保留。必要追加父状态/前子阻断风险后一轮42PASS、1个默认5s超时保留（8MB合成完整GET链）；只给该工具测试显式15s，产品、银行、其他预算未改，最终该节点3.333s实际通过。所有negative断言仍保留，无失败重标。HTTP夹具明确TOOL_ONLY；JSON fixture由原纯domain合成输入产生，保存来源SHA，未访问DB/银行/浏览器，不能当经济结果。

本批不运行PG、实际0015迁移、银行执行、浏览器或全量。Root共享hook/迁移/宿主/全局门安装与真实场景证明须独立登记。周节奏、多期全局最优、部分/有损/跨子原子性、真实经济实验、当前真实资金接口均未证明。FULL-307/704整体及其它编号不凭文件复用关闭。
