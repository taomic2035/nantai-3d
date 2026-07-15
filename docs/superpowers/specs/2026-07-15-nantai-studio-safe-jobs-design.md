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

Studio 保留六个信息视图，但不再把它们伪装成线性流水线。真实依赖是 DAG：Sources → Reconstruct，
Align 是 Reconstruct job 内的 phase 与证据视图；Assets → Compose，当前 `world` 只组合布局和已验收
素材，不读取 recon；Review 才汇合 Reconstruct 与 Compose 的证据。`pipeline.stitch` 状态键为保持
adapter v2 兼容暂不改名，界面显示为 Compose。

每个视图拥有自己的可用操作、前置条件、参数摘要、最近运行和产物。顶部主按钮只是“当前最高优先级
可执行 DAG 节点”的快捷入口，不拥有独立业务规则。

## 3. 范围

### 3.1 V1 支持的任务

| 阶段 | 命令 ID | 行为 | 正式输出 |
|---|---|---|---|
| Sources | `ingest` | 扫描项目 `input/`，复制图片并抽取视频帧 | `photos/` |
| Reconstruct | `reconstruct` | 执行配准与 mock/import 重建 | `recon/`、`web/data/recon/` |
| Assets | `validate-assets` | 验收指定仓内 handoff；可选择在验收成功后注册 | `assets/`、feedback 文件 |
| Compose | `world` | 在素材注册后生成布局、chunks 与 world manifest | `layouts/`、`web/data/chunks/`、`web/data/manifest.json` |

Align 是 reconstruct 的内部 phase 与独立证据视图，不是已经存在的外部前置产物，也不在 V1 再造
一套 registration CLI。Reconstruct 负责联合配准、3DGS merge 与 LOD；Compose 负责把布局和已验收
素材组装成可分页 world chunks。Viewer/Review 同时读取 reconstruction 与 world 两条 artifact 支线；
Review 只执行验收，不启动改变几何的任务。

### 3.2 非目标

- 不在浏览器上传文件；V1 继续使用项目 `input/`，空状态明确展示绝对目录和“重新扫描”。
- 不内置 COLMAP、GPU trainer、GLM key 或云端算力；只编排当前仓库已经诚实声明的能力。
- 不允许任意 shell、任意模块名、任意输出目录或用户提供的环境变量。
- 不把 mock 任务成功提升为 measured、metric-aligned 或真实 3DGS。
- 不在本切片实现多项目、多用户、远程访问或分布式队列。

### 3.3 GPT/image2 素材边界

视觉素材生成、视觉设计与图像处理由 GPT 的图像生成能力负责。在没有真实素材时，每个被布局消费的
语义角色都必须有明确标注为 `gpt-mock / synthetic` 的模拟输入。图像模型用于生成参考图、材质/纹理、
可分离物体图和模拟拍摄输入；位图不是 3D 几何证据，必须经过可复现转换与 handoff 验收才能成为
PLY/3DGS proxy。

模拟素材不能绑定某个具体村名、坐标或唯一建筑。后续独立素材规格采用稳定 `asset_family`/slot、通用
kind、variant tags、名义尺寸、local-z-up/meter footprint、地面/连接锚点和替换约束。真实资产只要满足
同一 slot 的空间与语义契约，就能替换 synthetic 版本而无需修改布局。现有 HANDOFF-001 的 11 个南台
风格资产仅是首批 fixture，不是资产分类上限。

每个图像源记录 prompt/hash、输入引用 hash、生成工具、实际可获知的模型标识、编辑链、source SHA、
usage/license 与 `synthetic=true`；工具未暴露模型名时记录 `unknown`，不得凭产品称呼伪造 provenance。
安全任务工作台的 `validate-assets` 只消费标准 handoff，不在服务端隐式调用图像模型。通用模拟素材库、
image2 生成批次与图像到 proxy 的转换链使用独立设计和实施计划。

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

