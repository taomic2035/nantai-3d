.PHONY: help setup verify env clean

PY ?= python
PROJECT_ROOT := $(shell pwd)

help:
	@echo "无限村庄世界生成系统 - 命令清单"
	@echo ""
	@echo "  make setup     安装本机依赖"
	@echo "  make verify    运行关键验证"
	@echo "  make env       创建云端GPU任务环境(在AutoDL上执行)"
	@echo "  make clean     清理生成产物"

setup:
	$(PY) -m pip install -e .

verify:
	$(PY) verification/verify_3dtiles_conversion.py
	$(PY) verification/verify_glm_layout.py

# 云端 GPU 任务环境 (在 AutoDL 实例上执行)
env:
	@echo "在云端 GPU 实例上执行以下命令:"
	@echo "  conda create -n nantai python=3.11 -y"
	@echo "  conda activate nantai"
	@echo "  pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118"
	@echo "  pip install hy3dgen segment-anything-2 groundingdino-py"
	@echo "  # 详见 cloud/setup_autodl.sh"

clean:
	rm -rf corpus/ assets/ layouts/ scenes/
	rm -rf verification/output/
