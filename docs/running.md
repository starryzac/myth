# 本地运行

本项目需要两个相互隔离的 PostgreSQL 数据库：应用库和模拟银行库。这里以环境 `zygo_local` 为例；数据库名称、角色和配置字段需要保持一致。

## 数据库与配置

在本机 PostgreSQL 16 中创建三个登录角色 `zygo_app_owner`、`zygo_app`、`zygo_bank`，分别设置自己的密码。创建 `zygo_local_app` 并指定所有者为 `zygo_app_owner`，创建 `zygo_local_bank` 并指定所有者为 `zygo_bank`。应用角色 `zygo_app` 需要连接应用库及使用 public schema 的权限，但不要授予它银行库或迁移所有者的权限。迁移会设置应用表的访问权限。

创建本地 `.runtime` 目录，按下面结构准备三个 JSON 文件。所有 `YOUR_*` 都需要替换。`bank_permit_secret` 使用自己生成的至少 32 字节随机字符串，应用与银行两侧必须一致。这些文件不要提交到 Git。

`.runtime/migration-runtime.json`：

```json
{
  "role": "migration",
  "environment": "zygo_local",
  "namespace": "zygo_local",
  "simulation": true,
  "migration_dsn": "postgresql://zygo_app_owner:YOUR_OWNER_PASSWORD@127.0.0.1:55439/zygo_local_app"
}
```

`.runtime/app-runtime.json`：

```json
{
  "role": "app",
  "environment": "zygo_local",
  "namespace": "zygo_local",
  "simulation": true,
  "listen_addr": "127.0.0.1:19310",
  "app_dsn": "postgresql://zygo_app:YOUR_APP_PASSWORD@127.0.0.1:55439/zygo_local_app",
  "bank_url": "http://127.0.0.1:19311",
  "bank_permit_secret": "YOUR_GENERATED_RANDOM_SHARED_SECRET",
  "web_origin": "http://127.0.0.1:5178"
}
```

`.runtime/bank-runtime.json`：

```json
{
  "role": "bank",
  "environment": "zygo_local",
  "bank_dsn": "postgresql://zygo_bank:YOUR_BANK_PASSWORD@127.0.0.1:55439/zygo_local_bank",
  "bank_permit_secret": "YOUR_GENERATED_RANDOM_SHARED_SECRET"
}
```

这里的 55439 只是端口示例，改成你实际 PostgreSQL 的监听端口。连接地址须为本机地址；数据库名称须与环境对应。密码中有 URI 特殊字符时，需要进行 URL 编码。

首次迁移应用库：

```sh
./bin/zhiyu migrate -config .runtime/migration-runtime.json
```

模拟银行首次启动需要显式应用它自己的结构：

```sh
./bin/banksim -config .runtime/bank-runtime.json -migrate -protocol-v2 -migrate-v2 -credit-inventory -migrate-credit
```

## 初始化一个空的模拟用户

创建 `.runtime/demo-seed.json`，填入自己生成的四个不同 UUID、至少 32 字节的随机登录秘密、当前或过去的 UTC 时间。字段如下：

```json
{
  "protocol": "zhiyu-demo-seed-v1",
  "namespace": "zygo_local",
  "environment_id": "YOUR_ENVIRONMENT_UUID",
  "user_id": "YOUR_USER_UUID",
  "account_id": "YOUR_ACCOUNT_UUID",
  "goal_id": "YOUR_GOAL_UUID",
  "login_secret": "YOUR_GENERATED_RANDOM_LOGIN_SECRET",
  "target_cents": "100000",
  "created_at": "YOUR_UTC_TIMESTAMP"
}
```

创建 `.runtime/seed-runtime.json`，使用 `role: "seed"`，加入与上文一致的 `environment`、`namespace`、`simulation: true`、`app_dsn`、`bank_dsn`、`bank_permit_secret` 和 `seed_state: ".runtime/demo-seed.json"`。不要加入 `migration_dsn`。

```sh
./bin/zhiyu seed -config .runtime/seed-runtime.json
```

该命令创建零现金的模拟用户和初始目标，不会代替用户确认策略或授权。此后可启动 API 和前端，使用模拟用户 ID 和登录秘密登录。先在页面确认规则及对应授权，再通过 `demo-income` 导入一笔真实发生于本地模拟银行的合成收入：

```sh
./bin/zhiyu demo-income -config .runtime/seed-runtime.json -amount-cents 10000
```

保留 seed 状态文件；它记录已准备的原始收入身份，重复执行不会改成一笔新收入。不同产品能力还需要对应的模拟银行产品或账单来源配置，空用户不会自动生成这些资料。

## 后台任务和可选功能

金融执行使用 `worker`，新版持续规则使用 `native-standing`，读取投影使用 `projection`；审计任务使用 `audit-worker`。它们均读取显式 `-config .runtime/app-runtime.json`，建议分别运行。旧协议兼容消费者的命令为 `standing`。

DeepSeek 配置示例位于 `config/deepseek.example.json`。需要时将它复制为 `.runtime/deepseek.json`，启用并通过 `ZYGO_DEEPSEEK_API_KEY` 环境变量提供自己的密钥。不配置模型时，结构化财务页面仍可使用。

外部历史资料、账单来源和审计信任配置按各自协议显式提供。离线审计签名入口为 `audit-anchor`，私钥由操作人员单独保管；API 不持有签名私钥。未配置的来源或审计范围会返回未配置或不完整状态。

运行期间请保留 `.runtime` 和两个数据库。暂停、撤销、结果未知或进程退出并不等于银行侧没有效果；恢复时应查询原操作，而不是删除记录后重做。
