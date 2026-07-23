#!/bin/bash
# 云 GPU：照片/视频 → 3D Gaussian Splatting（nerfstudio splatfacto）
# ── 这是「照片 → 可漫游 3D」的训练那一步（本机无 NVIDIA GPU 时的主路径）。
#    产出标准 INRIA-3DGS point_cloud.ply + training-request.json + training-result.json，
#    下载回本机用 pipeline.reconstruct --engine import 导入。
#    端到端手册：docs/manual/reconstruction-setup.md
#
# ⚠️ 诚实说明（不假装一键必成）：
#   - 需要一台 NVIDIA CUDA GPU（RTX 3060 12GB 起；免费档 Colab T4 也行）。账号/租赁你来。
#   - nerfstudio / gsplat 的安装依 CUDA 版本与 gsplat 编译而异，下面是规范起点；
#     若某步报错，多半是 torch/CUDA/gsplat 版本匹配问题，按报错调整（见文末排错）。
#   - ns-process-data 会在云上自己跑 COLMAP（你不需要本机 COLMAP 走云路径）。
#
# Provenance manifests（P1 Follow-up A）:
#   - 训练前 emit training-request.json，绑定输入照片/视频 SHA + config.yml SHA
#     + 训练意图（trainer / max_res / total_steps / seed）。
#   - 训练后 emit training-result.json，从实际 PLY / config.yml / training.log 字节
#     派生所有 SHA + GPU 环境（nvidia-smi）+ trainer 版本 + 退出码。
#   - 两 manifest 都是 content-addressed：本机 prepare_import.py 重算每个 SHA，
#     任一字节漂移即 fail-closed。
#   - 失败的训练（exit_code != 0）也能 emit result（无 PLY），用于诊断。
#   - 三层 evidence：trusted prefix 需要 registration quality report + content closed
#     + trainer identified；只 content closed 得 content-only receipt；都没有则无 evidence。
#
# 用法（在你租的云 GPU 实例上）：
#   bash train_3dgs_nerfstudio.sh <输入> [选项]
#     <输入> = 图片目录 或 单个视频文件
#   选项：
#     --seed N          训练随机种子（默认 42，必填——无种子不可复现）
#     --max-res N       最大训练分辨率（默认 800）
#     --total-steps N   训练步数（默认 10000）
#     --trainer-ver V   nerfstudio 版本（默认自动探测 ns-train --version）
#     --config-yml P    显式 config.yml 路径（否则用 nerfstudio 生成的）
set -euo pipefail

INPUT="${1:?用法: bash train_3dgs_nerfstudio.sh <图片目录|视频文件> [选项]}"
shift

# ── 默认参数 ──
SEED="${SEED:-42}"
MAX_RES="${MAX_RES:-800}"
TOTAL_STEPS="${TOTAL_STEPS:-10000}"
TRAINER_VER=""
EXPLICIT_CONFIG=""

# ── 解析可选参数 ──
while [ $# -gt 0 ]; do
  case "$1" in
    --seed)         SEED="$2"; shift 2 ;;
    --max-res)      MAX_RES="$2"; shift 2 ;;
    --total-steps)  TOTAL_STEPS="$2"; shift 2 ;;
    --trainer-ver)  TRAINER_VER="$2"; shift 2 ;;
    --config-yml)   EXPLICIT_CONFIG="$2"; shift 2 ;;
    *) echo "!! 未知参数: $1"; exit 2 ;;
  esac
done

WORK="${WORK:-$HOME/nantai_recon}"
PROC="$WORK/processed"
OUT="$WORK/outputs"
EXPORT="$WORK/export"
MANIFESTS="$WORK/manifests"
mkdir -p "$WORK" "$MANIFESTS"

# 训练 log 文件（tee 写入，训练后 SHA 绑定）
TRAIN_LOG="$WORK/training.log"
: > "$TRAIN_LOG"

# 捕获训练退出码（即使失败也要 emit result）
TRAIN_EXIT=0

echo "=== 1. 安装 nerfstudio（含 gsplat/splatfacto 后端）==="
# AutoDL 建议先选 PyTorch 2.x + CUDA 11.8 镜像；Colab 官方 notebook 会自动装。
python -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())" \
  || { echo "!! 先装匹配 CUDA 的 torch，再重跑"; exit 1; }
pip install --upgrade pip
pip install nerfstudio          # 会拉 gsplat；失败见文末排错
command -v ns-train >/dev/null || { echo "!! nerfstudio 安装未成功"; exit 1; }
command -v colmap  >/dev/null || echo "注意: 云上无 colmap，ns-process-data 需要它；装：apt-get install -y colmap 或用带 colmap 的镜像"