queued/running 时顶部主按钮统一变为“查看任务进度”。同一项目只允许一个写任务；其他 DAG 节点
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
  "request_token": "startup-scoped-256-bit-token",
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

服务端从实际 bind socket 的地址验证 loopback，不信任 CLI hostname 字符串。它只广告启动时生成的
canonical origin，例如 `http://127.0.0.1:8000`；写请求的 Host 和 Origin 必须逐字匹配该 origin。
capability 响应使用 `Cache-Control: no-store`，request token 由 `secrets.token_urlsafe(32)` 生成并在
每次服务启动时轮换。

## 6. 服务端组件

### 6.1 `pipeline/studio_server.py`

保留静态文件、快照构建、安全响应头和 HTTP 路由。它只负责解析请求、校验同源写请求、调用任务服务、
把领域错误映射为结构化 HTTP 错误。任务状态机和 subprocess 逻辑从该文件移出，避免继续扩大当前
约 1,300 行的多重职责。

### 6.2 `pipeline/studio_jobs.py`

包含以下边界：

- `CommandRegistry`：命令 ID、参数 schema、前置条件、argv 构造器、staging 输出与验证器；
- `JobService`：跨服务单写者租约、启动、取消、重试和启动时恢复；
- `JobWorkspace`：为 run 创建固定的 staging/log 目录；
- `ArtifactPromoter`：在项目锁内执行发布日志、备份、替换和崩溃恢复；
- `ProcessController`：POSIX process group 与 Windows Job Object、终止和超时升级为 kill。

所有 subprocess 都使用 argv 数组和 `shell=False`。工作目录固定为项目根，Python 命令固定使用
启动 Studio 的 `sys.executable`。

### 6.3 `pipeline/studio_ledger.py`

使用 Python 标准库 SQLite，数据库为 `.nantai-studio/studio.db`。开启 WAL、foreign keys 和
busy timeout。数据库 schema 带整数版本，V1 包含：

- `runs`：id、command、command schema version、status、phase、retry_of、参数 JSON、输入摘要与
  digest、项目 revision、writer lease owner/expiry、时间、退出码、error code、staging 路径和已发布
  artifact IDs；
- `events`：全局自增 cursor、run_id、run 内 seq、phase、progress、level、message、时间；
- `meta`：schema version 和最后恢复时间。

状态写入与事件追加在同一事务。创建 active run 前先非阻塞获取 `.nantai-studio/writer.lock`，并在验证、
发布和 ledger 终态提交完成前一直持有；OS 在进程退出时释放该锁。随后使用 `BEGIN IMMEDIATE` 创建或
claim run，并由 partial unique index 保证同一项目至多一个 queued/running run。worker 写入 owner 与
有期限 lease，并持续 heartbeat；lease 过期但 writer lock 仍被持有时不得接管。其他 Studio 进程只能
观察，不能启动第二个 writer。发布/恢复另取 `.nantai-studio/publish.lock`，不能把 WAL 或 busy timeout
当成作业互斥。前端 localStorage ledger 只服务 mock adapter；真实模式始终以 SQLite 为唯一真相源。

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

每个命令在提交时持久化 command-specific `ConcurrencySnapshot`，不用 Git HEAD 或整棵工作树 hash：

- `input_digest` 是该命令实际读取文件的规范化清单 hash，清单含项目相对路径、类型、大小和 SHA-256；
- 每个正式 target 记录 `absent`、单文件 SHA-256 或目录 tree-manifest hash；
- `.nantai-studio/`、SQLite、heartbeat、日志、staging 和命令不读取的无关路径明确排除；
- 获取 publish lock 后逐项重算输入与 target；输入或 target 改变则以 `concurrent_change` 拒绝发布，
  只改无关文件或 heartbeat 不产生冲突。

不同命令的 snapshot 边界由 CommandRegistry 声明并测试。例如 ingest 读取 `input/`、发布 `photos/`；
reconstruct 读取 `photos/` 与选定 descriptor/base scene、发布 `recon/` 和 `web/data/recon/`。

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

