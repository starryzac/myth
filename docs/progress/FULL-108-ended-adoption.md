# FULL-108 原采纳在窗口结束后的只读证明

2026-10-06。`FUNCTIONAL_PROOF_IMPLEMENTED / NEW_PROJECTION_BRANCH_AND_ACTUAL_POSITIVE_PENDING`。本包补充真实到期原件读取能力，不关闭原 FULL-108；旧采纳 v1、保护 v3、历史 hash 和失败均保留。

## 已实现与文件

- `domain/full_seasonal_ended_adoption.py`：新显式 `seasonal-ended-adoption-proof-v1`、输入 `seasonal-ended-adoption-input-v1` 和 wrapper kind `VERIFIED_ENDED_SEASONAL_ADOPTION`。旧 `VERIFIED_SEASONAL_ADOPTION` 不接收或自动解释新证明。
- `services/full_seasonal_ended_adoption.py`：`read_current_ended_seasonal_adoption(session,user_id,policy_id,now)`，要求原干净 REPEATABLE READ/READ ONLY、实际模拟 User、Asia/Shanghai 时区和唯一同 owner OPEN epoch。只读，不新建建议、采纳、Evidence、DecisionRun、确认、付款或 claim。
- `tests/test_full_seasonal_ended_adoption.py`：合成原件直接风险；金额1800分来自既有确定性历史夹具，只是纯域手算，非真实财务效果。
- `tests/test_full_seasonal_ended_adoption_integration.py`：Root 独占执行的生成库缺采纳负链，仅收集，未运行数据库。

旧读取通过今天的 `_scope` 重跑 READY；到期后建议本身不能 READY，故旧分支 UNKNOWN。本服务不调用今天的 `_scope`，复用原 `_original` 及 `verify_frozen_seasonal_adoption_trace` 在采纳原时点复算所有历史统计、分类、官方窗口、金额、明确 USER、请求、原 evidence/trace/hash。再从今天同一 RRRO 原件核当前 Full policy/version/config/confirmation 仍完整相同。

## 完整来源和到期边界

查询该 owner 全部 `FULL_SEASONAL_ADOPTION` Evidence 原行，包括其他 epoch，完整数目和原 ID 列表不得由过滤后的空数组代替。每原行保留未修改完整 TypedTrace、adoption Evidence metadata 和实际当前全部历史 Evidence copies；逐字段与冻结 scope 原件相等。即使来源已经过有效期，旧统计在原采纳时点有效和完整才可保留历史证明；不能把已改变、缺失、被替换或篡改的原件当到期成功。

当前 audit 必须实际 EXACT VALID，chain/reference、原 OPEN head count/sequence/tail 与验证结果一致。缺当前审计、来源、不完整行分母、重复或重叠原窗口、未来原记录、不同 owner/epoch、新版本、新确认、挂起或撤销均 UNKNOWN/null。源容量200采纳、16MiB完整输入；超限 UNKNOWN，不截断成功。

只有服务器可信业务时刻转换为原 Asia/Shanghai **本地日严格大于**原 `protection_end` 时，`VERIFIED_ENDED / current_floor_cents=0`。结束日最后一微秒仍 UNKNOWN/null；UTC 同一天不能代替本地日。保留 original 原采纳、1800等原金额、官方窗口、统计来源和 `floor_released_on=end+1`，不改变现金或收入、不生成付款，不增加或转移原 Income。

自然 valid_until 到期仅可作为历史读取：原持久 status 仍 ACTIVE/CONFIRMED、版本和确认全部原件不变，当前 derived EXPIRED 必须由同原 valid_until 与实际 now 导出，planning_confirmation_valid=false。`current_permission_proven=false` 始终保留，不能当当前金融授权。明确挂起、撤销、手工 EXPIRED 或新版本不走此分支。

无原采纳时只返回 `NO_ORIGINAL_ADOPTION` 和实际完整0行分母，**floor和原金额均为null**，不能因此把其他未来义务或未支持窗口置零。原件读取失败且不能完整捕获时 count为null，不冒充0行。

原 current FullPolicyView 的 updated_at/confirmed_at/valid_from/valid_until 四个声明 DTO 时钟仅比较 aware 瞬时，允许同一瞬时的 Z/+00:00 表示；两个原 JSON、确认 content、trace/source bytes和所有hash保持原样。不能用一般文本规范化消除真实原 hash 漂移。

