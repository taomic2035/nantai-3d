#!/usr/bin/env bash
# 重新下载 viewer 的 vendored 前端依赖 (three + Spark)。需要网络。
# 版本升级: 改下面的 THREE_VER / SPARK_VER, 重跑, 再用 verify_vendor.mjs 打印的 sha256
# 更新 web/viewer/vendor/VENDOR.md 表格。
set -euo pipefail

THREE_VER="0.180.0"
SPARK_VER="2.1.0"
JSDELIVR="https://cdn.jsdelivr.net/npm/three@${THREE_VER}"
SPARK="https://sparkjs.dev/releases/spark/${SPARK_VER}"
SPARK_NPM="https://cdn.jsdelivr.net/npm/@sparkjsdev/spark@${SPARK_VER}"

cd "$(dirname "$0")"
mkdir -p \
  three/addons/controls \
  three/addons/loaders \
  three/addons/libs \
  three/addons/math \
  three/addons/postprocessing \
  three/addons/utils \
  three/examples/jsm/libs/basis \
  spark

dl() { echo "  ↓ $2"; curl -fsSL --max-time 60 -o "$2" "$1"; }

dl "${JSDELIVR}/build/three.module.js"                         three/three.module.js
dl "${JSDELIVR}/build/three.core.js"                           three/three.core.js
dl "${JSDELIVR}/examples/jsm/controls/OrbitControls.js"        three/addons/controls/OrbitControls.js
dl "${JSDELIVR}/examples/jsm/loaders/GLTFLoader.js"            three/addons/loaders/GLTFLoader.js
dl "${JSDELIVR}/examples/jsm/loaders/KTX2Loader.js"            three/addons/loaders/KTX2Loader.js
dl "${JSDELIVR}/examples/jsm/libs/ktx-parse.module.js"         three/addons/libs/ktx-parse.module.js
dl "${JSDELIVR}/examples/jsm/libs/zstddec.module.js"           three/addons/libs/zstddec.module.js
dl "${JSDELIVR}/examples/jsm/math/ColorSpaces.js"              three/addons/math/ColorSpaces.js
dl "${JSDELIVR}/examples/jsm/postprocessing/Pass.js"           three/addons/postprocessing/Pass.js
dl "${JSDELIVR}/examples/jsm/utils/BufferGeometryUtils.js"     three/addons/utils/BufferGeometryUtils.js
dl "${JSDELIVR}/examples/jsm/utils/WorkerPool.js"              three/addons/utils/WorkerPool.js
dl "${JSDELIVR}/examples/jsm/libs/basis/basis_transcoder.js"   three/examples/jsm/libs/basis/basis_transcoder.js
dl "${JSDELIVR}/examples/jsm/libs/basis/basis_transcoder.wasm" three/examples/jsm/libs/basis/basis_transcoder.wasm
dl "${SPARK}/spark.module.js"                                  spark/spark.module.js
dl "${JSDELIVR}/LICENSE"                                       three/LICENSE
dl "${SPARK_NPM}/LICENSE"                                      spark/LICENSE
# Spark 2.1.0 npm 包的 LICENSE 无文末换行，统一为 POSIX 文本。
printf '\n' >> spark/LICENSE

echo "已下载 three@${THREE_VER} + Spark@${SPARK_VER}。sha256:"
find three spark -type f | sort | while read -r f; do
  printf "  %s  %s\n" "$(shasum -a 256 "$f" | cut -d' ' -f1)" "$f"
done
echo "→ 运行 'make verify-vendor' 校验 sha256 与离线闭合。"
