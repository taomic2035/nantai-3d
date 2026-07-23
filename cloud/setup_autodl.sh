#!/bin/bash
# ⚠️ 旧素材生成愿景脚本 (L1 资产生成 / L0 构件分割 / L2 神经布局)
#   本脚本装 Hunyuan3D / SAM2 / GroundingDINO / GaussianCity,
#   不装 nerfstudio——它不是「照片 → 3DGS」训练链路。
#
#   真实云 GPU 3DGS 训练请用: cloud/train_3dgs_nerfstudio.sh
#   端到端手册: docs/manual/reconstruction-setup.md §5a
#
# 在 AutoDL 实例上执行此脚本, 搭建 L1/L0 素材生成环境
#
# 使用方法:
#   1. 在 AutoDL 租用 RTX 3060 12GB 或更高实例
#   2. 选择 PyTorch 2.4 + CUDA 11.8 镜像
#   3. 上传此脚本并执行: bash setup_autodl.sh
set -e

echo "=== 1. 创建 Python 3.11 环境 ==="
conda create -n nantai python=3.11 -y
source activate nantai

echo "=== 2. 安装 PyTorch (CUDA 11.8) ==="
pip install torch==2.4.1 torchvision==0.19.1 --index-url https://download.pytorch.org/whl/cu118

echo "=== 3. 安装 Hunyuan3D-2.1 ==="
pip install hy3dgen
pip install trimesh plyfile pygltflib

echo "=== 4. 安装 SAM2 + GroundingDINO ==="
# SAM2
git clone https://github.com/facebookresearch/segment-anything-2.git /tmp/sam2
cd /tmp/sam2 && pip install -e .
# 下载 SAM2 权重
wget https://dl.fbaipublicfiles.com/segment_anything_2/072824/sam2_hiera_large.pt -O /root/models/sam2_hiera_large.pt

# GroundingDINO
pip install groundingdino-py

echo "=== 5. 安装 GaussianCity (可选) ==="
git clone https://github.com/hzxie/GaussianCity.git /tmp/gaussiancity
cd /tmp/gaussiancity
pip install -r requirements.txt
cd extensions
for e in */; do
    cd "$e" && pip install . && cd ..
done

echo "=== 6. 安装辅助工具 ==="
pip install loguru pydantic zhipuai python-dotenv

echo "=== 完成 ==="
echo "环境就绪, 可执行 L1 资产生成 / L0 构件分割 / L2 神经布局"
echo "成本估算: 单次 L1 资产生成 ~¥1-2 (按 ¥1/h 计)"