# 探测 trainer 版本（用于 result manifest）
if [ -z "$TRAINER_VER" ]; then
  TRAINER_VER="$(ns-train --version 2>/dev/null || echo unknown)"
  echo "[INFO] ns-train version: $TRAINER_VER"
fi

# ── 准备 capture manifest（输入目录/文件的 SHA + 文件列表）──
CAPTURE_MANIFEST="$MANIFESTS/capture_manifest.json"
python - "$INPUT" "$CAPTURE_MANIFEST" <<'PYEOF' || {
  echo "!! 生成 capture manifest 失败；继续但不产 provenance manifests"
  CAPTURE_MANIFEST=""
}
import json, hashlib, sys
from pathlib import Path
inp, out = Path(sys.argv[1]), Path(sys.argv[2])
files = []
if inp.is_dir():
    files = sorted(p for p in inp.rglob("*") if p.is_file())
else:
    files = [inp]
entries = []
for f in files:
    data = f.read_bytes()
    entries.append({
        "relpath": str(f.relative_to(inp) if inp.is_dir() else f.name),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    })
manifest = {
    "version": 1,
    "source_dir": str(inp),
    "file_count": len(entries),
    "files": entries,
    "manifest_sha256": hashlib.sha256(
        json.dumps(entries, sort_keys=True).encode()
    ).hexdigest(),
}
out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print(f"[CAPTURE] {len(entries)} files, manifest_sha256={manifest['manifest_sha256'][:16]}...")
PYEOF

echo "=== 2. 位姿+预处理（ns-process-data 内部跑 COLMAP）==="
if [ -d "$INPUT" ]; then
  ns-process-data images --data "$INPUT" --output-dir "$PROC" 2>&1 | tee -a "$TRAIN_LOG"
else
  ns-process-data video --data "$INPUT" --output-dir "$PROC" 2>&1 | tee -a "$TRAIN_LOG"   # 视频会自动抽帧
fi

# ── Emit training-request.json（训练前，绑定输入 + 意图）──
REQUEST_MANIFEST="$MANIFESTS/training-request.json"
if [ -n "$CAPTURE_MANIFEST" ]; then
  echo "=== 2b. Emit training-request.json ==="
  # 准备 config.yml（如显式给定则用之，否则用一个临时 placeholder——
  # nerfstudio 实际 config 在训练后才生成，request 绑定的是 operator 意图 config）
  if [ -n "$EXPLICIT_CONFIG" ] && [ -f "$EXPLICIT_CONFIG" ]; then
    CONFIG_FOR_REQUEST="$EXPLICIT_CONFIG"
  else
    CONFIG_FOR_REQUEST="$MANIFESTS/operator-intent-config.yml"
    cat > "$CONFIG_FOR_REQUEST" <<YAML
# operator intent config — actual trainer config.yml is produced by ns-train
trainer: nerfstudio-splatfacto
max_resolution: $MAX_RES
total_steps: $TOTAL_STEPS
random_seed: $SEED
YAML
  fi

  # 用仓库内 emit_training_provenance.py（本脚本假设已 clone nantai 到云实例）
  EMIT_SCRIPT="scripts/emit_training_provenance.py"
  if [ -f "$EMIT_SCRIPT" ]; then
    python "$EMIT_SCRIPT" request \
      --input "capture_manifest:$CAPTURE_MANIFEST" \
      --config-yml "$CONFIG_FOR_REQUEST" \
      --trainer nerfstudio-splatfacto \
      --trainer-version "$TRAINER_VER" \
      --max-resolution "$MAX_RES" \
      --total-steps "$TOTAL_STEPS" \
      --seed "$SEED" \
      --output "$REQUEST_MANIFEST" \
      || { echo "!! emit request 失败；继续但不产 provenance"; REQUEST_MANIFEST=""; }
  else
    echo "[WARN] 找不到 $EMIT_SCRIPT——跳过 provenance manifest emission"
    REQUEST_MANIFEST=""
  fi
else
  REQUEST_MANIFEST=""
fi

echo "=== 3. 训练 3DGS（splatfacto；普通版 ~6GB 显存, 适合免费 T4）==="
# 捕获退出码：失败也要 emit result
set +e
ns-train splatfacto --data "$PROC" --output-dir "$OUT" \
  --viewer.quit-on-train-completion True 2>&1 | tee -a "$TRAIN_LOG"
TRAIN_EXIT=${PIPESTATUS[0]}
set -e
echo "[INFO] ns-train exit code: $TRAIN_EXIT"

