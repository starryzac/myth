# FULL-108 / FULL-206 季节建议显式采纳差量

2026-10-06：新增有限生产后端，原编号仍 PARTIAL，真实金融/前端验收未完成。原 FULL-108 计划410—420/1204—1206要求公共节日与相似历史比较、首次明确确认后生效；FULL-206要求来源引起的边界差分不能由 LLM 编造。本差量不改原建议、FullPolicy 配置、确认、历史哈希或旧保护算法。

## 产品合同

新 `/api/v1/seasonal-reserve-adoptions`：

- `POST /{policy_id}/preview`：RR READ ONLY；仅 `expected_version_id/window_id`。从当前已确认 SeasonalReservePolicy 读取全部统计参数，使用原服务器日历/实际历史建议服务，完整银行/收入/当前审计、Full 确认及来源通过后返回 `REVIEW_REQUIRED`、原整数额和 `reviewed_hash`。无完整来源则 UNKNOWN，scope/hash 为 null。
- `POST /{policy_id}/confirm`：签名本地 USER；同版本、window、epoch、reviewed_hash、accepted=true、reason 和原 key。客户端不能提供金额、事实、角色、时钟或结果。重读当前来源，只有完整匹配才能原事务追加 EvidenceItem（USER_CONFIRMED_POLICY / FULL_SEASONAL_ADOPTION）和原 DECISION_RECORDED / EVALUATION trace。此确认只采纳额外准备金，不执行付款、不生成银行授权。
- `GET /commands/{epoch_id}/by-key/{key}`：无需当前 USER cookie，只读恢复确切原请求、request_hash、scope/reviewed_hash、原 Evidence/trace/hash；缺原件为 NOT_FOUND_NOT_FINAL，不允许因此换键。身份对应原 user+epoch+key，跨策略或 body 改写同键拒绝。
- `GET /{policy_id}`：重新完整读取该用户当期采纳原件分母。没有采纳为 ADVICE_ONLY；已采纳但来源/版本/周期/原审计变化为 UNKNOWN，保留原确认和固定金额，不能补造0。

Local USER 由现有 HttpOnly 签名会话服务器映射；并非真人身份已核。算法 `full-seasonal-adoption-v1`，trace algorithms 恰为 `{trace:decision-trace-v1, seasonal_adoption:full-seasonal-adoption-v1}`。Root 需显式注册两个算法白名单、router、RRRO GET 与 preview 后生成实际 Schema。没有新 ORM/迁移，旧 FullPolicy `advice_only=true` 与 bank_authority=false 保持。

## 金额、窗口和避免重复

只采用原服务器 `proposed_adjustment_cents`，不是用户任意输入或 LLM 候选数字。固定整数额取原必要类别历史消费与普通基线的相同节日比较结果，nearest-rank/向上整分复算必须逐字段等于原建议；required/proposed/cap_limited 分别保留。完整且确实零消费的原银行覆盖可给0；缺原件/不足历史不能给0。配置必须与原官方 holiday_code、start/end、统计参数相符。

原官方全年窗口和当前有效窗口分别保留。正在进行的节日从真实今天起采纳原剩余期建议；原固定采纳以后不随日数递减，也不把过去天数当未来0重新登记。后来 fresh 建议可以按剩余天数重算，但这里只对完整历史源做当前性比较，保留原固定采纳额。完整银行覆盖证书、来源或 Full 版本后来变化时返回 UNKNOWN，需要新来源处理；不偷偷沿用旧确认或释放原保护。

同一用户/epoch 内任何重叠官方窗口已有原采纳时拒绝再次登记，包括其它 FullPolicy 或新 key；不把 stopped/stale 旧原件删除后再加一份。本最小版本未实现替换、撤销或金额调整，因此更改版本后不能绕过重复周期门。范围比仅按 policy_id 去重更保守。当前完整采纳原件容量200，原scope512KiB，超限拒绝、不截断历史。原 DECISION_RECORDED 自身字节预算仍适用，过大 trace 会事务失败，不声称确认已成功。

复核哈希只剔除确切三项读时点派生字段：scope.evaluated_at、原建议.as_of、原建议.source_digest（该旧digest含as_of）。其余观察/有效时刻、当前状态、全部原证据内容/hash、交易分类、窗口、完整覆盖分母和金额保留；同日纯读时钟变化可以匹配，真实事实变化不能匹配。当前性 source hash 只另外剔除逐日重算的 scaled_excess，保留原比较总额/分数/原交易IDs与完整来源。没有跨请求权限缓存。

## Root 保护接缝

`services.full_seasonal_adoption.read_current_seasonal_adoption(session,user_id,policy_id,now) -> SeasonalAdoptionProof`。

