# Production CUDA 镜像发布与验证

这份手册只处理一件事：把仓库锁定的 CUDA 训练运行时构建为不可变 GHCR 镜像，
并把镜像、BuildKit SBOM/provenance、GitHub provenance 和 detached receipt
绑定到同一组内容 SHA。

镜像发布通过，只能把状态提升到 `published image contract`。它仍是
`modeled-unverified`，不等于 Production V1，也不证明真实场景训练完成。

## 四层状态不能混写

| 层级 | 通过条件 | 当前含义 |
|---|---|---|
| repository contract | lock、Dockerfile、probe、receipt 与 workflow 合同测试通过 | 仓库声明可执行 |
| published image contract | 精确 main SHA 的 workflow、OCI 检查、双 attestation 与下载后复验通过 | 某个不可变镜像字节集已发布 |
| fresh GPU clearance | 获批 GPU host 对同一镜像运行 fresh readiness，GPU/driver/executable 全部实测绑定 | 该 host 可尝试正式训练 |
| non-mock training | 同一 scene 的 production caller 产生并复验真实 3DGS result bundle | 才有真实训练产物 |

## 当前已验证的镜像

- Source commit：`f399db891b4db2562208201407b3f0151c94c724`
- Workflow：[30428129482](https://github.com/taomic2035/nantai-3d/actions/runs/30428129482)
- Image：`ghcr.io/taomic2035/nantai-3d-production-cuda@sha256:bd65e13522e11fde61ea1148fbed598407fc05c76bdaf1925ec06d9baeb0016d`
- Receipt 内容 SHA：`3b60d00c11ef9f513dea959ed70238a9421d552d776901b675afba110d6bae58`

该 workflow 的普通 CI、断网 no-GPU probe、OCI 检查、SBOM/provenance、双
attestation 与下载后复验均已通过，因此 `published image contract` 已完成。它
仍是 `modeled-unverified`；下一门是获批 NVIDIA host 对同一 digest 的 fresh GPU
clearance，不是重新构建镜像。

GitHub 托管 runner 没有 GPU。工作流中的 no-GPU probe 只检查包版本、CLI schema、
可执行文件和导入能力，不能证明 CUDA 可用；后续必须另跑 `fresh GPU clearance`，
再跑 `non-mock training`。

## 启动唯一的手动发布工作流

工作流只接受 `refs/heads/main`，只发布 `linux/amd64`，tag 带精确 source SHA。
正式消费永远使用 receipt 给出的 `image@sha256:<digest>` 身份，不从可变 tag 推断。

```powershell
$head = git rev-parse HEAD
$remote = git rev-parse origin/main
if ($head -ne $remote) { throw "local main differs from origin/main" }

gh workflow run production-cuda-image.yml --ref main
Start-Sleep -Seconds 5

$run = gh run list --workflow production-cuda-image.yml --limit 20 `
  --json databaseId,headSha,status,conclusion,url | ConvertFrom-Json |
  Where-Object {
    $_.headSha -eq $head -and
    $_.status -eq "completed" -and
    $_.conclusion -eq "success"
  } |
  Select-Object -First 1
if ($null -eq $run) { throw "successful exact-head image run missing" }
```

工作流先从锁定 Dockerfile 构建并推送 SHA tag，再用 action 返回的 digest 拉取和
probe；后续所有步骤只消费这个 digest。BuildKit 明确生成 SLSA v1 max provenance
和 SPDX SBOM。OCI inspector 会重新下载 manifest/blob，逐字节复核 digest、size、
predicate type 与 subject；任何缺失、重复或漂移都 fail closed。

## 下载并验证最终白名单

发布 artifact 只有两个 JSON。候选日志、attestation 临时 bundle、probe 中间文件和
容器缓存不会进入 artifact。

```powershell
$runId = $run.databaseId
$receiptRoot = ".nantai-studio\cuda-image\$runId"
$artifactName = "production-cuda-image-$head"
gh run download $runId --name $artifactName --dir $receiptRoot

$receiptPath = Join-Path $receiptRoot "production-cuda-image-release.json"
$verificationPath = Join-Path $receiptRoot `
  "production-cuda-image-verification.json"
if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) {
  throw "detached image receipt missing"
}
if (-not (Test-Path -LiteralPath $verificationPath -PathType Leaf)) {
  throw "workflow verification summary missing"
}

gh attestation verify $receiptPath -R taomic2035/nantai-3d `
  --deny-self-hosted-runners `
  --signer-workflow `
    taomic2035/nantai-3d/.github/workflows/production-cuda-image.yml `
  --signer-digest $head `
  --source-digest $head `
  --source-ref refs/heads/main

$imageIdentity = @'
from pathlib import Path
import sys

from pipeline.production_cuda_image_release import (
    load_production_cuda_image_release_bytes,
)

release = load_production_cuda_image_release_bytes(
    Path(sys.argv[1]).read_bytes()
)
if release.source_commit != sys.argv[2]:
    raise SystemExit("receipt source commit differs")
print(release.image_identity)
'@ | & .\.venv\Scripts\python.exe - $receiptPath $head
if ($LASTEXITCODE -ne 0 -or
    $imageIdentity -notmatch '^ghcr\.io/.+@sha256:[0-9a-f]{64}$') {
  throw "canonical receipt verification failed"
}

gh attestation verify "oci://$imageIdentity" -R taomic2035/nantai-3d `
  --bundle-from-oci `
  --deny-self-hosted-runners `
  --signer-workflow `
    taomic2035/nantai-3d/.github/workflows/production-cuda-image.yml `
  --signer-digest $head `
  --source-digest $head `
  --source-ref refs/heads/main
```

命令形式是 `gh attestation verify oci://...`，但实际 URI 必须由已经验证的 receipt
拼出；不要手写 image name、tag 或 digest。上面的 Python 调用会通过
`load_production_cuda_image_release_bytes` 重开 canonical JSON、复核内容 SHA 与
source commit，而不是依赖 PowerShell 的宽松 JSON 解析。

## Receipt 到 runtime policy 的固定映射

| Verified receipt 字段 | Private runtime policy 字段 |
|---|---|
| `image_name` + `image_digest` | `expected_container_identity` |
| `image_probe.torch_cuda_version`（锁定为 `11.8`） | `expected_cuda_runtime_version` |
| `image_probe.python_version` | `expected_python_version` |
| `image_probe.nerfstudio_version` | `expected_nerfstudio_version` |
| `image_probe.training_cli_schema_sha256` | `expected_training_cli_schema_sha256` |
| receipt 的必需训练选项投影 | `required_training_cli_options` |
| `image_probe.executables[python].sha256` | `expected_python_sha256` |
| `image_probe.executables[ns-train].sha256` | `expected_training_cli_sha256` |

这些镜像事实可以直接投影，不能由 operator 改写。下面四项是 host-specific 实测
事实，不能从镜像 receipt 猜测：

- 获批 GPU UUID；
- 最低显存；
- container runtime 的绝对路径与 executable SHA；
- `nvidia-smi` 的绝对路径与 executable SHA。

把两组事实写入私有
`.nantai-studio\private\production-runtime-policy-input.json`，再运行
`python -m pipeline.production_runtime_policy`。policy producer 只生成待验证合同；
只有同一 host 的 fresh checker report accepted，才得到 fresh GPU runtime 证据。

## 停止条件

- workflow 失败：保留 GHCR 候选供审计，修复代码或 lock 后用新提交重跑，不覆盖证据；
- receipt 或 OCI attestation 复验失败：不得生成 runtime policy；
- fresh GPU clearance 失败或过期：不得启动 production mutation；
- non-mock training 未完成：不得把 published image contract 写成真实 3DGS；
- 正式采集、accepted real-photo SfM、实测控制点或真实 Viewer QA 任一缺失：
  不等于 Production V1。
