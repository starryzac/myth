# 当次浏览器与存活审计的原件接线

状态：IMPLEMENTED_BINDING_TOOL_ONLY；尚未接入当次 `make check`。此项是 W1 的显式验收接线差量，不更改原编号、旧运行用途、金融断言或正式历史。

`scripts/w1_check_browser_outputs.py.capture(root,context_path,browser_path,browser_scoped)` 只读取原文件，返回六业务、三轮、原金融链和审计链四组 `MVP_NATIVE_V2` 语义输入及各原 producer run 的完整字节引用。实际语义仍由 exporter 重算，接线工具不会生成已付款、权限、损失或审计结论。

新运行必须在启动前登记 `purpose=MVP_ACCEPTANCE`，并绑定当前 final context 的原相对路径、字节 SHA、owner_run_id 和完整源码摘要。旧 DEVELOPMENT、部分范围、失败、未清理库、其他 owner、漂移源码、假 exit0、未执行 argv 均拒绝；旧结果不重新归属。核心源之外仅原 `.runtime/drive_mvp404_browser.py` 的精确不可变 SHA 可作为独立 oracle。必须保留原 browser 和其内层 observer 各自的 scoped manifest/source.before/source.after/log 与实际 run_id；不能将 observer 原件归为浏览器 wrapper。

三轮快照引用原 `round-N-reset-before` 的实际 BEGIN 快照，且前一原金额检查点须为 `rent-real-version-change`。三条编号分别 1/2/3、同一 scenario；不使用不存在的 round-complete 标签。全部 checkpoint、重置、HTTP、trace、截图、视频和审计原件索引逐文件 SHA 核验，不能只挑通过片段。Gzip/zip/WebM 标明 `NATIVE_PROOF_ONLY`，完整结构、financial/reset/audit 分母由各原语义门验证；不假标 JSON 或 UTF-8。

23 个 TOOL_ONLY 纯接线用例通过，原件 `current-check-browser-original-binding-final-pure-20261005T055307Z-8653a245`；两源 mypy 和 Ruff 原件分别 `055355Z-1cd61130`、`055358Z-34bf2162`。这些夹具故意没有有效银行/审计/Playwright 原件，只证明绑定门，不能关闭任何 MVP/FULL。最终 check 仍需 root 在现有核心源码冻结解除后接入真实浏览器和同步 observer producer，并取得当前源码全量验收。
# 2026-10-05 接入修订

`tasks.py` 的真实e2e分支现在为新browser预声明唯一output目录，调用原native scoped wrapper；仅在实际 `BOUNDEDFUNDS_FINAL_CONTEXT` 存在时传入原context。`w1_current_check_context.py` 在启动数据库/浏览器前核原context bytes、owner、隔离数据库名、四deferred组和完整当前源摘要。默认开发运行仍DEVELOPMENT；旧原件不能后补purpose。

新browser原manifest直接记录purpose/context；最终alive audit仍在同一owned库退出前，由父进程同步运行。验收context存在时同时执行实际scoped audit wrapper，并从该次原stdout唯一EVIDENCE_DIRECTORY取得所属manifest路径，不扫描最新目录。observer读父原manifest副本并核同一原context；任务调用本adapter核全部原件后登记四组。六业务/三轮/金融原链/审计语义仍由exporter逐项重算，不以登记动作判成功。

真实工具验证 `current-check-wired-binding-final-pure-20261005T063553Z-2c226dd1` **124 TOOL_ONLY PASS/6.25s**，另八文件types/static/format通过。原CLI pytest模块路径、namespace重复及同一task重复组的工具失败保留。此接入尚未实际执行初版全量check，没有接受旧浏览器结果或编造四组成功。

