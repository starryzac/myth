# 知余 · Zhiyu

基于 FastAPI、React 和 PostgreSQL 的模拟资金 Agent。包含规则配置、多目标规划、资产配置与支取、必要问题问答、持续授权和动作恢复代码。全部账户、银行回执与资金操作均为模拟，不连接真实银行。

金额、权限、资金边界、执行状态、幂等与恢复由确定性代码处理。未来未到账收入不扩大当前可安排金额。模型只用于理解需求与生成候选，不能直接授权资金动作。

## 工程结构

- `apps/api/app`：API、规则和规划领域、模拟银行、执行器、审计与后台任务。
- `apps/api/alembic`：数据库迁移。
- `apps/web`：前端源码，含扩展版、旧 Demo 和完整工程入口。
- `packages/contracts`：OpenAPI 与 TypeScript 合同。
- `scripts`：依赖安装、构建、合同生成、初始化与独立运行入口。
- `deploy`：容器构建与独立模拟部署配置。

这是当前已接入应用的工程快照。工程目录不附开发计划、过程记录、验收脚本、测试夹具、截图、对话记录、运行数据或本机密钥。功能实现与完整验收结论分别处理；本 README 不声明五能力整体已通过验收。

## 本地运行

需要 Python 3.12、uv、Node.js 24、pnpm 11.19.0 和 PostgreSQL 16。启动脚本要求独立目录名为 `bounded-funds-next`。

```powershell
git clone https://github.com/starryzac/myth.git bounded-funds-next
Set-Location bounded-funds-next
python scripts/tasks.py bootstrap
```

模拟数据库管理端默认使用 `127.0.0.1:54329`。尚无服务时可运行 `docker compose up -d db`；已有本地 PostgreSQL 时直接使用其管理连接。自定义管理连接通过当前会话的 `ZHIYU_NEXT_ADMIN_DATABASE_URL` 提供，不提交含口令的连接地址。

```powershell
uv run --frozen --no-dev python scripts/zhiyu_next.py prepare
uv run --frozen --no-dev python scripts/zhiyu_next.py start --round <prepare返回的完整轮次>
uv run --frozen --no-dev python scripts/zhiyu_next.py status --round <同一完整轮次>
```

扩展版地址为 `http://127.0.0.1:19273/zhiyu-next.html`，API 默认端口为 `19200`。每轮创建独立数据库、低权限角色和审计 epoch；运行信息与私有配置只写本地忽略的 `.runtime`。已有历史不会在启动时自动重置。

模型设置在本地页面填写并保存，再测试连接。密钥只进入本地后端私有配置，未配置时仍可使用离线规则入口。真实 DeepSeek 调用仍需在用户配置密钥后验证。

持续任务 worker 默认关闭。`app.workers.zhiyu_autonomy` 保留现有源码验证许可门；仓库不提供本机许可文件，不能直接启用该 worker 或以导出源码替代其许可。暂停阻止新执行，已有 UNKNOWN 动作继续使用原动作和银行键查询恢复。

## 构建与合同

```powershell
pnpm build          # 扩展版生产构建
pnpm build:all      # 完整入口、旧 Demo、扩展版
pnpm types          # 从 API 生成 OpenAPI 和 TypeScript 合同
```

三个前端构建输出分别是 `apps/web/dist`、`apps/web/dist-zhiyu`、`apps/web/dist-zhiyu-next`，不提交构建缓存。数据库迁移使用 `uv run --frozen --no-dev alembic upgrade head`，实际连接必须指向所属模拟环境。

`deploy/compose.demo.yaml` 是独立模拟容器配置，构建需要显式提供其中声明的镜像、摘要和运行变量；常规本地体验使用以上独立启动入口。
