# FULL-108 / FULL-206：节日建议的明确采纳界面

状态：独立功能模块交付中。原编号仍待完整验收；没有真人、浏览器或真实金融成功结论。

这是显式执行差量，保留原 ADVICE_ONLY 建议、原策略确认/hash 和所有早期失败。界面消费新 `full-seasonal-adoption-v1` 服务，只让用户采纳服务器已验证历史所给出的固定整数分金额。用户不能编辑金额、提交角色/时钟/银行事实，界面不会执行金融动作。

## 可接入导出

- `SeasonalReserveAdoptionPanel` 默认导出，props：`{fullPolicy: FullPolicy, userId: string, epochId: string | null, mutationBlocked?: boolean}`。只用于当前 SeasonalReservePolicy。
- 同文件 `SeasonalAdoptionOriginalRecoveryPanel`：无策略列表、当前身份或 props 前置，单独呈现已保存原键，只有 GET。父页面须把此入口置于策略详情之外，以免列表失败丢掉恢复入口。
- `recoverSeasonalAdoptionOperation()`、`useSeasonalAdoptionOperation()`、`getSeasonalAdoptionOperation()`：暴露 `pending / original_receipt / busy / recovering / storage_error`。Root App 须接全族门；其他族的 pending、损坏存储或 write-flight 会阻止新采纳。

独立文件位于 `apps/web/src/api/seasonal-reserve-adoptions.ts`、`features/seasonal-adoption-operation.ts`、`components/SeasonalReserveAdoptionPanel.tsx`。所有 DTO 从实际 `packages/contracts/schema.d.ts` 引用；不手写金融响应替代合同。

## 用户工作流与原件恢复

1. 用户明确输入窗口 ID；服务返回原登记公共窗口、完整历史覆盖与样本比较。未登记、过期、历史不完整等以 UNKNOWN/null 显示，不能确认。公共窗口只展示服务器本次原件，不捏造全年安排。
2. 用户点击只读预览，看到官方窗口、实际剩余保护期、服务器固定金额、上限限制、全证据分母和完整来源。金额字段没有编辑控件。
3. 用户手动读取 HttpOnly 本地 USER 会话、输入非空 exact 理由、勾选明确采纳。显示身份不证明真人，也不作为银行授权。
4. beforePOST 保存完整 user/policy/epoch/body/body JSON/request hash/reviewed scope/key。任意 POST 结果，包括 200、4xx 和连接丢失，都保留 pending。
5. 用户手动 GET `/seasonal-reserve-adoptions/commands/{epoch}/by-key/{key}`。只有 fresh GET 的完整原 command/body/review/source/evidence/hash 与保存请求相符，才能清本族待核对门。NOT_FOUND_NOT_FINAL、响应错误和保存失败均保留原身份，不自动重试或换键。
6. 历史回执只证明原采纳记录。当前 GET 的 VERIFIED/ADVICE_ONLY/UNKNOWN 与原件、全注册记录分母分别展示；不从历史回执继承当前保护有效性。刷新当前 GET 清除预览，策略用户/epoch/version/hash/status/reference 变化使旧预览不能确认。

使用既有 sessionStorage，键为 `bounded-funds-seasonal-adoption-operation-v1:{VITE_API_BASE_URL|same-origin}`。不保存密码、Cookie、Bearer 或身份授权缓存；原签名 principal 仅作为历史回执字段保留。

## 摘要与金额边界

原 Seasonal 配置 `quantile` 是 float，旧通用浏览器摘要工具仅允许整数，首轮因此 32 项失败。新 reader 仅允许 `type=seasonal_reserve` 配置中的 `quantile` 为 0.8..1 且最多四位小数，并保留 Python typed float `1.0` 的原 canonical 字节。其他小数、无法精确表示的整数分和 bool 金额仍拒绝。旧摘要工具没有修改。

新 reader 核原 request、UUID5 command/evidence identity、review/source/evidence hash、所有捕获 source evidence owner/hash/time、完整 source denominator、原确认与签名 USER 窗口。它不替代服务器银行/审计/历史统计验真；界面不计算金融可执行权限。大于 JS safe integer 的金额失败关闭，不四舍五入。

## 验证与未覆盖

真实模块检查原件：

- 首 sandbox 启动 EPERM：`docs/progress/evidence/W5/seasonal-adoption-ui-direct-20261006T015010Z-176a09c9`，未运行测试。
- 首 native 32 FAIL：`seasonal-adoption-ui-direct-native-20261006T015055Z-5c6a5f65`，完整原源码保存在 `.runtime/seasonal-ui-first-check-before-20261006T015009Z-9e0c5083`。
- 摘要修后 33 PASS / 6.95s：`seasonal-adoption-ui-quantile-repaired-20261006T015307Z-91d5035d`；scope 稳定 true，global false，仅 Root 两个新 Seasonal protection 源改变。后续同版本暂停/UNKNOWN 门差量独立保源并定向检查，不能把 33 旧结果标作新 34 最终结果。
- 最终检查及源 SHA 由新的 FINAL manifest 记录，不覆盖本段历史。

fixture JSON 来自原 Python domain/service-memory 纯夹具，显式 `SYNTHETIC_MODULE_FIXTURE_NOT_REAL_USER_OR_PG`；它不是银行/真人/生产演示证据。

未覆盖：Root 父页和全族门最终接线、实际当前完整历史正向采纳、实际签名 USER 丢响应恢复、真实新保护曲线消费、浏览器交互、全量验收。原短 seed 实际候选只证明不足/不采纳负链，不能替代上述正链。采纳撤回或替换没有新接口，重叠窗口被服务保守拒绝；同一采纳固定额不会随时间缩小或凭 UNKNOWN 自动释放。没有真实资金接口或真人研究结论。
