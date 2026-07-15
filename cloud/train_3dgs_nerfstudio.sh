#!/bin/bash
# 云 GPU：照片/视频 → 3D Gaussian Splatting（nerfstudio splatfacto）
# ── 这是「照片 → 可漫游 3D」的训练那一步（本机无 NVIDIA GPU 时的主路径）。
#    产出标准 INRIA-3DGS point_cloud.ply，下载回本机用 pipeline.reconstruct --engine import 导入。
#    端到端手册：docs/manual/reconstruction-setup.md
#
# ⚠️ 诚实说明（不假装一键必成）：
#   - 需要一台 NVIDIA CUDA GPU（RTX 3060 12GB 起；免费档 Colab T4 也行）。账号/租赁你来。
#   - nerfstudio / gsplat 的安装依 CUDA 版本与 gsplat 编译而异，下面是规范起点；
#     若某步报错，多半是 torch/CUDA/gsplat 版本匹配问题，按报错调整（见文末排错）。
#   - ns-process-data 会在云上自己跑 COLMAP（你不需要本机 COLMAP 走云路径）。
#
# 用法（在你租的云 GPU 实例上）：
#   bash train_3dgs_nerfstudio.sh <输入>   # <输入>=图片目录 或 单个视频文件
set -euo pipefail

INPUT="${1:?用法: bash train_3dgs_nerfstudio.sh <图片目录|视频文件>}"
WORK="${WORK:-$HOME/nantai_recon}"
PROC="$WORK/processed"
OUT="$WORK/outputs"
EXPORT="$WORK/export"
mkdir -p "$WORK"

echo "=== 1. 安装 nerfstudio（含 gsplat/splatfacto 后端）==="
# AutoDL 建议先选 PyTorch 2.x + CUDA 11.8 镜像；Colab 官方 notebook 会自动装。
python -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())" \
  || { echo "!! 先装匹配 CUDA 的 torch，再重跑"; exit 1; }
pip install --upgrade pip
pip install nerfstudio          # 会拉 gsplat；失败见文末排错
command -v ns-train >/dev/null || { echo "!! nerfstudio 安装未成功"; exit 1; }
command -v colmap  >/dev/null || echo "注意: 云上无 colmap，ns-process-data 需要它；装：apt-get install -y colmap 或用带 colmap 的镜像"

echo "=== 2. 位姿+预处理（ns-process-data 内部跑 COLMAP）==="
if [ -d "$INPUT" ]; then
  ns-process-data images --data "$INPUT" --output-dir "$PROC"
else
  ns-process-data video --data "$INPUT" --output-dir "$PROC"   # 视频会自动抽帧
fi

echo "=== 3. 训练 3DGS（splatfacto；普通版 ~6GB 显存, 适合免费 T4）==="
ns-train splatfacto --data "$PROC" --output-dir "$OUT" --viewer.quit-on-train-completion True

echo "=== 4. 导出标准 INRIA-3DGS .ply ==="
CONFIG=$(find "$OUT" -name config.yml | sort | tail -1)
echo "使用 config: $CONFIG"
ns-export gaussian-splat --load-config "$CONFIG" --output-dir "$EXPORT"

echo ""
echo "=== 完成 ==="
echo "产物: $EXPORT/point_cloud.ply"
echo "⬇️  下载它回本机 D:\\vibecoding\\nantai\\trained\\point_cloud.ply，然后本机跑："
echo "   python scripts\\normalize_ply_quats.py trained\\point_cloud.ply   # 若四元数非单位"
echo "   python scripts\\prepare_import.py trained\\point_cloud.ply         # 生成导入契约+打印导入命令"
echo ""
echo "⏱ T4 上约 60-90 min。Colab 免费档断线会清空——务必先下载 point_cloud.ply 再关机。"
echo ""
echo "── 排错 ──"
echo "  gsplat 编译失败: 确认 nvcc 版本与 torch 的 CUDA 一致; 或 pip install gsplat 单独看报错。"
echo "  ns-process-data 报缺 colmap: apt-get install -y colmap（需 CUDA 版可用）或换带 colmap 的镜像。"
echo "  显存 OOM: 用普通 splatfacto（非 -big）; 减少输入图数/分辨率。"
