# 知余

知余是一个围绕生活开支、储蓄目标和资金安排的个人财务应用。它想解决一个很日常的问题：钱到账以后，扣除接下来要用的钱，还能拿出多少去完成自己的目标？

你可以设置生活准备金和储蓄规则，让多个目标共同分配新增收入，也可以查看账单、模拟资产配置、执行记录和原始回执。涉及资金的操作需要明确的规则和授权；遇到结果不确定的请求，系统会保留原记录并继续查询，不会简单换一个请求再扣一次。

当前仓库使用 Go 后端、React 前端和 PostgreSQL。银行服务是独立的本地模拟程序，所有余额、产品与交易都用于模拟，不连接真实银行或真实资金。

## 可以做什么

- 查看当前资金、目标进度、现金流预测和操作历史。
- 设置生活准备金、储蓄目标及分配规则，并在确认后授予对应权限。
- 预览单目标和多目标资金安排，处理模拟账单、目标调拨与资产操作。
- 由后台任务处理已授权操作，并保留处理中、结果未知和最终结清的状态。
- 查看审计记录和导入的历史资料，按原请求恢复中断的操作。
- 选择接入 DeepSeek，辅助理解问题和整理候选方案。模型不能替用户确认授权，也不能决定实际扣款。

这是仍在开发中的 Go 版本。仓库提供当前软件源码，完整场景验收与性能收尾仍在进行。

## 构建

需要 Go 1.27.2、Node.js 22.12 或更新版本，以及 pnpm 11.25.0。完整运行还需要本机 PostgreSQL 16。

```sh
go mod download
go build -trimpath -o bin/zhiyu ./cmd/zhiyu
go build -trimpath -o bin/banksim ./cmd/banksim
go build -trimpath -o bin/audit-anchor ./cmd/audit-anchor

cd web
pnpm install --frozen-lockfile
pnpm build
```

Windows 下请给输出文件加上 `.exe` 后缀。后端不会自动读取旧项目的数据库或配置；运行配置需要显式提供。

## 运行

首次启动和配置字段见 [本地运行说明](docs/running.md)。API 默认监听 `127.0.0.1:19310`，模拟银行监听 `127.0.0.1:19311`，前端入口为 `http://127.0.0.1:5178`。

配置完成后，分别在终端运行：

```sh
./bin/banksim -config .runtime/bank-runtime.json -protocol-v2 -credit-inventory
./bin/zhiyu api -config .runtime/app-runtime.json
./bin/zhiyu worker -config .runtime/app-runtime.json
./bin/zhiyu native-standing -config .runtime/app-runtime.json
./bin/zhiyu projection -config .runtime/app-runtime.json
```

前端在 `web` 目录运行 `pnpm dev`。`pnpm preview` 只用于预览构建产物，不会代理后端接口。

## 代码结构

```text
cmd/          应用、模拟银行与离线审计签名入口
internal/     资金规则、规划、授权、执行、审计和接口实现
db/           数据库迁移、查询与内嵌银行结构
api/openapi/  HTTP 接口定义
web/          React 页面与前端资源
config/       可选模型配置示例
```

金额以整数分处理；未来收入不会被当成已经到账的钱。模拟操作的请求、授权和回执会保留关联，方便解释一次安排为什么发生、做到哪一步，以及失败后该从哪里继续。
