# Nantai Studio 阶段式安全任务工作台设计

日期：2026-07-15
状态：用户已批准设计方向，等待书面规格复核

## 1. 目标

把当前只读 Studio 从“产物状态查看器”推进为可在本机安全执行 Nantai 管线的阶段式工作台。
用户可以从现有六阶段导航启动任务、观察结构化进度、取消运行、检查失败并创建保留来源的重试；
同时继续遵守现有真实性原则：任务成功不等于几何可信，只有正式 artifact 与 provenance/fidelity
门禁通过后，界面才能提升 trust 或发布状态。

本切片解决 2026-07-15 交互审计中的四项问题：空项目主操作死路、只读服务展示伪写操作、
运行任务不可取消、失败任务不可重试。

## 2. 已选方案

采用“阶段式安全任务工作台”，不采用以下两种方案：

- 不做单一的“生成全部”黑盒按钮。该方案简单，但无法解释输入、配准、重建、世界和素材中
  哪一步失败，也无法在高成本步骤前让用户确认参数。
- 暂不拆出独立任务 daemon 或云队列。当前产品是本机单项目工具，引入额外服务会增加部署、
  鉴权和进程恢复复杂度，却不能提升首个本地闭环的真实性。

Studio 保留六阶段数量，并按真实依赖调整为 Sources → Align → Reconstruct → Assets → Compose →
Review。`pipeline.stitch` 状态键为保持 adapter v2 兼容暂不改名，界面显示为 Compose。每个阶段拥有
自己的可用操作、前置条件、参数摘要、最近运行和产物。顶部主按钮只是“下一个有效阶段操作”的
快捷入口，不拥有独立业务规则。

## 3. 范围

### 3.1 V1 支持的任务

| 阶段 | 命令 ID | 行为 | 正式输出 |
|---|---|---|---|
| Sources | `ingest` | 扫描项目 `input/`，复制图片并抽取视频帧 | `photos/` |
| Reconstruct | `reconstruct` | 执行配准与 mock/import 重建 | `recon/`、`web/data/recon/` |
| Assets | `validate-assets` | 验收指定仓内 handoff；可选择在验收成功后注册 | `assets/`、feedback 文件 |
| Compose | `world` | 在素材注册后生成布局、chunks 与 world manifest | `layouts/`、`web/data/chunks/`、`web/data/manifest.json` |

Align 是 reconstruct 的显式前置与证据视图，不在 V1 再造一套独立配准 CLI。Reconstruct 仍负责
联合配准、3DGS merge 与 LOD；Compose 负责把重建结果、布局和已验收素材组装成可分页世界。Review
只读取所有产物并执行验收，不启动改变几何的任务。

### 3.2 非目标

- 不在浏览器上传文件；V1 继续使用项目 `input/`，空状态明确展示绝对目录和“重新扫描”。
- 不内置 COLMAP、GPU trainer、GLM key 或云端算力；只编排当前仓库已经诚实声明的能力。
- 不允许任意 shell、任意模块名、任意输出目录或用户提供的环境变量。
- 不把 mock 任务成功提升为 measured、metric-aligned 或真实 3DGS。
- 不在本切片实现多项目、多用户、远程访问或分布式队列。

## 4. 用户体验

### 4.1 空项目

没有图片或视频时，顶部主按钮为“查看输入目录”，点击后选择 Sources 阶段并聚焦空状态卡。
卡片显示支持格式、项目 `input/` 路径和“重新扫描”。它不显示“上传成功”等浏览器没有完成的行为。
当检测到输入后，阶段操作变为“处理图片与视频”。

### 4.2 阶段操作

每个阶段检查器底部固定一个操作区，包含：

- 主操作及其前置条件；
- 即将执行的参数摘要；
- 预计影响的正式输出；
- synthetic/external 能力说明；
- 服务端不支持时的禁用原因。

点击主操作先打开确认面板。确认面板只展示命令注册表允许的字段，不提供自由文本命令。
提交成功后自动展开任务抽屉，并把焦点放到新 run。

### 4.3 运行中

queued/running 时顶部主按钮统一变为“查看任务进度”。同一项目只允许一个写任务；其他阶段操作
显示“已有任务运行中”，而不是提交后才收到模糊错误。任务抽屉显示阶段、进度、最近事件、开始时间、
参数和“取消任务”。取消按钮要求二次确认，并说明已发布的旧产物不会被删除。

### 4.4 失败与重试

失败主操作为“查看失败原因”，打开对应阶段和失败事件。详情显示稳定错误码、用户可读说明、退出码、
日志尾部和未发布的 staging 状态。重试先展示上一次参数，只允许修改命令 schema 标记为可重试的字段；
提交后创建新 run，并保存 `retry_of`，绝不改写失败记录。

