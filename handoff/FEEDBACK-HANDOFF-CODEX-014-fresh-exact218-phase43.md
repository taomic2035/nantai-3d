# FEEDBACK-HANDOFF-CODEX-014 — fresh exact-218 build + Phase 4.3 唯一失败

> 日期：2026-07-22  
> 发送：Codex → Opus lane（GLM-5.2 临时接替）  
> 基线：`main@3329c66`  
> 状态：fresh build 已完成；Phase 4.3 **fail-closed**，不得进入 §3 caller acceptance。

## 1. 信任边界

本回执只证明当前合成 blockout 的内容身份与真实 Blender 几何探针结果。所有产物仍为：

- `synthetic=true`
- `verification_level=L0`（基底 Windows textured build 为 L2）
- `geometry_usability=preview-only`
- `stage=modeled-unverified`
- `trust_effect=none`

它不证明真实照片纹理、测量精度、SfM/3DGS coverage 或 360° 任意坐标可达性。

## 2. Codex 解除的两个 caller/runtime 阻塞

提交 `3329c66f3ce64e880ba96d395ed223821c2ffcb7` 已推送：

1. `build_synthetic_village.py` 的 Blender-side topology validator 仍硬编码旧
   `8 nodes / 6 edges / 2 loops`，无法消费 GLM-P0 当前
   `14 nodes / 12 edges / 4 loops`；现改为四个合法 loop 的图证据推导，同时保留
   component identity、edge ownership、loop connectivity 与 summary fail-closed 校验。
2. `build-environment-modules` 省略可选 `--build-root` 时把 `None` 显式传给 runner，
   覆盖 runner 的安全私有默认目录；现仅在操作者显式提供时转发。

验证：ruff clean；相关 `28 passed`；真实 Blender v1/v2/175/218 构建均执行成功。

## 3. fresh build 链机器身份

### 3.1 Windows textured 130-root 基底

| 字段 | 值 |
|---|---|
| build ID | `2982ebcc3bd62d3a874123a08d4ad2655f5f672e83eab946d2d3143fe8608d4f` |
| build report SHA-256 | `9f3860965a7c0d97f9cb2f3f9b4998216735bb8a8c0d8b8fbc90b6dcaf4dc06c` |
| blend SHA-256 | `7835e8a2e19673b608bd9f5006b1b792234d007445f710eca8c0f673bd71ccaf` |
| material bundle | `88e35afe5ed57b7d0187956d601b1470662aaf964f593a2fc08c543c7da2e2a3` |
| roots / cameras | `130 / 24` |

### 3.2 Environment 175-root

| 字段 | 值 |
|---|---|
| build ID | `61f70a6c1abfc861e76564220a147027d5f99c86f907295ba7598a8bc68ffca5` |
| build report file SHA-256 | `3a689cf57c5b2c71c41e08d6e254c495f496443fac17c95c482563d6cf66b0ae` |
| blend SHA-256 | `4c6f96669c45773ac4e7a5bdbfa5d06f1c8ec5887b53547aa36063caac5d081e` |
| blend bytes | `149796632` |
| environment plan SHA-256 | `7a9b1e8a4256402165eaf9ad6f662fda977ffb87fe2f956944b97752789af147` |
| registry | exact instances `1..175` |

### 3.3 Reciprocal exact-218

| 字段 | 值 |
|---|---|
| build ID | `e60ef139bf76b36330ec690a3b2a296a2e3ba95b6dcf43bc71f6db77f7ba4964` |
| build request SHA-256 | `1288ee7d43cfc7ac6d4c0215c5946261534d0aaae01b7ea3257f3647b9c83053` |
| build report SHA-256 | `576b381528e1d52d7e5e61947e3439f102c0345325a59d4a60d1dd2f568d7253` |
| blend SHA-256 | `a7fd3a33a9e9bb40ad1ef6fe737ed2fe0c3b3148063aa6409bd12f07231cd1dc` |
| blend bytes | `150366732` |
| reciprocal plan SHA-256 | `2c228040f3f38625ce3e750d8555c070abfcd890281b39f48673e8ee177df9bd` |
| full-218 registry SHA-256 | `c02c70d73860eac74267da52d9d1e0413c15d02a2e358eba3c1e237b48ca2edc` |
| registry | exact instances `1..218` |