echo "=== 4. 导出标准 INRIA-3DGS .ply ==="
CONFIG=$(find "$OUT" -name config.yml | sort | tail -1)
PLY="$EXPORT/point_cloud.ply"
if [ "$TRAIN_EXIT" -eq 0 ] && [ -n "$CONFIG" ]; then
  echo "使用 config: $CONFIG"
  ns-export gaussian-splat --load-config "$CONFIG" --output-dir "$EXPORT" 2>&1 | tee -a "$TRAIN_LOG"
  EXPORT_EXIT=${PIPESTATUS[0]}
  if [ "$EXPORT_EXIT" -ne 0 ] || [ ! -f "$PLY" ]; then
    echo "!! ns-export 失败或未产出 PLY"
    TRAIN_EXIT=1
    PLY=""
  fi
else
  echo "[WARN] 训练失败（exit=$TRAIN_EXIT），跳过 export"
  PLY=""
fi

# ── Emit training-result.json（训练后，从实际字节派生）──
RESULT_MANIFEST="$MANIFESTS/training-result.json"
if [ -n "$REQUEST_MANIFEST" ] && [ -f "$REQUEST_MANIFEST" ]; then
  echo "=== 4b. Emit training-result.json ==="
  ACTUAL_CONFIG="${CONFIG:-$CONFIG_FOR_REQUEST}"
  if [ -z "$ACTUAL_CONFIG" ] || [ ! -f "$ACTUAL_CONFIG" ]; then
    echo "[WARN] 无实际 config.yml——result manifest 将绑定 operator-intent config"
    ACTUAL_CONFIG="$CONFIG_FOR_REQUEST"
  fi

  EMIT_ARGS=(
    "result"
    "--request" "$REQUEST_MANIFEST"
    "--config-yml" "$ACTUAL_CONFIG"
    "--log" "$TRAIN_LOG"
    "--trainer" "nerfstudio-splatfacto"
    "--trainer-version" "$TRAINER_VER"
    "--exit-code" "$TRAIN_EXIT"
    "--output" "$RESULT_MANIFEST"
  )
  if [ -n "$PLY" ] && [ -f "$PLY" ]; then
    EMIT_ARGS+=("--ply" "$PLY")
  elif [ "$TRAIN_EXIT" -eq 0 ]; then
    echo "!! 训练 exit=0 但无 PLY——emit interrupted result"
  fi
  if [ "$TRAIN_EXIT" -ne 0 ]; then
    EMIT_ARGS+=("--error-message" "trainer/export failed (exit=$TRAIN_EXIT)")
  fi

  python "$EMIT_SCRIPT" "${EMIT_ARGS[@]}" \
    || echo "!! emit result 失败（manifest 未产出）"
else
  echo "[INFO] 跳过 result manifest emission（无 request manifest）"
fi

echo ""
echo "=== 完成 ==="
if [ -n "$PLY" ] && [ -f "$PLY" ]; then
  echo "产物: $PLY"
  echo "provenance: $REQUEST_MANIFEST + $RESULT_MANIFEST"
  echo "⬇️  下载这三者回本机 D:\\vibecoding\\nantai\\trained\\，然后本机跑："
  echo "   python scripts\\normalize_ply_quats.py trained\\point_cloud.ply   # 若四元数非单位"
  echo "   python scripts\\prepare_import.py trained\\point_cloud.ply \\"
  echo "       --training-request trained\\training-request.json \\"
  echo "       --training-result trained\\training-result.json"
  echo "   # 如有 registration quality report，再加："
  echo "   #   --registration-quality-report rq.json \\"
  echo "   #   --registration-json registration.json \\"
  echo "   #   --registration-quality-policy policy.json"
else
  echo "⚠️  训练未成功产出 PLY（exit=$TRAIN_EXIT）"
  echo "provenance (诊断用): $RESULT_MANIFEST"
  echo "查看 $TRAIN_LOG 排错"
fi
echo ""
echo "⏱ T4 上约 60-90 min。Colab 免费档断线会清空——务必先下载 point_cloud.ply + manifests 再关机。"
echo ""
echo "── 排错 ──"
echo "  gsplat 编译失败: 确认 nvcc 版本与 torch 的 CUDA 一致; 或 pip install gsplat 单独看报错。"
echo "  ns-process-data 报缺 colmap: apt-get install -y colmap（需 CUDA 版可用）或换带 colmap 的镜像。"
echo "  显存 OOM: 用普通 splatfacto（非 -big）; 减少输入图数/分辨率。"
echo "  provenance emit 失败: 检查 nantai 仓库是否已 clone 到云实例当前目录。"