写请求必须带 `Content-Type: application/json`、`X-Nantai-Request-Token` 和与 canonical origin 完全
一致的 `Origin`。Host 只接受实际监听 family 对应的 `127.0.0.1:<port>` 或 `[::1]:<port>`，不接受
`localhost`、任意 hostname、wildcard 或 Host 推导出的 origin。服务端不启用 CORS，不处理跨站预检。
body 上限固定，重复提交使用客户端 request ID 幂等去重。

稳定错误枚举为：`invalid_input`、`precondition_failed`、`conflict`、`unsupported`、
`not_found`、`forbidden`、`stale_job`、`concurrent_change`、`interrupted`、`job_failed`、
`publish_failed`、`internal_error`。响应不包含任意文件内容、完整环境变量或未截断 traceback。

## 9. 状态机

合法主状态转换：

```text
queued -> running -> succeeded
queued -> canceled
running -> failed
running -> canceled
```

run 另有 `phase=executing|validating|publishing`。取消请求通过事件表达，不增加 `canceling` 主状态；
queued 或 executing 可以取消，进入 validating/publishing 后取消按钮禁用，避免 staging 清理与验证/发布
竞态。只有进程确认退出后才能写 `canceled`。succeeded、failed、canceled 都是不可逆终态；重复 cancel
返回当前 run，不创建矛盾状态。active writer 直到验证、发布和 ledger 提交完成才释放。服务重启时：

- queued run 只有在 command schema version、capability 和完整 `ConcurrencySnapshot` 重新
  校验通过后才按创建顺序调度；不匹配时终止为 `stale_job`，绝不执行旧 argv；
- lease 未到期且 owner 存活的 active run 只观察；lease 过期且没有受管进程的 executing run 终止为
  `interrupted`；
- validating/publishing run 先按持久发布状态恢复，再决定 roll back 或 roll forward，不能一律标记失败。

## 10. staging 与正式发布

每次 run 写入 `.nantai-studio/work/<run-id>/`。命令注册表把现有 CLI 的输出参数指向该目录，
禁止直接写正式 `photos/`、`recon/`、`layouts/` 或 `web/data/`。

`reconstruct` 在进入命令注册表前必须先关闭一个现有旁路：当前 registration 默认 workspace 是正式
`recon/colmap_ws`。核心 CLI 需要把 COLMAP workspace 固定传为 `<out_dir>/colmap_ws`；fake-COLMAP
隔离测试必须证明 `--out` 和 `--web` 指向 staging 时，正式 `recon/` 与 `web/data/recon/` 零写入。
该测试未通过前，capability 不广告 reconstruct。

进程成功后按顺序执行：

1. 命令专属结构与语义验证；
2. 生成包含相对路径、大小和 SHA-256 的发布清单；
3. 获取项目发布锁并再次检查 command-specific `ConcurrencySnapshot`；
4. 在 SQLite 创建 publication 与逐目标 journal；每个目标记录 target、stage、backup、`had_old`、
   当前 intent/done 状态，提交后用 synchronous FULL 持久化；
5. 每次 rename 前先持久化 intent，rename 后 fsync 相关父目录，再持久化 done；
6. 所有目标切换并复验后，在同一个 SQLite 事务中把 publication 标为 committed、run 标为 succeeded、
   写入 artifact IDs 与终态事件；这是唯一 point-of-no-return；
7. committed 后删除 backup 只是可重试 GC，不再改变 run 结果。

恢复规则由 point-of-no-return 决定。publication 未 committed 时，根据逐目标 intent/done、`had_old` 和
文件存在性逆序回滚；原目标不存在时删除已移动的新目标，原目标存在时恢复 backup。publication 已
committed 时只 roll forward：复验正式目标、补做 backup GC，绝不能把 run 改成 interrupted/failed。
SQLite 的 committed 与 run succeeded 在同一事务，消除“正式产物已换新但 ledger 失败”的 split-brain。
每一个 journal 指令前后都必须有崩溃注入测试。Studio 启动完成所有恢复前不广告写 capability。