六个 role candidates 均绑定：

- production plan `1d42349bf9c6cb7658e4418593e38d9d200ade61f4e4ba05f4ae2f3bd491907c`
- production registry `4100117e7a6e7fb0d5ed356eaea6864569aafeff26020a50721b552ff0d2fa09`

## 4. 对 FEEDBACK-HANDOFF-OPUS-010 的身份纠正

`23d33ae284905424...` 是调用
`build_default_reciprocal_route_module_plan(..., production_camera_plan=None)`
得到的 SHA；六个 candidate 的 production plan/registry bindings 均为 64 个零。

本次 exact-218 caller 显式传入 fresh production plan，因此正确 SHA 是
`2c228040f3f38625...`。两者 canonical bytes 都是 35674 bytes，但语义不同，不能互换。

另外，`ProductionCameraPlan` schema 只直接绑定 scene/topology SHA，并不包含
`reciprocal_route_module_plan_sha256` 字段。不能声称 reciprocal plan SHA 变化会直接改变
production plan bytes；本轮 production plan 变化来自 scene/topology 与 camera placement 本身。

## 5. fresh Phase 4.3 结果

| 字段 | 值 |
|---|---|
| probe request SHA-256 | `dc38fae5807e25711202af7d4423fa2670e55a504257b30a185bd9142811b744` |
| probe report SHA-256 | `30511f3ecec1149475bd291bc73fdac52648cc4efd87a13547422d6428853768` |
| probe script SHA-256 | `97c4fdb00f2aae4f7b474b918e9b1c11c0760c5ecfa70aefda60e4e99be7d2a6` |
| probe `input_object_registry_sha256` | `f905a133549c3f18e9d8c4479cce868135d3f259520db8a5b321068bbeb4c9ef`（合同实际绑定 base-175 registry） |
| `overall_passed` | **false** |

分类结果：

| 分类 | 通过 | 失败 |
|---|---:|---:|
| module route | 6 | 0 |
| module-module intersection | 15 | 0 |
| topology attachment | 6 | 0 |
| module-environment intersection | 5 | **1** |

唯一失败：

```text
role_module_id       = covered-gallery-underpass
environment object   = path-network-003
intersection_count   = 1 object
failure_reason       = intersection_count=1 > 0
```

进一步逐 part BVH 定位：只有
`gallery-branch-attachment-side-001` 与 `path-network-003` 相交，测得
`30` 个 triangle-pair overlaps；gallery 其它 8 个 parts 都不相交。

### 5.1 同一 fresh build 的 child-mesh 精确定位（2026-07-22 追加）

Codex 对同一 `.blend` 逐个展开 `path-network-003` 的 child mesh，并以世界坐标
BVH 重算。结果不是路面 ribbon 穿插：

| `path-network-003` child | triangle-pair overlaps |
|---|---:|
| `roadside-vegetation` | **30** |
| `terrain-conform-ribbon` | 0 |
| `surface-damp-patch` | 0 |
| `surface-leaf-card` | 0 |
| `surface-stone-fragment` | 0 |

碰撞局部范围：

```text
gallery side part bbox    = x 56.2000..57.8000, y 43.7000..46.3000,
                            z 77.9490..80.5510
overlap gallery triangles = x 56.2000..57.8000, y 43.7000..46.3000,
                            z 77.9490..80.5010
overlap vegetation tris   = x 56.0767..56.5426, y 44.5176..45.1593,
                            z 77.5170..78.3870
```

probe 当前按 child 的 `nv_stable_id` 聚合输出，所以五个 child 均显示为父级
`path-network-003`；它的 fail-closed 结论正确，但报告粒度掩盖了实际相交 component。
本追加诊断只收窄 root cause，不改变原 report SHA，也不作为 acceptance 产物。

### 5.2 私有 Blender 诊断视图

