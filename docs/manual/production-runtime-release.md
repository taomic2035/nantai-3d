# Production Runtime 发布手册

本页只说明真实场景 Production runtime 的封装、独立复验与运行。当前公开版本仍是
`v1.0.0-preview.2` synthetic Preview；它和 Production V1 是两种互斥的发布合同，
不能把 Preview receipt、模型效果或绿色测试解释为真实场景验收。

## 发布前提

只有同一个 scene identity 的真实采集、accepted real-photo SfM、非 mock GPU 3DGS、
实测米制对齐、真实浏览器 Viewer QA、人工画质复核和发布权利全部通过，并且最终
aggregate 明确给出 `production_release_allowed=true`，才能开始正式封装。

`VERSION` 没有默认值。门禁未满足时不得填写或发布 `v1.0.0`，也不得预先创建正式
tag；继续使用 Preview 版本号并保留 blocked/unknown 状态。

## 仓库维护命令

以下五个命令只属于源码仓库根目录的开发版 `make.py`：

```text
build-production
verify-production
audit-production-privacy
stage-production-assets
verify-production-assets
```

它们负责重验私有 acceptance、构建候选包、隐私审计和最终四件套整理。正式 runtime
不会携带这些维护入口，也不会携带它们所需的私有策略、控制点或工作区。

## 构建

私有验收根通常位于忽略的 `.nantai-studio/` 工作区。下面三个变量都必须显式提供：

```powershell
$env:ACCEPTANCE_ROOT = (Resolve-Path .nantai-studio\real-scene\accepted).Path
$env:VERSION = "v1.0.0"
$env:ARCHIVE = (Join-Path $PWD "dist\nantai-3d-v1.0.0.zip")
python make.py build-production
```

`build-production` 会重新打开并验证私有 acceptance，投影脱敏的公开证据，计算 runtime
scene closure，再以 no-replace 方式生成确定性 ZIP。已有目标不会被覆盖。

## 独立验证下载字节

发布前先验证构建产物；从 Release 下载后还必须对下载到本机的实际字节再次运行同一
`verify-production`，不能用网页显示的哈希或构建机上的旧文件代替：

```powershell
$env:ARCHIVE = (Resolve-Path .\nantai-3d-v1.0.0.zip).Path
python make.py verify-production
```

验证器只依赖 Python 标准库，检查 ZIP 路径安全、确定性元数据、每个 artifact 的
SHA-256/字节数、`SHA256SUMS.txt`、公共证据、scene manifest 与
`PRODUCTION-RELEASE.json` 的内容绑定。任一不一致都拒绝运行。

## 隐私机器审计

独立验证通过后，必须使用保存在私有工作区、由操作者维护的 canonical needle policy
扫描最终 ZIP。policy 的 needle 是至少 8 bytes 的 canonical base64；policy 和报告都
必须位于公开包之外：

```powershell
$env:ARCHIVE = (Resolve-Path .\nantai-3d-v1.0.0.zip).Path
$env:PRIVACY_POLICY = (Resolve-Path .nantai-studio\private\privacy-policy.json).Path
$env:PRIVACY_REPORT = (Join-Path $PWD ".nantai-studio\verification\privacy-v1.0.0.json")
python make.py audit-production-privacy
```

审计器会先复验 Production 包、以不超过 1 MiB 的分块扫描所有公开文件，再复验一次
包身份；跨块 private needle、PEM/private-key marker、明确 credential assignment、
Windows/POSIX 私有绝对路径、symlink、非 regular file、额外文件或读取漂移都会阻止
发布。报告只含类别、公开相对路径、数量和 package content ID，不回显秘密；报告本身
也不提升 scene trust。`valid=true` 且报告中的 package content ID 与独立 verifier
一致，才可继续。

## 整理最终公开资产

三平台 clean-room 浏览器 QA 和人工复核通过后，从已验证候选 ZIP 生成一个全新目录。
`stage-production-assets` 会再次独立验证候选包、重新执行隐私审计、拒绝 modeled fixture，
并且只发布四个最终公开资产：

```powershell
$env:ARCHIVE = (Resolve-Path .nantai-studio\releases\v1.0.0\build-a.zip).Path
$env:PRIVACY_POLICY = (Resolve-Path .nantai-studio\private\privacy-policy.json).Path
$env:RELEASE_DIR = (Join-Path $PWD ".nantai-studio\releases\v1.0.0\public-assets")
python make.py stage-production-assets
```