`validate-assets(register=true)` 不发布一个从空目录生成的完整 `assets/`。它使用 copy-on-write registry
事务：在 AssetRegistry 锁内读取当前 registry 与 revision；把新 payload 写入 staging CAS；在当前完整
registry 副本上应用全部候选版本并验证；再次核对 revision 后，可在 pre-commit 向正式 CAS 追加新
payload，因为未引用 payload 是安全孤儿。候选完整 `registry.json` 与 feedback 分别作为同一个 SQLite
publication journal 的两个正式 target，都具有 backup、`had_old`、intent/done，并共享 publication
committed + run succeeded 的唯一 point-of-no-return。不得在 journal 外先替换 registry。

AssetRegistry 锁必须保持到 point-of-no-return 事务完成。固定锁顺序为 writer lock → publish lock →
AssetRegistry lock → 短 SQLite transaction，任何路径不得反向获取。旧资产、历史版本和未涉及条目始终
保留；第 N 个候选失败或 revision 冲突时正式 registry 不变。已复制但未引用的 payload 可由后续 GC
清理，不能删除既有 payload。为避免现有逐项 register 的嵌套锁与中间提交，D 里程碑必须增加显式 batch
copy-on-write API。当前并行进行的 Windows 文件锁改动必须单独通过审核后作为该命令的前置基线，
本规格不吞并或重写该工作。

## 11. 进程与取消