## Root 接线合同

`derive_ended_seasonal_adoption(EndedSeasonalAdoptionInput)` 重新计算完整结果、输入hash和proof_hash；`verify_ended_seasonal_adoption(source,proof,as_of,timezone,expected_user_id=...)` 必须重算相等，并与原 FullProtectionPolicySource 的身份、版本号、config、confirmation、时窗、来源和当前状态精确绑定。未知 proof 不通过。

新投影版本可追加严格对象 `{"kind":"VERIFIED_ENDED_SEASONAL_ADOPTION","proof":proof.model_dump(mode="json")}`，只在新 opt-in 分支中逐项调用 helper。需要将全部原采用/统计/确认 Evidence IDs 和当前原件纳入同次完整保护来源与轨迹；相同 proof 的全采纳分母保持一致。旧 v3 的未知 wrapper不能被静默移除或当成已释放；active/ended 对同原采纳冲突、重复和不同新窗口仍拒绝。Root 集成新 projection algorithm/producer/history verifier，旧输入/hash/数学完全不改。本包没有修改 Main/deps/旧投影、金融、模型或迁移。

## 已运行检查与原失败

| 原证据 | 实际结果 |
| --- | --- |
| W5 `ended-seasonal-first-direct-20261006T023256Z-3178f140` | 31PASS/2FAIL，9.26s；本地日夹具误比较 UTC date，以及手写旧 FullPolicyView fixture 缺原 no-authority DTO 默认字段。原源已留存 |
| W5 `ended-seasonal-first-types-20261006T023257Z-9ffbb8b6` | 1类型RED：新算法常量缺 Literal 注解；原输出保留 |
| W5 `ended-seasonal-corrected-direct-20261006T023543Z-eb4b7510` | 36PASS/1FAIL，8.58s；完整 current DTO 默认字段与手写原 fixture 差异仍被诚实拒绝；未放宽生产原件门 |
| W5 `ended-seasonal-complete-original-dto-direct-20261006T023732Z-97771e39` | 37PASS/8.80s，exit0，all/scoped stable=true；原夹具在生成其明确采纳前使用真实完整 FullPolicyView shape，包括原 no-authority字段 |
| W5 `ended-seasonal-complete-original-dto-types-20261006T023732Z-659aa5f2` | 当前4源严格 mypy PASS |
| W5 `ended-seasonal-complete-original-dto-static-20261006T023732Z-66638e90` | 当前4源 Ruff PASS，owned format已完成 |
| W5 `ended-seasonal-actual-absence-collection-only-20261006T023544Z-61cd38ee` | 1 collected/4.59s；生成库和任何金融未执行 |

首 source 与两轮 FAILED 完整字节保存 `.runtime/FULL-108-ended-adoption/first-source/`、`first-failed-source-20261006T0232Z/`、`second-failed-source-20261006T0235Z/`；原输出和失败状态不改。当前纯测试覆盖 end日/next日、原金额保留、0行null、审计/epoch/head/count/checkpoint、完整原来源、source metadata、hash/已重hash假金额、同窗重叠、新确认/版本、撤销/暂停、自然到期与无权限、原 DTO 同瞬时时钟、服务RRRO/零写约束。

## 未覆盖与下一依赖

- Root 尚未接新年度、执行保护、FullJoint、历史 projection 新算法分支；本服务可直接调用，但当前旧 v3 继续原 UNKNOWN，不称已修复现公开曲线。
- **真实原银行两历史窗口→明确签名USER采纳→时间越过 end→原历史回读→新保护消费者正链 NOT_RUN**。当前真实候选仅验证没有原采纳的完整null读取和全physical零写；不能替代正链。
- 跨 epoch 保留历史缺当前 Evidence 时仍 UNKNOWN，不借 archive 尚未验真的原件重造来源。不同新窗口、新确认、新版本如何组合历史下界仍未支持。
- 不运行 PG/browser/Docker/全量；真人研究未开展，新性能未测。原项验收和关闭保留后续真实证据要求。

Root 实际候选节点：`test_full_seasonal_ended_adoption_integration.py::test_actual_ended_read_without_original_adoption_is_null_and_all_physical_rows_unchanged`。下一步先注册新投影显式版本，保留旧反例，再由 Root 单金融链取真实正链证据。
