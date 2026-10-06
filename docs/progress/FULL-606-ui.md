# FULL-606 固定收款关系前端

状态：真实 API reader、持久恢复状态和两个产品面板已实现；定向前端检查通过。Root App/策略中心接线由主任务完成。HTTP 单元夹具不是实际银行、PostgreSQL、浏览器或真人证据，原 FULL-606 尚未关闭。

## 可运行流程

`FixedPaymentRelationPanel` 消费真实当前 `FullPolicy`、当前账户原用户及当前 epoch，并让用户选择实际已授权的原 MVP 周期策略。完整服务端范围预览核对用户、两版本、银行身份原证据、现金账户身份、金额规则/单次限额、自然月、周期自主、有效窗口及完整摘要。随后由用户明确发起、明确确认关系，手动准备原周期动作。只有原实际 `ASK_ONCE` 提供逐次明确同意；`AUTO_EXECUTE` 仍须复核原动作再执行；`ADVISE_ONLY/BLOCKED` 不能通过同意升级。

使用已有 HttpOnly 本地 USER 会话与只读身份入口，没有公共 token 存储，金融 POST 不包含客户端角色、金额、时钟、银行结果或成功标签。当前 fetch 的同源 Cookie 行为沿用已有 HTTP 接缝；跨源部署必须由主任务另行验证，不能据单元夹具声称 Cookie 已在真实浏览器生效。

每条写命令发送前保存完整 user/epoch/Full、原 body 字节序列、原键、客户端摘要、原 scope、原 action/effect 身份。每个 POST 结果继续保留 pending。独立 GET 必须精确匹配原 envelope/body/UUID5、三份关系摘要、原准备绑定、USER 同意或指定原动作的完整 SETTLED 回执，才解除相应门。NOT_FOUND_NOT_FINAL、UNKNOWN、缺回执、篡改或存储错误继续保留原身份。恢复历史工作区不继承先前 GET 标记，执行前须重新读取原动作；不把历史记录缓存为当前权限。

仅支持用户手动恢复独立 GET 已验真的原 SETTLED 银行身份、同路径和完整原 execute body；仍受其他族 pending、实际 write flight 和外部 mutationBlocked 阻挡。新准备不能替换未终局旧付款，不自动 retry/keyswap。

`FixedPaymentOriginalRecoveryPanel` 不依赖当前策略列表、详情、账户读取或 USER 显示，只有手动原 GET，没有 POST。主任务在策略中心详情之外无条件挂载，避免来源读取失败时丢失原请求恢复入口。

## 接线合同

- 主面板 props：`{ fullPolicy: FullPolicy; userId: string; epochId: string | null; mutationBlocked?: boolean }`。实际 user 来自账户原响应，epoch 来自实际 demo/state；不能从 FullPolicy 猜 user。
- 独立恢复面板默认 export `FixedPaymentOriginalRecoveryPanel()`，无必需 props，无金融写操作。
- 状态接口：`recoverFixedPaymentOperation(): Promise<State>`、`useFixedPaymentOperation()`；State 提供 pending/busy/recovering/storage_error 和仅历史 workflow。Root App 将本族加入统一写门，给本面板传“其他族阻挡”，排除本族 pending 对指定原身份恢复的自锁。
- 存储 key 为 `bounded-funds-fixed-payment-operation-v1:<actual API base>`，使用 sessionStorage；没有自动 POST，没有存储或发放新的凭证。
- 全部 Payment* DTO alias 使用主任务实际生成的 `packages/contracts/schema.d.ts`。模块类型检查继承仓库真实 tsconfig，私有配置仅限制新增九文件及其实际导入图，没有替换金融 DTO。

## 已运行的直接检查

| 范围 | 实际结果 | 原 manifest |
| --- | --- | --- |
| API reader 11、store 11、主面板 5 | 当时整批 27 PASS，6.50s | `evidence/W4/fixed-payment-ui-final-direct-20261005T212637Z-ad762d26/manifest.json` |
| 同身份恢复外部门窄修后的 store 11、主面板 5 | 16 PASS，6.61s；API reader及其11例未改变 | `evidence/W4/fixed-payment-ui-resume-gate-direct-20261005T213049Z-f1addf97/manifest.json` |
| 独立原件恢复薄面板 | 3 PASS，3.93s | `evidence/W4/fixed-payment-ui-independent-recovery-direct-20261005T213356Z-b9e0ac99/manifest.json` |
| 新增九文件及真实导入图类型 | PASS | `evidence/W4/fixed-payment-ui-independent-recovery-types-20261005T213342Z-28a22b46/manifest.json` |
| 主面板七源 ESLint（恢复门窄修后） | PASS | `evidence/W4/fixed-payment-ui-resume-gate-static-20261005T213033Z-ccabda21/manifest.json` |
| 薄面板两源 ESLint | PASS | `evidence/W4/fixed-payment-ui-independent-recovery-static-20261005T213343Z-f78d9e1b/manifest.json` |

当前30个不同前端用例的证据由未变API11 + 最终store/主面板16 + 新薄面板3组成，没有声称一条最终命令跑了30例。精确源码、before/after、exit/log 和 scope/global稳定字段保存于 FINAL 归档。

## 原失败与尚未覆盖

首次 pnpm 名称未被 Python subprocess 正确定位（两个启动错误原目录保留）；直接 Node 后默认沙箱 esbuild spawn EPERM 原 FAILED 保留，原命令经受控放行后实际运行。首次原生测试22 PASS/1 FAIL，是夹具在已 recover 的 family 直接修改 storage而未改变实际内存门，修为实际 write flight，旧失败未改写。私有 tsconfig 的类型库相对定位 RED 与随后三个 nullable 类型 RED均原样保留，最终仅定位真实类型库并补确切非空门。

没有实际浏览器、刷新/多页并发、Cookie expiry、丢响应金融、UNKNOWN 银行恢复的端到端结果。Root 的实际金融首批失败发生在共享 RRRO 路由遗漏，修后第二批在 Full 未来账户流出未证明处正确 UNKNOWN；这些不是本前端金融成功证据。周期自主额度/到期、银行身份歧义、Agent 自生外付及新版本竞争完整矩阵仍需实际金融证明；未知银行收款身份创建未实现。下一步 Root 接入两个面板和全族写门，完成保守账户来源证明后统一运行直接金融链与真实产品浏览器验证。

## 最终原件稳定性补充

最终9源类型与两批ESLint的scope/global均稳定。恢复门16例及薄面板3例均scope稳定，但global分别因Root App/FullProductsPage、Root full_execution_protection独立变化为false；两个实际PASSED manifest保持这些原值。最终30例按上表未变API11/最终store与主面板16/薄面板3分别绑定，不将跨批结果重写为一次全源稳定测试。