### 4.5 完成与验收

任务进程退出 0 仅表示 execution 成功。服务端必须完成输出验证与发布，才把 run 标记为 succeeded。
终态事件到达后，Studio 只刷新一次 `/api/project`，再由现有 model 与 Viewer runtime capability
计算 geometry usability、render fidelity 和 trust。发布验证失败时 run 为 failed，旧正式产物继续有效。

## 5. 能力协商

新增 `GET /api/capabilities`。响应是前端展示写控件的唯一依据：

```json
{
  "schema_version": 1,
  "mode": "read-write",
  "request_token": "runtime-random-token",
  "single_writer": true,
  "commands": {
    "ingest": {"enabled": true, "cancel": true, "retry": true},
    "reconstruct": {"enabled": true, "cancel": true, "retry": true},
    "world": {"enabled": true, "cancel": true, "retry": true},
    "validate-assets": {"enabled": true, "cancel": true, "retry": true}
  }
}
```

默认 `python -m pipeline.studio_server` 仍是 read-only。只有同时满足以下条件才返回 `read-write`：

1. 使用显式 `--enable-jobs` 启动；
2. bind host 是 loopback；
3. `.nantai-studio/` 可创建且 SQLite ledger 可写；
4. 服务端命令注册表初始化成功。

任一条件不满足时，快照仍可读取，但所有写控件在点击前禁用并展示服务端给出的原因。方法存在、
adapter kind 或 mock 场景都不能被当作真实写 capability。

## 6. 服务端组件

### 6.1 `pipeline/studio_server.py`

保留静态文件、快照构建、安全响应头和 HTTP 路由。它只负责解析请求、校验同源写请求、调用任务服务、
把领域错误映射为结构化 HTTP 错误。任务状态机和 subprocess 逻辑从该文件移出，避免继续扩大当前
约 1,300 行的多重职责。

### 6.2 `pipeline/studio_jobs.py`

包含以下边界：

- `CommandRegistry`：命令 ID、参数 schema、前置条件、argv 构造器、staging 输出与验证器；
- `JobService`：单写者仲裁、启动、取消、重试和启动时恢复；
- `JobWorkspace`：为 run 创建固定的 staging/log 目录；
- `ArtifactPromoter`：在项目锁内执行发布日志、备份、替换和崩溃恢复；
- `ProcessController`：跨 POSIX/Windows 创建进程组、终止和超时升级为 kill。

所有 subprocess 都使用 argv 数组和 `shell=False`。工作目录固定为项目根，Python 命令固定使用
启动 Studio 的 `sys.executable`。

### 6.3 `pipeline/studio_ledger.py`

使用 Python 标准库 SQLite，数据库为 `.nantai-studio/studio.db`。开启 WAL、foreign keys 和
busy timeout。数据库 schema 带整数版本，V1 包含：

- `runs`：id、command、status、retry_of、参数 JSON、输入摘要、项目 revision、时间、退出码、
  error code、staging 路径和已发布 artifact IDs；
- `events`：全局自增 cursor、run_id、run 内 seq、phase、progress、level、message、时间；
- `meta`：schema version 和最后恢复时间。

状态写入与事件追加在同一事务。前端 localStorage ledger 只服务 mock adapter；真实模式始终以 SQLite
为唯一真相源。

## 7. 命令注册表与参数

请求只提交命令 ID 和结构化参数。未知字段、路径穿越、绝对路径、非有限数值和越界值都在创建 run 前
拒绝。V1 参数边界如下：

- `ingest`：`fps` 为 `(0, 30]`，`max_frames` 为 `[1, 10000]`，`blur_threshold` 为非负有限数，
  `max_long_edge` 为 `[256, 16384]`；输入固定为 `input/`。
- `reconstruct`：`engine` 仅 `mock/import`，`reg_engine` 仅 `auto/colmap/mock`，dedup 与 replace
  参数必须为非负有限数；import descriptor 和 base scene 必须是项目内普通文件。
- `world`：`size` 为 `[1, 50]`，seed 为有符号 32 位整数，`use_glm` 只有服务端检测到配置后才启用。
- `validate-assets`：deliverable 必须位于 `handoff/deliverables/` 的真实子目录；`register=true`
  只在完整验收通过后执行。

前端参数表单由自身的显示配置渲染，但服务端 schema 始终是权威。重试重新执行当前 schema 校验，
不能复用旧版本已经失效的字段。

## 8. HTTP 契约

