# web/viewer/vendor — 离线发行的前端依赖 (vendored)

Viewer 的 3 个 importmap 依赖已从固定版本 CDN 内联到本目录, 让 viewer + Studio (iframe 嵌
viewer) 在**无网络**时也能加载完整 3DGS 渲染, 而不是退回 DC point preview。

## 为什么 vendoring

- `web/viewer/index.html` 的 importmap 原本指向 `cdn.jsdelivr.net` (three) 与 `sparkjs.dev`
  (Spark)。离线 / CDN 故障 / 供应链变更时, `import('@sparkjsdev/spark')` 会超时 →
  `splat-layer.mjs` 降级 DC 点预览, full splat 不可用。
- vendored 后 importmap 全部指向 `./vendor/...`, 模块图闭合在仓库内, 零外部请求。

## 清单 (固定版本 + sha256, 可复现)

| 文件 | 来源 URL | sha256 | 字节 |
|---|---|---|---|
| `three/three.module.js` | https://cdn.jsdelivr.net/npm/three@0.180.0/build/three.module.js | `c8211c69345d2e9949dc7a8ac969380497aa0600a5a8ac6a459c8cd02dd9cb8a` | 603113 |
| `three/three.core.js` | https://cdn.jsdelivr.net/npm/three@0.180.0/build/three.core.js | `eb077d2417f61d3e6d9264c317cabc4ea35769ed6b0ab533067292a550784c20` | 1403455 |
| `three/addons/controls/OrbitControls.js` | https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/controls/OrbitControls.js | `b97879c748170baadeb3fb84cea1ffdf4674e283dc06042f34e2acb95a76042c` | 38703 |
| `three/addons/loaders/GLTFLoader.js` | https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/loaders/GLTFLoader.js | `67ac5551fdafa6e349bd80c8f8e5e39c136d6b2fb1ad647db9abb21dc86f9e4a` | 114739 |
| `three/addons/loaders/KTX2Loader.js` | https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/loaders/KTX2Loader.js | `c43052b95310199d50935bdc41fcd0fc347f25eac3b4f0245e6e4de1ef6e1d93` | 34635 |
| `three/addons/libs/ktx-parse.module.js` | https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/libs/ktx-parse.module.js | `f40c491f6c44dde511268121f778a0050e73b1a15fd844c1ae2c78c73213eafc` | 19932 |
| `three/addons/libs/zstddec.module.js` | https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/libs/zstddec.module.js | `5cbf818e842628a4464e748594a6deae18ceddda3c2f541e7b3a0ff5fc7611e2` | 39754 |
| `three/addons/math/ColorSpaces.js` | https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/math/ColorSpaces.js | `cc35c01c793cd17ccded7bc8142abffd3ce0d60dd6de8d5d216983bd05aee262` | 4290 |
| `three/addons/postprocessing/Pass.js` | https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/postprocessing/Pass.js | `444b409c235ead986893c472e720da1b779a56985c7d10b279c7944b52bd61c5` | 4218 |
| `three/addons/utils/BufferGeometryUtils.js` | https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/utils/BufferGeometryUtils.js | `fda7e946b8e0b5ab39b779206589e7a1079a22eb24efb89d7223e03fdfb1f751` | 35539 |
| `three/addons/utils/WorkerPool.js` | https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/utils/WorkerPool.js | `5ac7095fd566bc9ae48376055fd66edf27cb9ebbf9e1269dc206bfd4933ae9eb` | 3110 |
| `three/examples/jsm/libs/basis/basis_transcoder.js` | https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/libs/basis/basis_transcoder.js | `8478b5b6d6b74e7d3082b89f6417321d8d1dc0307f2b30d4484bb11b441696a1` | 57529 |
| `three/examples/jsm/libs/basis/basis_transcoder.wasm` | https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/libs/basis/basis_transcoder.wasm | `6cf17dc889352c42e9acf8897107978d127005fe3386c36a0e3845e27967630a` | 527333 |
| `spark/spark.module.js` | https://sparkjs.dev/releases/spark/2.1.0/spark.module.js | `c0355a962f68a6de9b13df69f05b1aba3614d9aec43a4504975daeb349126a8a` | 5379614 |

## 许可证

| 包 | 许可证 | 来源 | sha256 |
|---|---|---|---|
| `three@0.180.0` | MIT | https://cdn.jsdelivr.net/npm/three@0.180.0/LICENSE | `bfe119ea4fd413f5f7ca3fcd63adb0c4a073ed39daa2fe7d3e6b769e21272601` |
| `@sparkjsdev/spark@2.1.0` | MIT | https://cdn.jsdelivr.net/npm/@sparkjsdev/spark@2.1.0/LICENSE | `7ab7f9c7c389f20899bc02d2b4be19e33fcef27ce97ec08ff77cb052b06c5c6a` |

完整许可文本分别保存在 `three/LICENSE` 和 `spark/LICENSE`，并由离线依赖测试锁定哈希。
Spark npm 发布包的 LICENSE 无文末换行，vendoring 时只规范化为 POSIX 文本文末 LF，许可内容未改。

## 模块图（为什么需要这组闭包）

```
index.html importmap
  three                → vendor/three/three.module.js  → (相对) ./three.core.js
  three/addons/        → vendor/three/addons/
  @sparkjsdev/spark    → vendor/spark/spark.module.js

main.js         import 'three', 'three/addons/controls/OrbitControls.js',
                'three/addons/loaders/GLTFLoader.js'
GLTFLoader.js   import 'three', '../utils/BufferGeometryUtils.js'
KTX2Loader.js   import 'three', '../utils/WorkerPool.js',
                '../libs/ktx-parse.module.js', '../libs/zstddec.module.js',
                '../math/ColorSpaces.js'
KTX2Loader      runtime fetch → examples/jsm/libs/basis/basis_transcoder.js
                                + basis_transcoder.wasm
splat-layer.mjs import('@sparkjsdev/spark')  (懒加载)
OrbitControls.js import 'three'
BufferGeometryUtils.js import 'three'
Pass.js          import 'three'
spark.module.js  import 'three', 'three/addons/postprocessing/Pass.js'
                 wasm → data:application/wasm;base64 内联 (无独立 .wasm)
                 worker → Blob + createObjectURL 内联 (无外部 worker fetch)
```

Spark 的 WASM 与 Web Worker 都内联在 `spark.module.js` 内（data URI / blob URL），因此没有
额外二进制文件。KTX2Loader 不同：它在运行时读取 Basis transcoder JS/WASM，所以两者必须作为
同版本独立文件 vendoring；离线门禁同时从 KTX2Loader 静态导入继续遍历其三个辅助模块。

## 更新 / 复现

```sh
make vendor        # 按上表 URL 重新下载 (需要网络)
make verify-vendor # 校验 sha256 + importmap 无 CDN + 模块图离线闭合 (无需网络)
```

`make vendor` 覆盖本目录并重算 sha256; 升级版本时改 `web/viewer/vendor/fetch-vendor.sh` 里的
版本号, 重跑, 再把新 sha256 填回上述代码与许可证表格 (与 `assets/registry.json` 的
sha256 作风一致)。