`domain.full_seasonal_adoption.verify_seasonal_adoption(source,proof,as_of,timezone)->bool`。Proof 为 VERIFIED 才可进入新保护算法；携带原完整采纳、原 Evidence/hash、trace/hash、fresh scope、实际采纳总数与全部原 command IDs。核同 owner/epoch/版本/确认/来源/窗口和原固定金额；未采纳/缺源/过期/UNKNOWN 返回false。未来窗口可以有当前有效证明，其保护曲线日期映射由 Root 明确新分支处理。

共享审计的精确重放入口 `verify_frozen_seasonal_adoption_trace(trace:DecisionTrace)->SeasonalAdoptionOriginal`，仅原算法对；复核原完整 trace/hash、原采纳整数复算、命令/body/时间/原 source metadata/内容和完整分母。不是只有字符串白名单。Root两处审计读取需显式调用该入口；新增原重哈希source_ref篡改仍拒绝的风险检查。

建议用已有 source.reference_snapshots 追加 `{kind:VERIFIED_SEASONAL_ADOPTION,proof:<原dump>}`，仅新算法消费；未验证采纳不能添加默认金额或变旧source/hash。Root须保所有其它保护/占用与未来收入0；同 period 只加固定采纳一份。新旧曲线最低余量/可购额度以及数值来源差分尚未由本模块计算，不能把本 trace 的存在说成 FULL-206 全部完成。

## 检查、原失败与实测边界

新7文件为 domain/service/router、domain/service/API三个纯风险测试和一个 actual PG 候选。纯来源采用原 ORM/Evidence/coverage 形状的明确 synthetic fixture，不冒充真实用户或银行历史。首31 domain PASS1.99s；新增service/API首批17 PASS1 FAIL（本地测试secret不足24字节导致真实登录401），原件 `W5/seasonal-adoption-service-and-json-pure-20261006T012228Z-3ee9d16b` 保留。修夹具后34 domain+API PASS4.59s；类型RED25/3/1项原源与日志独立保留，修复为类型限定/import/局部注解，不放松金额/角色/源门。最终完整相关结果另在本包FINAL记录。

唯一 actual PG 候选 `test_full_seasonal_adoption_integration.py::test_actual_short_seed_seasonal_adoption_remains_advice_only_and_cannot_confirm` 已 collection，未运行。原真实60日seed无两份同节日完整历史，候选要求 UNKNOWN/ADVICE_ONLY、签名USER也无法确认、原key NOT_FOUND_NOT_FINAL、全部物理行零写。不得为了通过而补造两旧节日、已付0或虚构成功。本次没有运行 PG/浏览器/全量，没有修改正式历史。

尚缺实际完整旧历史下的采纳正向数据库、响应丢失同键恢复数据库、Root新保护曲线/差分、独立UI及真实交互验收。官方日历仍仅原2024—2026中国窗口，其它时区/年份和旅行活动声明未支持；原最小参数范围lookback<=1096/minimum>=2/quantile>=8000bps/cap<=500000，Full配置超此范围UNKNOWN，不自动降低门。策略自然生效状态、证书或元数据变化也可能使原采纳UNKNOWN，此时保原金额，不认定保护已解除。全量验收和 FULL 关闭保持待完成。

## 后端最终检查追加

最终独立测试为50个unique risk：31 domain、16 service、3真实FastAPI/签名JSON。`seasonal-adoption-complete-source-pure-20261006T012712Z-1fdc5603` 49 PASS6.85s；新完整 frozen trace gate 首轮47 PASS3 FAIL（误用TraceEvidence.status，真实字段为status_at_decision），原FAILED/typeRED保留。只改准确属性后受影响service16 PASS2.88s，未重复31+3行为未变检查。合并复用不能称同一次50全量。

最终七源 mypy strict/Ruff/format PASS；actual候选1 test collected4.83s，PG仍NOT_RUN。原类型、401夹具、frozen trace属性失败日志和可辨别版本字节在 `.runtime/seasonal-adoption-*` 与 `docs/progress/evidence/W5/seasonal-adoption-*` 原路径保留；FINAL source manifest另列checks及scope/global实际稳定字段。缓存写入WinError5/Starlette弃用警告没有转成失败或自动安装依赖。

2026-10-06 01:36 UTC 原 FINAL 后必要修订：空 owner-filtered 采纳列表也先核当前完整 audit，再允许 ADVICE_ONLY。否则被篡改 owner/source 的原采纳可能被过滤成0条。本包新增空分母＋audit INTEGRITY_ERROR 必拒的直接风险；只读相同请求 audit_scope 可复用，不能跨请求缓存。受影响service17 PASS2.91s，七源 strict/Ruff PASS；此前backend FINAL原字节不改，新FINAL另行绑定。unique risk合计51（原31+3行为未变不重跑），不是同一次最终51全量执行。