新增或扩展以下接口：

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/capabilities` | 返回只读/可写能力、命令列表与 request token |
| POST | `/api/jobs` | 校验前置条件并创建 queued run |
| POST | `/api/jobs/{id}/cancel` | 请求取消 queued/running run |
| POST | `/api/jobs/{id}/retry` | 从终态 run 创建带 `retry_of` 的新 run |
| GET | `/api/runs?cursor=N` | 返回 run 摘要、新事件与下一 cursor |
| GET | `/api/runs/{id}` | 返回单个 run、参数和受限日志尾部 |

写请求必须带 `Content-Type: application/json`、`X-Nantai-Request-Token` 和与服务端 origin 完全一致的
`Origin`。服务端不启用 CORS，不处理跨站预检。body 上限固定，重复提交使用客户端 request ID 幂等去重。

稳定错误枚举为：`invalid_input`、`precondition_failed`、`conflict`、`unsupported`、
`not_found`、`forbidden`、`job_failed`、`publish_failed`、`internal_error`。响应不包含任意文件内容、
完整环境变量或未截断 traceback。

## 9. 状态机

合法主状态转换：

```text
queued -> running -> succeeded
queued -> canceled
running -> failed
running -> canceled
```

取消请求通过事件表达，不增加 `canceling` 主状态；只有进程确认退出后才能写 `canceled`。succeeded、
failed、canceled 都是不可逆终态。重复 cancel 返回当前 run，不创建矛盾状态。服务重启时：

- queued run 保持 queued，由恢复器按创建顺序重新调度；
- 数据库中仍为 running、但没有受管进程的 run 标记为 failed，错误码为 `interrupted`；
- 检测未完成发布日志，优先恢复旧正式产物，再将对应 run 标记 `publish_failed`。

## 10. staging 与正式发布

每次 run 写入 `.nantai-studio/work/<run-id>/`。命令注册表把现有 CLI 的输出参数指向该目录，
禁止直接写正式 `photos/`、`recon/`、`layouts/` 或 `web/data/`。

进程成功后按顺序执行：

1. 命令专属结构与语义验证；
2. 生成包含相对路径、大小和 SHA-256 的发布清单；
3. 获取项目发布锁并再次检查 `expected_project_revision`；
4. 写入并 fsync 发布日志；
5. 将旧目标移动到同卷 backup，将 staging 目标移动到正式位置；
6. fsync 父目录，更新发布日志为 committed；
7. 删除 backup，记录 artifact IDs 和 succeeded 事件。

多目标发布必须使用同一日志。任何失败都按日志逆序恢复 backup。Studio 启动时先恢复未完成日志，
完成前不广告写 capability。这样取消、崩溃或验证失败都不会把半成品提升为当前 artifact。

`validate-assets(register=true)` 复用 AssetRegistry 的内容寻址事务；handoff 验收产物先在 run staging
生成，验证通过后才更新 registry。当前并行进行的 Windows 文件锁改动必须单独通过审核后作为该命令的
前置基线，本规格不吞并或重写该工作。

## 11. 进程与取消

POSIX 使用新 session，Windows 使用新 process group。取消先发送温和终止并等待固定宽限期，超时后
终止整个受管进程组。日志读取线程持续排空 stdout/stderr，避免 pipe 阻塞；每行转为结构化事件前做
UTF-8 replacement decode、长度上限和敏感值遮盖。完整原始日志按大小轮转，不通过 API 无界返回。

取消后保留 run、事件和受限日志，删除未发布的大型 staging payload；保留发布清单和诊断摘要。
用户可从 canceled run 重试，但重试仍需重新满足当前前置条件。

## 12. 前端组件与状态

新增以下模块，避免继续扩大 `web/studio/app.js`：

- `job-controller.mjs`：加载 capability、提交/取消/重试、轮询 cursor、终态后刷新 snapshot；
- `job-actions.mjs`：从阶段状态、capability 和 active run 纯函数派生操作；
- `job-drawer.mjs`：run 列表、事件、日志尾部和取消/重试控件；
- `job-forms.mjs`：白名单参数表单与确认摘要。

`app.js` 只负责组合项目快照、Viewer capability 和 job controller。local adapter 增加 capability、
cancel、retry 和 run detail 方法。mock adapter 实现同一接口，但持续显示 simulated 标识。

顶部操作派生优先级固定为：连接失败 > active queued/running > active failed > 空输入 > 下一缺失阶段 >
Review。任何时刻恰好一个全局主操作；阶段内可以有次操作，但不允许两个视觉同级的主按钮。

## 13. 轮询与性能

V1 使用轻量 cursor polling，不轮询完整 `/api/project`：

- 有 active run 时每 1 秒请求 `/api/runs?cursor=N`；
- 后台无 active run 时每 5 秒请求；页面隐藏后降为 15 秒；
- 网络错误采用上限 15 秒的退避；
- 收到终态事件后只刷新一次 `/api/project` 和 Viewer artifact。

run API 不解析 PLY，不构建 144MB 级快照。项目快照继续按用户操作或终态刷新，从架构上避免现有
PLY 语义验证在高频轮询下放大内存。

## 14. 安全边界

- 默认只读，写模式必须显式开启且仅绑定 loopback；
- 命令、参数、输入和输出都由注册表定义；
- subprocess 永远 `shell=False`，不拼接命令字符串；
- 路径在打开前和发布前两次执行 realpath containment 与普通文件/目录检查；
- 写 API 要求同源 Origin、自定义 header、随机 request token、JSON body 和大小上限；
- 单项目单写者，启动和发布均检查 project revision；
- 日志遮盖已知 secret 值，不记录完整环境；
- 服务端只继承命令所需环境，GLM 等外部能力未配置时 capability 直接禁用；
- 静态白名单、CSP、路径穿越与 symlink fail-closed 规则保持现状。

## 15. 测试与验收

### 15.1 纯函数与前端

- 空输入派生并点击 `inspect-sources`，焦点进入 Sources 空状态；
- read-only capability 下不出现可点击写操作；
- queued/running 派生“查看任务进度”，并阻止第二写任务；
- cancel/retry 仅在 capability 与状态同时允许时出现；
- retry 保留父 run，失败记录不被覆盖；
- cursor 去重、退避、页面隐藏频率和终态单次 snapshot 刷新；
- mock 与 local 使用同一交互接口，mock 标识不可移除。

### 15.2 ledger 与状态机

- SQLite 初始化、迁移拒绝未知新版本、并发 cursor 单调；
- 每个合法状态转换通过，每个非法或终态回退被拒绝；
- 启动恢复 queued、孤儿 running 和未完成发布日志；
- request ID 幂等，重复 POST 不产生第二个 run。

### 15.3 命令与安全

- 每个允许参数的边界值与非法值；
- 未知命令/字段、绝对路径、`..`、symlink escape、NaN/Inf、超大 body 全部拒绝；
- argv 精确匹配，`shell=False`，项目根与 staging 路径固定；
- 跨站 Origin、缺 token、错误 token、非 loopback write mode 全部拒绝；
- 日志中的测试 secret 被遮盖。

### 15.4 执行、取消与发布

- 使用小型受控 helper 覆盖成功、非零退出、挂起、忽略终止和子进程树；
- Windows 与 POSIX 都验证进程组取消，平台不支持的 symlink 测试必须基于能力明确 skip，而不是误报；
- staging 验证失败不改变正式产物；
- 在发布每一步注入失败，启动恢复后旧产物完整且 run 为 `publish_failed`；
- revision 冲突在发布前拒绝，不覆盖并行产生的新产物。

### 15.5 端到端浏览器验收

1. read-only 启动：现有 Viewer/Studio 可用，写操作禁用且原因可见；
2. `--enable-jobs` 启动：空项目引导到真实 `input/`；
3. 执行 mock ingest/reconstruct/world，抽屉显示事件，刷新页面后 run 仍存在；
4. 取消慢任务，正式 artifact 不变；
5. 注入失败，检查错误定位与带 `retry_of` 的成功重试；
6. 终态后 Viewer 加载新 artifact，synthetic/proxy 水印与 trust gate 仍正确；
7. 键盘完成阶段选择、确认、取消、错误定位和抽屉操作，live region 不重复刷屏。

## 16. 交付切片

本规格对应一个实现计划，但按可独立验证的顺序交付：

1. capability 与前端操作派生，先关闭现有四个交互误导；
2. SQLite ledger、状态机和只读 run API；
3. 命令注册表、单写者和安全 POST；
4. staging、验证、发布日志与恢复；
5. cancel/retry 和跨平台进程控制；
6. job drawer、参数确认、cursor polling 与浏览器验收；
7. 文档、默认只读兼容性和完整门禁。

每个切片都必须先有失败测试，再实现最小行为，并使用包含指定
`Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>` 的独立提交。

## 17. 成功标准

V1 完成需要同时满足：

- 默认服务保持只读且现有读取功能无回归；
- 写模式下四个允许命令可从 Studio 阶段操作启动；
- 不存在任意 shell/路径/环境注入；
- 单写者、取消、重试、崩溃恢复和事务发布有自动测试；
- 刷新页面不会丢 run，失败记录不会被重试覆盖；
- 半成品永不成为正式 artifact，旧产物在失败后继续可用；
- synthetic/proxy 与 measured/full 的真实性边界不因任务成功而改变；
- Python、Viewer、Studio、HTTP、安全和浏览器验收均提供当前提交上的新鲜证据。
