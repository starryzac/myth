# FULL-204 已登记实际来源的当前集合 v5

2026-10-06 12:30 北京时间，功能增量；原 FULL-204 仍为 PENDING。

新增 domain/services/api 的 `full_action_set_boundary_registered.py`，算法 `full-policy-registered-action-set-boundary-v5`，协议 `registered-action-set-input-v5`。保留完整 recovery-composed-v4 原快照，Release 和 Joint 必须与原 v4 使用同一完整 ActualActionSetInput、owner、epoch、时刻和输入 hash，不能把独立重新捕获的来源并成一份已证明的集合。服务只捕获一次 actual，交给 periodic/recovery/release/joint 四族；来源 id 的原副本冲突直接拒绝。

Release 只有完整分母、唯一键、无 shadow、无冲突、全已知时新增候选。Joint 只有完整分母、独立已证明同经济效果时才替换原候选；不同分配量仍 UNKNOWN，原动作不删除。旧独立 UNKNOWN、原来源问题和未支持 producer 保留；只重新计算旧 v4 两个汇总理由。完整输入超过 16 MiB 或候选超过 64 仍 UNKNOWN，不裁剪。

新 GET `/api/v1/boundary/registered-action-set/current` 不接受金额、权限、时钟或 query，不生产资金、授权或通知。当前 Root 新文件已可独立调用；Main/RRRO 注册、实际 schema、实际 PG 与界面接线待后续稳定合同安装。`notification_support=NOT_IMPLEMENTED_FOR_REGISTERED_V5`，不能沿用旧通知成功标签。

## 本批直接证据

- `W3/registered-v5-exact-family-composition-risk-20261006T041548Z-e5846828`：6 pure 风险 PASS，38.66s，scope stable。包括完整零 Release/Joint、内嵌原 v4、独立来源绑定拒绝、缺来源 UNKNOWN、权限变化签名变化；使用明确合成输入，不是银行实证。
- `W3/registered-v5-one-capture-and-current-http-risk-20261006T041827Z-52f2fb24`：2 PASS，13.69s，scope stable。四族接同一实际 capture 对象且仅捕获一次，HTTP owner/clock 来源、额外 query 422、POST 405、零 commit/flush。
- `W3/registered-v5-root-types-20261006T041827Z-861f2359`：5 个 Root 新源 strict PASS。

## 具体未覆盖及下一依赖

Joint 正臂合同正在独立收尾；其不同分配量执行能力未实现，当前 Full GoalAllocationPolicy 没有进入原 planner，不能清掉其原 unsupported。到期的新 USER 执行不是自动加入本集合的证明。完整实际非零 Release/Joint、持久观察/通知、真实浏览器、性能和 FULL-204 全量仍缺证据。

下一步在独立 Joint FINAL 后安装 Main/RRRO 并生成实际 schema；当前金融检查串行、所需共享源冻结，其他独立功能继续实现。旧 v1—v4 的输入/hash/失败和结果不改。

## 2026-10-06 12:38 北京时间：实际入口与单条真实链

v5 Main 和 GET RRRO 已安装。实际 schema `W3/registered-v5-current-actual-openapi-20261006T042620Z-2fd7d2f3` PASS；三 shared strict60711904、七源 static e60b372d PASS。新增真实 Main/RRRO 合同检查首轮23858216 FAILED15.11s 为合成 Request scope 缺 headers，Root 原件保 `.runtime/root-registered-current-mount-first-red-20261006T0427Z/`（SHA8b444eab）；生产门不变，只补真实所需 headers 后7399bc1d PASS1/15.93s。原6pure+2HTTP行为未变复用，旧失败不改。

Joint五源 FINAL80234250：37direct82810966 PASS197.03s，4 strict1eac398c/static/format PASS；实际节点只有collection/NOT_RUN。同经济效果可独立替代原候选；不同分配量、未消费的GoalAllocationPolicy以及其它未知依旧UNKNOWN，新通知仍NOT_IMPLEMENTED。独立联合USER ASK不同金额模块正实现，新增三表Root MODEL_ONLY尚未注册/迁移。

Root唯一真实金融 session88175 / `W4/actual-mature-user-ask-original-bank-key-current-functional-20261006T043608Z-715f5295` RUNNING，无终态。启动672个API/config/迁移精确源码保全于 `.runtime/root-current-functional-maturity-real-20261006T0436Z/source/`，source-manifest SHA1952bf6c3f31359a372e47c7b106d332bfabe0e3b2dbfa0ac55d41c676b06f85；argv SHA4a83c8d564a536fd2b93f7a676dde757e1832ddd604b8deb3bb3b6d29ac4825e。现有源HOLD，新未注册独立文件继续。该单条使用实际隔离 bf_test，正式库不reset/migrate。
