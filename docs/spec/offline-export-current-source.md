# 当前源码离线导出原件校验

执行修订 `W1_NATIVE_OFFLINE_ORIGINALS_FULL_REPLAY_V1`。新 `scripts/w1_offline_export_validation.py` 接入正式 exporter 的 `offline_three_golden_chains` 语义组；原候选、旧导出器冻结、旧失败和旧64MiB工具保持原件。新组已实现校验能力，不代表已有合格原件。

必须具备同一当前源码/HEAD、不可变owner与Compose镜像绑定的真实启动、实际断网、API/DB/Web分别IP/DNS/default-route探测和回环入口。四个原生Edge用例覆盖三黄金链及定存有损确认；全部22检查点、4真实reset、每份实际512协议stdin/stdout/退出码、完整24物理表、所有user×epoch完整审计核验、原money/reset oracle重放均保留。请求/WS/原relay分母完整，真实trace/WebM/PNG和退出前最终活库快照必须一致。仅成功字段、初态审计、健康页200及无实际运行的工具测试不能通过。

新增导出器同时支持独立命令行的受控本仓库模型加载，恢复原sys.path；算法源和注册原件仍逐份核SHA。只读导出，不启动Docker、浏览器、数据库或金融链，不声称主机防火墙隔离，不将四离线用例改标七项/三轮。

实际验证：`offline-export-integration-pure-20261005T071419Z-998c37d9` 211 TOOL_ONLY PASS、1项原Windows1314符号链接能力跳过，10.66s；2源文件 strict mypy 和 Ruff 通过。此前候选30项、协调器90项及512传输36项为独立工具风险证据，保留其范围。完整校验body、Docker内新512、真实四离线用例仍 NOT_RUN。

下一前置：冻结新版API/前端/工具源，新owner注册构建并真实启动；旧852镜像不满足当前源。实际隔离与探测后再启原回环relay与协调器，全部原件进入本组校验后才有离线验收结论。MVP-504仍PENDING，原关闭数20/92不变。