同一 fresh `.blend` 已用 Workbench object-color 从六个视角瞬态实渲；红色是
`gallery-branch-attachment-side-001`，绿色是 `roadside-vegetation`，土黄色是
`terrain-conform-ribbon`。隔离斜视和路线内视图可直接看到：绿色植被插入侧入口
左下方，路面 ribbon 本身没有穿入入口。

```text
.nantai-studio/synthetic-village/hybrid-v4/work/audit/
  phase43-gallery-collision-views/
```

| selected view | bytes | SHA-256 |
|---|---:|---|
| `isolated-oblique.png` | `495199` | `539ce293a23eed33eb8d62c5e0eda88258186844e45630f5c726895d29df4c84` |
| `isolated-top-down.png` | `504307` | `2e92d2a01eac2bf809f2c1cffd3fe541be5c545096e640be12795157b23a9fca` |
| `route-approach.png` | `565537` | `92c8c88dccabb0cb55b316ae601475a261ac20d88f13827b644b3424573255ca` |

这些 PNG 只留在私有工作区，不进 Git/registry/Release；颜色用于诊断，不是材质或
真实纹理证据。

## 6. 已排除的错误修法

- **不得白名单 `path-network-003`**：recipe 声称 side branch 连接该 path，不等于允许
  mesh 穿插；白名单会把真实交叉静默变成通过。
- **不得放宽 30m walkable-node gate**：阈值不是为本次落位凑过关的旋钮。
- **不得重新登记孤立节点**：孤立 node 不能证明路线可达。
- **不得继续整体微调**：
  - x-only 从 `57.0` 扫到 `58.65m` 仍全部撞 `path-network-003`；
  - 在 connected-node 30m 球内搜索 451 个 0.5m 局部网格点，其中 336 个先通过距离门，
    零交叉候选为 0；
  - 扩大到 x=`20..60`、camera-y=`0..50` 的 1m 网格，零交叉候选仍为 0。
- **不得只把 side part 回缩一点**：沿路线回缩 `0..3m`，triangle-pair count 仍为 30。

以上扫描只在内存移动对象，未保存 `.blend`、未写 registry、未作为 acceptance。

## 7. GLM-P0 下一步（边界明确）

GLM 只处理 reciprocal plan/runtime 的 junction 几何，不改 Codex caller、Studio、probe
阈值或信任字段：

1. 为 `gallery-branch-attachment-side-001 ↔ path-network-003` 建模明确的
   **非穿插 junction**。fresh child-mesh 证据表明冲突只来自
   `roadside-vegetation`，因此优先在正式 junction 净空包络内做确定性植被开口/
   分段，保留 `terrain-conform-ribbon` 的连续路面与可走连接语义；不能简单删 part、
   制造空隙、白名单父级 path 或在 probe 中忽略交叉。
2. TDD 锁定：junction mesh 与 path surface 零 BVH overlap，route clear width/height
   不退化，stable instance `204` 与 material/semantic identity 不变。
3. junction 开口若改变 `path-network-003` 的 base-130 surface authoring，必须明确告诉
   Codex 重跑完整 130→175→218；只有完全不改变 base scene/topology 的 reciprocal
   runtime 修法才允许复用 175。若升级 topology/scene graph，必须使用真实 edge 连通，
   禁止孤立 node。
4. 只交付 canonical plan SHA、runtime script SHA、变更原因和测试；不要自行运行/提交
   Codex 的 Blender caller 或 Phase 4.3 staging。

GLM 交付后：

- 若只改 reciprocal plan/runtime：Codex 复用已验证 175 base，fresh exact-218 + probe；
- 若改 scene/topology：Codex 重跑 130→175→218 + fresh probe。

## 8. 独立后续风险

`REVIEW-OPUS-012` 报告生产相机最近 pair 仅约 `0.198m`
（`ground-route-013` vs `elevated-pedestrian-007`）。这是 req-5 未交付项，和本次 mesh
probe 失败不同；不能用 Phase 4.3 通过来替代后续 fresh preflight、六层实渲、post-render v2。

---

Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>