POSIX 使用 `start_new_session` 与 `killpg`。Windows write/cancel 的条件依赖固定为
`pywin32>=311`（当前环境已安装 311；实现时放入 Windows 专用 extra）。Job Object 以 suspended 状态
创建进程，设置 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`，在首个用户代码运行前 assign 到 Job 后再
resume。可用时先通过受控 IPC/CTRL_BREAK 请求温和退出；宽限期结束后关闭 Job，终止整个子孙树。
`CREATE_NEW_PROCESS_GROUP` 或只杀父进程不作为完整取消证据。

B 里程碑尚未交付 Job Object 时，在 Windows 只广告 ingest `cancel:false`；其 subprocess 只写隔离
staging，服务正常退出会等待任务结束。C 里程碑只有在 pywin32 导入、Job Object 启动自检和真实孙进程
测试都通过后才广告 `cancel:true` 并启用 reconstruct/world。最终 Windows 发布门禁强制安装并验证该
extra；依赖或自检失败时 fail closed 到 read-only，而不是用不完整的父进程终止替代。

日志读取线程持续排空 stdout/stderr，避免 pipe 阻塞；每行转为结构化事件前做 UTF-8 replacement
decode、长度上限和敏感值遮盖。完整原始日志按大小轮转，不通过 API 无界返回。

取消后保留 run、事件和受限日志，删除未发布的大型 staging payload；保留发布清单和诊断摘要。
用户可从 canceled run 重试，但重试仍需重新满足当前前置条件。

## 11.1 跨平台持久化后端

`ArtifactPromoter` 依赖显式 `DurabilityBackend`，不把 POSIX 目录 fsync 假设搬到 Windows：

- POSIX：文件内容 `fsync`，rename 后打开并 `fsync` 每个相关父目录；
- Windows V1：只在本地 NTFS 上开启写能力，使用 pywin32/Win32 的 `FlushFileBuffers` 与
  `MoveFileExW(..., MOVEFILE_WRITE_THROUGH)`/`ReplaceFileW` 完成 write-through 文件或目录交换；
- 启动时在 `.nantai-studio/` 所在卷执行文件系统、flush、replace 和 write-through rename 自检；不是
  NTFS 或任一操作失败时只提供 read-only capability；
- POSIX 与真实 Windows/NTFS 都执行杀进程式 crash/restart 集成测试，mock fsync 只能用于故障注入，
  不能单独证明崩溃持久性。

## 12. 前端组件与状态

新增以下模块，避免继续扩大 `web/studio/app.js`：

- `job-controller.mjs`：加载 capability、提交/取消/重试、轮询 cursor、终态后刷新 snapshot；
- `job-actions.mjs`：从阶段状态、capability 和 active run 纯函数派生操作；
- `job-drawer.mjs`：run 列表、事件、日志尾部和取消/重试控件；
- `job-forms.mjs`：白名单参数表单与确认摘要。

`app.js` 只负责组合项目快照、Viewer capability 和 job controller。local adapter 增加 capability、
cancel、retry 和 run detail 方法。mock adapter 实现同一接口，但持续显示 simulated 标识。

顶部操作派生优先级固定为：连接失败 > active queued/running > active failed > 空输入 > 最高优先级的
ready DAG 节点 > Review。DAG ready 的默认优先级为 ingest、reconstruct、validate-assets、world；用户
仍可从相应视图主动选择另一个已经 ready 的独立节点。任何时刻恰好一个全局主操作；阶段内可以有
次操作，但不允许两个视觉同级的主按钮。

## 13. 轮询与性能

V1 使用轻量 cursor polling，不轮询完整 `/api/project`：

- 有 active run 时每 1 秒请求 `/api/runs?cursor=N`；
- 后台无 active run 时每 5 秒请求；页面隐藏后降为 15 秒；
- 网络错误采用上限 15 秒的退避；
- 收到终态事件后只刷新一次 `/api/project` 和 Viewer artifact。

run API 不解析 PLY，不构建 144MB 级快照。项目快照继续按用户操作或终态刷新，从架构上避免现有
PLY 语义验证在高频轮询下放大内存。

## 14. 安全边界

- 默认只读；写模式必须显式开启，并从实际 socket 地址验证 loopback；
- 命令、参数、输入和输出都由注册表定义；
- subprocess 永远 `shell=False`，不拼接命令字符串；
- 路径在打开前和发布前两次执行 realpath containment 与普通文件/目录检查；
- 写 API 要求 canonical Host/Origin、自定义 header、每次启动轮换的至少 256-bit request token、
  `no-store`、JSON body 和大小上限，拒绝 DNS-rebinding Host；
- SQLite active-run 唯一约束、lease 和跨进程发布锁共同保证单项目单写者；启动和发布均检查命令级
  concurrency snapshot；
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
- 两个独立 Studio 进程竞争时只有一个能创建/claim active writer，活 lease 不被抢占，过期 lease 可恢复；
- 启动恢复 queued、孤儿 running 和未完成发布 journal；queued 的 schema version、capability 或
  command-specific concurrency snapshot 变化时以 `stale_job` 终止；
- request ID 幂等，重复 POST 不产生第二个 run。

### 15.3 命令与安全

- 每个允许参数的边界值与非法值；
- 未知命令/字段、绝对路径、`..`、symlink escape、NaN/Inf、超大 body 全部拒绝；
- argv 精确匹配，`shell=False`，项目根与 staging 路径固定；
- 跨站 Origin、伪造/歧义 Host、DNS rebinding Host、缺 token、错误 token、非 loopback write mode
  全部拒绝；
- Windows B capability 为 `cancel:false`；缺 pywin32、Job Object 自检失败或非 NTFS 时 write/cancel
  capability 按里程碑规则 fail closed；
- 日志中的测试 secret 被遮盖。

### 15.4 执行、取消与发布

- 使用小型受控 helper 覆盖成功、非零退出、挂起、忽略终止和子进程树；
- Windows Job Object 与 POSIX process group 都用真实孙进程树验证取消；平台不支持的 symlink 测试必须
  基于能力明确 skip，而不是误报；
- executing 可取消，validating/publishing 明确拒绝取消且不删除 staging；
- staging 验证失败不改变正式产物；
- fake-COLMAP 证明 staging reconstruct 对正式 `recon/` 与 `web/data/recon/` 零写入；
- 在每个 publish intent、rename、fsync、done 与 point-of-no-return 前后注入崩溃；pre-commit 恢复旧
  产物并终止为 `publish_failed`，post-commit roll forward 且 run 保持 succeeded；
- POSIX 目录 fsync 与 Windows/NTFS write-through backend 分别通过真实 crash/restart 测试；
- asset copy-on-write 覆盖原 registry 保留、并发 revision 冲突和第 N 项失败不丢数据；
- 外部 CLI 改正式 target 或命令输入时发布失败；只改无关文件或 heartbeat 时不冲突。

### 15.5 端到端浏览器验收

1. read-only 启动：现有 Viewer/Studio 可用，写操作禁用且原因可见；
2. `--enable-jobs` 启动：空项目引导到真实 `input/`；
3. 执行 mock ingest/reconstruct/world，抽屉显示事件，刷新页面后 run 仍存在；
4. 取消慢任务，正式 artifact 不变；
5. 注入失败，检查错误定位与带 `retry_of` 的成功重试；
6. 终态后 Viewer 加载新 artifact，synthetic/proxy 水印与 trust gate 仍正确；
7. 键盘完成阶段选择、确认、取消、错误定位和抽屉操作，live region 不重复刷屏。

## 16. 实施里程碑

本规格是目标架构，不塞进一个实施计划。按故障域拆成四个独立计划，每个计划都交付可运行、可回滚、
可独立验收的软件：

### A. Read-only capability 与 UX 纠偏

只增加 capability 契约和纯前端操作派生，关闭当前四个交互误导。服务仍拒绝所有写方法。该里程碑
验证 DAG、空输入、disabled reason、active/failed run 导航和 mock/local 一致性，不引入 subprocess。

### B. 单命令 ingest job kernel

交付 SQLite ledger、跨进程 single-writer/lease、安全 POST、ingest command schema、staging、逐目标
发布 journal、崩溃恢复和 cursor polling。只支持 ingest，以最小命令验证完整事务内核；没有可靠恢复
证据前不增加第二个命令。

### C. cancel/retry 与 reconstruct/world

交付 POSIX process group、Windows Job Object、phase-aware cancel/retry；先修复 COLMAP workspace 旁路，
再启用 reconstruct，随后启用 world。完成参数确认、job drawer 和终态 Viewer 刷新。

### D. AssetRegistry copy-on-write 接入

在并行 Windows 锁基线独立审核通过后，最后接入 validate-assets。使用完整 registry copy-on-write、CAS
追加、revision conflict 和独立 feedback 目标；不得复用普通目录整体交换代替 registry 事务。

### 并行素材线：通用 synthetic asset source

该工作不属于任务执行内核，另写设计规格并可在 A–C 期间并行推进：先定义通用 asset family/slot 与
替换契约，再由 GPT 图像生成能力产出项目内 mock 视觉源，记录完整 synthetic provenance，最后通过
可复现转换与 HANDOFF 验收生成 PLY/3DGS proxy。D 完成后把这些标准 handoff 接入 Studio 注册流程；
真实素材到达时按同一 slot 替换，不改变布局引用。

每个计划都必须有自己的 threat model、新鲜门禁和浏览器或 CLI 验收；每一步先有失败测试，再实现最小
行为，并使用包含指定
`Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>` 的独立提交。

## 17. 成功标准

V1 完成需要同时满足：

- 默认服务保持只读且现有读取功能无回归；
- 四个里程碑全部完成后，写模式下四个允许命令可从 Studio 的 ready DAG 节点启动；
- 不存在任意 shell/路径/环境注入；
- 跨进程单写者、lease、phase-aware 取消、重试、崩溃恢复和事务发布有自动测试；
- 刷新页面不会丢 run，失败记录不会被重试覆盖；
- 半成品永不成为正式 artifact，旧产物在失败后继续可用；
- synthetic/proxy 与 measured/full 的真实性边界不因任务成功而改变；
- Python、Viewer、Studio、HTTP、安全和浏览器验收均提供当前提交上的新鲜证据。
