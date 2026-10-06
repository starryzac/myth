# 原生Edge录屏运行时路由

2026-10-05显式运行修订：`current-scenario-native-three-rounds-20261005T081042Z-6c07bdcd` / native `w1-20261005T081043Z-d65de08b` FAILED。API/Web、原种子/基线通过，但创建浏览器页面时需要的ffmpeg路径为不存在的E盘缓存，零UI业务checkpoints；原trace/results/log/FAILED及cleanup VERIFIED_ABSENT保留。

仓库已有真实 `.runtime/playwright-browsers/ffmpeg-1011/ffmpeg-win64.exe`，原SHA `5b8f3f59ba61685828939ff3c833109748adbdea2fff4b4ae570c9fc0fc1ff4d`，实际version `n7.0.1-playwright-build-1011`。read-only核前后SHA一致，原version stdout/stderr见 `.runtime/W1-native-decoder-route-20261005T081658Z-3a424fc2`。未安装、移动或改写该binary，未修改系统环境或应用源码。

随后单命令显式设置PLAYWRIGHT_BROWSERS_PATH为此原目录，启动新 `current-scenario-native-three-rounds-decoder-route-20261005T081704Z-1ded0461` / native `w1-20261005T081705Z-a7ca37b4`。只用新生成自有bf_test库，原source继续冻结。当前真实UI运行中：原工资、消费恢复、历史恢复读取零写的checkpoints已取得；三完整轮/最终alive审计尚未完成。此记录不关闭MVP-404或初版全量，不能把原失败改成成功。

后续所有原生录屏命令须绑定此实际存在且核验过的binary及运行目录；时长/完整解码和业务内容分别验，不能以metadata或旧失败录屏代替当前黄金链。

## 后续真实结果及显式截图修订

当前活动快照（2026-10-05 08:49 UTC）：仍 **20/92**，W1 IN_PROGRESS。实际三轮session1707已结束FAILED，native `w1-20261005T081705Z-a7ca37b4`：首轮业务/验证/真实reset完成，第二轮工资/消费恢复/历史读取零写三个checkpoints通过，随后测试第二次写fixed-ask-stage-0.native.txt触发EEXIST。共12checkpoints，三轮/最终alive审计未完成，cleanup VERIFIED_ABSENT；原日志/视频/trace/失败不改。更早session96371零UI因E盘ffmpeg缺失FAILED保留，真实仓库decoder单命令路由已有核验。root已归档Web原source，为每次截图添加独立序号且保留wx拒覆写；新Web typecheck PASS，尚未重跑三轮。

新增序号只避免新轮的原截图文件名重复，不覆盖旧原件，不放宽任何业务、银行、审计或reset断言。此时尚无三轮成功。
