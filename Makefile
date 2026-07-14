.PHONY: help setup test ingest reconstruct world assets verify validate-handoff serve env clean

PY ?= python
PROJECT_ROOT := $(shell pwd)
ASSET_DELIVERABLE := handoff/deliverables/HANDOFF-001

help:
	@echo "无限村庄世界生成系统 - 命令清单"
	@echo ""
	@echo "  make setup             安装本机依赖"
	@echo "  make test              运行 Python + Viewer + Studio 测试"
	@echo "  make ingest            L0 输入处理 (input/ 照片+视频 → photos/)"
	@echo "  make reconstruct       端到端重建 (照片+视频 → 统一坐标系 → 3DGS → LOD)"
	@echo "  make world             生成 5x5 无限世界 (布局 → ply)"
	@echo "  make assets            确定性生成、验收并幂等注册 11 个模拟素材"
	@echo "  make validate-handoff  验收 GPT 交付物 (DELIV=交付目录)"
	@echo "  make serve             启动 Studio + 本地只读 adapter (http://127.0.0.1:8000/)"
	@echo "  make verify            运行关键验证"
	@echo "  make env               创建云端GPU任务环境(在AutoDL上执行)"
	@echo "  make clean             清理生成产物 (不动 assets/ 素材注册表)"

setup:
	$(PY) -m pip install -e ".[dev]"

test:
	$(PY) -m pytest tests/ -q
	node --test web/viewer/*.test.mjs
	node --test web/studio/*.test.mjs

ingest:
	$(PY) -m pipeline.ingest --input input --output photos

reconstruct:
	$(PY) -m pipeline.reconstruct --photos photos

world:
	$(PY) -m pipeline.generate_world --size 5 --seed 42

assets:
	$(PY) $(ASSET_DELIVERABLE)/scripts/generate.py --output $(ASSET_DELIVERABLE)
	$(PY) -m pipeline.validate_handoff $(ASSET_DELIVERABLE) \
		--feedback-dir handoff --register --assets-dir assets

# 用法: make validate-handoff DELIV=handoff/deliverables/HANDOFF-001
validate-handoff:
	$(PY) -m pipeline.validate_handoff $(DELIV)

serve:
	$(PY) -m pipeline.studio_server --host 127.0.0.1 --port 8000

verify:
	$(MAKE) test PY=$(PY)
	$(MAKE) assets PY=$(PY)
	$(MAKE) world PY=$(PY)
	$(PY) -m json.tool docs/contracts/studio-adapter-v2.schema.json >/dev/null
	$(PY) -m json.tool web/data/manifest.json >/dev/null
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
	rm -rf corpus/ layouts/ scenes/ recon/
	rm -rf web/data/recon/
	rm -rf verification/output/
