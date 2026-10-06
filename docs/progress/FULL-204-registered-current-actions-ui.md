# FULL-204 当前登记动作集合只读界面

2026-10-06 功能增量，PENDING。新增 `api/registered-action-set.ts`、`components/RegisteredActionSetPanel.tsx` 及各自 direct tests、两份 TOOL_ONLY fixture 文件；使用实际生成的 v5 DTO，不修改旧 reader、旧输入/hash、金融状态或原失败记录。

可运行能力：手动 GET 当前 v5，保留全部 v2/v3/v4/Release/Joint 原件；展示四家族、当前候选、原动作/未决分母、旧候选替换、27 表实际覆盖、全量不足原因与 nullable 签名。UNKNOWN 不显示为完整；缓存、正在刷新和失败刷新均不能沿用旧完整结论。组件没有 POST、通知或授权能力，不建立写门。Root 负责 App 宿主。

检查：修正后 30/30 direct PASS，Vitest 4.14s（wrapper 5.87626s），证据 `docs/progress/evidence/W6/registered-action-set-current-web-direct-repaired-20261006T051318Z-7e6539bd/manifest.json`；5 源 ESLint PASS，wrapper 3.48023s，`registered-action-set-current-web-static-repaired-20261006T051312Z-5a01393a`。两次 scoped/global 来源均稳定。

原失败完整保留：wholeWeb types `multi-template-preview-ui-final-current-web-types-20261006T050213Z-b79269f3`；首次 Vitest 因沙箱阻止 esbuild 启动 `0df65097`；首次 native direct `da625560` 为 26 PASS、4 FAIL（Joint 合成夹具只有 24 表、正确被旧 reader 拒绝；小数负例的测试重哈希自身提前拒绝）。失败精确源保 `.runtime/registered-action-set-web-first-type-red-20261006T050433Z/` 和 `.runtime/registered-action-set-web-first-direct-red-20261006T0510Z/`。修复只调整新 DTO 收窄和新合成夹具/负例，不修改冻结的旧 reader。零散纯夹具构建曾报缺 `relation_source_count` 和读取不存在的 `ActionSetInput.snapshot`，未产生银行结果，详情在 FINAL handoff。

具体未覆盖：Root App 接线/最终 wholeWeb types；生产 API 的非空四族联动实证；真实浏览器；性能；v5 持久观察/通知（API 自身仍 NOT_IMPLEMENTED_FOR_REGISTERED_V5）；所有 FULL-204 全量。客户端仅验响应一致性，不能独立证成银行真值或全局任意手动意图。详见 `docs/spec/registered-current-action-set-web-contract.md`。