`RELEASE_DIR` 必须不存在，命令不会覆盖旧目录。成功目录精确包含：

```text
nantai-3d-v1.0.0-runtime.zip
nantai-3d-v1.0.0-runtime.zip.sha256
PRODUCTION-RELEASE.json
SHA256SUMS.txt
```

独立 receipt 与 checksum 来自同一份已复验 ZIP；archive sidecar 按最终公开文件名重新
计算。privacy policy、privacy report、QA 截图原件和私有 acceptance 仍留在私有
工作区，不进入公开目录。这个步骤只收拢已验收字节，不替代双构建一致性、三平台
浏览器 QA、人工复核或 GitHub 下载后复验。

发布后把四个 GitHub 资产下载到同一个全新目录，再整体复验：

```powershell
$env:RELEASE_DIR = (Resolve-Path .\downloaded-v1.0.0-assets).Path
python make.py verify-production-assets
```

该命令要求目录精确包含四个 regular non-link 文件，验证 archive sidecar，独立解压
并复验 ZIP，再逐字节比较外置与包内的 `PRODUCTION-RELEASE.json`、
`SHA256SUMS.txt`。版本、package content ID、公开文件名或任意一项不一致都会拒绝，
modeled fixture 也不能通过。

## 解压与运行

下载后先解压到空目录。包根 `make.py` 是独立的最小 runner，不是源码仓库的开发
runner。它的解压包命令只有：

```text
python make.py help
python make.py verify
python make.py serve
```

先在未安装依赖、未添加文件的包根复验；通过后再创建环境、安装并启动：

```powershell
python make.py verify
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\Activate.ps1
python make.py serve
```

该 runner 拒绝构建、隐私审计、资产整理、多 target 组合和私有 import 输入，只使用
包内内容绑定的 scene。任何未知 target 都以退出码 2 fail closed。

打开 <http://127.0.0.1:8000/web/studio/>。Studio 必须显示
`Production 包 · 已校验`；Viewer 必须加载包内完整 3DGS。scene 缺失、Spark
不可用或 receipt 不一致时必须停止并显示错误，不能回退到 repository synthetic
world、model preview 或 DC point demo。

## 私有闭包与公开闭包

私有 acceptance closure 保存重建所需的完整机器证据，仅供获授权的操作者复验。
公开 runtime closure 只包含最终运行白名单、脱敏证据和离线验证材料，核心入口是：

- `PRODUCTION-RELEASE.json`
- `evidence/public-evidence.json`
- `SHA256SUMS.txt`
- `web/data/recon/recon_manifest.json`
- Studio、Viewer、runtime code 与离线 verifier

公开包明确排除原始照片、原始视频、训练输入像素、控制点坐标、操作者身份、远程主机
配置、凭据、`.nantai-studio/` 私有工作区、缓存、日志、测试 fixture 和中间产物。
需要审计私有证据时应在受控环境中验证，不能把它们复制进公开 Release。

## 哈希与现实边界

内容哈希证明“当前字节与被签署字节一致”，不证明素材权利，也不证明场景对应物理现实。
权利必须由 rights receipt 和人工授权范围证明；真实采集、尺度、画质与可漫游范围必须
分别由 capture、SfM、GPU training、control/check points、真实浏览器 QA 和人工复核
证明。

即使 ZIP 独立验证通过，也仍须在下载包上完成 cold start、三机位截图、移动/旋转/缩放、
console、帧时间、内存、空洞、漂浮物、遮挡与视角相关颜色检查。真实浏览器 QA 和人工
签署缺一不可；哈希不能替代这些验收。

## 发布清单

1. 同一 scene identity 的十个 aggregate gate 全部 accepted；
2. `production_release_allowed=true` 且权利允许公开分发；
3. `build-production` 在干净输出路径成功；
4. 构建机与下载后的 ZIP 都通过 `verify-production`；
5. 最终 ZIP 通过 `audit-production-privacy`，报告 package content ID 一致；
6. 解压包在真实浏览器完成冷启动和交互 QA；
7. `stage-production-assets` 输出目录只含最终四件套，Release 不上传中间态；
8. 最后才创建正式 tag，并再次确认没有私有或中间产物。

更早的采集、训练、对齐和 Viewer 证据生成步骤见
[真实重建端到端手册](reconstruction-setup.md)，当前缺口见
[Production V1 状态与 TODO](../production-v1-status.md)。
