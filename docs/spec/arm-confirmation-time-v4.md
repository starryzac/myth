# 五臂原确认证据时钟修订 V4

执行修订 `W1_ARM_CONFIRMATION_ORIGINAL_INSTANT_V4`，不是历史时间/哈希改写。原 V1/V2/V3 源与证据继续保留；root接入前原件另存 `.runtime/W1-arm-confirmation-time-before-v4-20261005T072647Z-8af7ea24`。

首次GENERAL真实PG13节点：9 PASS、4 FAIL，241.58s，原件 `general-provider-installed-real-pg-20261005T071759Z-bebe15f9`。其中B3原确认真实生成，但工具将typed原件UTC `Z` 与原content `+00:00`逐字符比较，误拒绝同一瞬时。V4仅将valid_to/content.valid_until及observed_at/content.confirmed_at用原严格aware时钟解析器比较；仍重核主体、动作、原effect hash、原确认content实际hash、原有效窗口及实际actor时钟，不重算写回任何旧hash，不缓存权限。

69 TOOL_ONLY风险PASS（原67保留并新增合法UTC表示和真实时刻错配负例）、2源文件 strict mypy/Ruff通过。4失败节点复核3 PASS/1 FAIL、110.38s：B3真实逐动作明确确认通过，原bank commit后丢响应与原projection失败UNKNOWN/SETTLED/原hash/原占用/无伪回执通过。两个旧测试将银行状态误写为SUCCEEDED，修成原银行真实SETTLED；生产银行代码不改。旧4FAIL与此1FAIL原件保留。

收入份额节点随后真实通过：`general-provider-real-income-only-20261005T073435Z-1f8a8b6a` 1 PASS/20.10s。原银行普通消费耗尽所选账户旧现金后，再真实入账补回旧余额并多入100000分，保持原总现金边界；新PREPARED的income_uses为10000分，逐项绑定真实收入fragment，准备期间无BankOperation、原账本保持一致。旧legacy夹具拒绝及首次新消费/收入夹具 `EXECUTION_NOT_READY: BASELINE_LIQUIDITY_RISK` 原件均保留，未放宽网关。该夹具不是24正式case输入，不调整正式基线规则。原9已通过节点未改变行为，四失败按受影响范围分别获得3 PASS和1 PASS，没有为凑统一报告重跑无关节点。

三阶段capture目前仍NOT_PHASE_PROOF/economic=false，真实24×5未运行。原20/92关闭数、MVP-501/503 PENDING不变。
