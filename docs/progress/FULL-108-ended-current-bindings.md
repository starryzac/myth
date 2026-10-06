# FULL-108 结束采纳窗口的显式当前绑定

记录时间：2026-10-06 11:00 北京时间。新增独立 `full_seasonal_current_protection.py`，算法标识 `registered-full-protection-ended-seasonal-v4`；旧 v3 消费器、原统计建议、原采纳 JSON 与哈希不改。

新绑定完整验证 `VERIFIED_ENDED_SEASONAL_ADOPTION` 原件 wrapper，复算原采纳、全 owner 行/命令分母与当前审计头。只在原本地结束日已过、当前原策略和版本仍匹配时释放该保护下界为零；现金不增加、不产生支付、不授予当前资金权限。活动采纳仍使用原 v3 金额与各日原保护结束日期。多个证明的 owner、epoch、审计头与命令分母必须一致，重复策略或原源哈希冲突继续有明确原因。

10 项直接风险 `W2/ended-original-current-seasonal-binding-fixed-risk-20261006T025805Z-3998a438` PASS，4.61 秒；两源 strict `ae05e734` PASS，Ruff/format 通过。首次运行时 TYPE_CHECKING 注解未延迟导致 collection ERROR `99423f8e`，两处类型错误 `d9e6583b` 原样保留；窄补 future annotations、异类循环变量和测试字典注解，没有放宽证明门。初始源存于 `.runtime/root-new-bindings-before-fix-20261006T0257Z/`。

具体未覆盖：年度曲线、完整目标分配和执行保护尚未接新 v4，不能声称结束窗口后产品已释放金额。真实 Signed USER 采纳正链、实际结束日后的回读、银行全表零写与浏览器仍 NOT_RUN。上游四源结束证明的 37 项直接风险仅为原件数学/服务合同，真实负向候选也仅 collected。当前绑定测试夹具明确 synthetic，不是真实银行、PG 或真人证据。

本包快照见 `.runtime/root-new-independent-functional-final-20261006T0301Z/manifest.json`（SHA `38f80cc7ac08276cf9ca16544bf8b294c2e895b7e9540969b0b462ac577befa8`）；当前 FULL-108 仍 PENDING，正式关闭 21/92，全量验收待最后集中进行。

## 2026-10-06 11:38 北京时间：当前消费者交付

上述“尚未接线”是 11:00 时点记录。现新 v4 已接真实当前保护读取、年度 1098 点、完整目标分配和原执行保护；未请求结束证明时仍按原算法和输入派生原结果。只在窗口已过且结束采纳原件完全匹配时将该保护下界归零，原采纳金额仍可读，现金和权限不增加。没有采纳原件时不生成假的零额结束证明；有采纳但不完整时保留 UNKNOWN/null。

`W2/ended-seasonal-v4-current-and-original-consumers-fixed-risk-20261006T031111Z-8ba25df4` 的 19 项相关直接风险 PASS10.85s，7 源 strict `1595df5b` PASS。首次循环 import 导致 `0310393e` collection ERROR 已保留；只将新消费者 imports 延后，冻结的 ended-v1 原件算法字节未改。前端新 v4 和旧 v3 各两项 reader case 通过；所在批次 `404206ca` 因另一个宿主夹具缺 simulation 整批 FAILED，不能把整批记为成功。窄补该宿主测试夹具后两项宿主通过 `52a5b7aa`，当前整体前端类型 `6221340f` 和六文件 lint `d01a41f3` PASS。12 个篡改分支集中在一个 reader case，不扩充为12个独立测试。

真实 Signed USER 采纳+结束日正链、真实 v4 联动银行/完整原表零写、浏览器和版本全量仍 NOT_RUN。合成夹具的 audit.complete=false/SYNTHETIC 保持原标签。FULL-108 仍 PENDING，不改此前原失败或历史哈希。
