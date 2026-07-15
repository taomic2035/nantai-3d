# HANDOFF-002 — 素材字节跨平台可复现 (交办 GPT)

## 背景

HANDOFF-001 已交付并注册 11 个村庄 3DGS 素材，`assets/registry.json` 把每个素材的
**payload SHA-256 钉为信任根**：fresh checkout 后 `make assets` 重新生成素材，只有当
生成字节的 SHA 与 registry 记录一致时才幂等恢复；不一致则 `register` fail-closed，
拒绝用不同字节冒充同一 `asset_id`（这是 provenance-safety 的正确行为）。

问题：素材当前在 **macOS 上生成并 commit**。项目已迁到 Windows 后实测发现，11 个素材中
有 **2 个的 PLY 字节跨平台不一致**，导致 fresh `make assets` 在非 mac 平台于第 9 个素材
中止、world 降级为 8/11。经诊断，这是**跨平台浮点分歧**（平台 libm 的 `sin/cos/求和` 等
最后一位 bit 差异），逐平台稳定、跨平台不同。

**你的任务**：让 11 个素材的 PLY 字节**跨平台逐字节一致**（deterministic across OS），
整目录重新交付（含 manifest.json）。方法不限（量化程序化生成器输出，或用 image2 重生成），
只要满足下方**输出契约**与**验收**。我方通过验收后 `--register` 重新 baseline 全部 11 个 SHA。

## 诊断证据 (精确)

| 事项 | 结论 |
|---|---|
| 受影响素材 | `stone_wall_01`、`fence_wood_01`（其余 9 个已跨平台一致，无需改动但需随目录一并重交以统一 baseline）|
| 性质 | 逐平台 **run-to-run 稳定**（Windows 两次生成 SHA 相同），跨平台不同 → 非随机/非集合序，是浮点 |
| `stone_wall_01` | mac `1f74f80351b5…` vs Windows `b31266fde6a7…` |
| `fence_wood_01` | mac `026f44603b29…` vs Windows `08740a9cbc0c…` |
| `manifest.script_sha256` 漂移 | **无关**，纯 CRLF 检出差异，已由本仓库新增 `.gitattributes`(`*.py eol=lf`) 修复，勿再处理 |

> 当前 registry 全部 v3。重交付经 `--register` 后会 `replace()` 到 v4，旧版本进 history，
> 引用 `asset_id` 的布局无需改动。

## 输出契约 (必须满足)

1. **跨平台字节一致**：同一素材在 Windows / Linux / macOS 上生成，PLY 文件 SHA-256 必须相同。
2. **平台内确定性**：同一平台重复生成，SHA 必须相同（已满足，须保持）。
3. **坐标 / 格式 / 属性契约不变**：完全沿用 HANDOFF-001 的约定
   （右手系、Z 上、米制、地面 z=0；binary_little_endian 3DGS PLY；
   `x,y,z / nx,ny,nz / f_dc_0..2 / opacity(logit) / scale_0..2(log米) / rot_0..3(wxyz)`，均 float32）。
   素材清单、`footprint_m`、外观基调与 HANDOFF-001 一致。
4. **manifest.json**：schema v2，`handoff_id: "HANDOFF-002"`，11 项，每项带实测 `sha256`，
   `generator.script_sha256` 为生成脚本的 SHA（若交付脚本）。

### 推荐实现 (供参考，不强制)

浮点分歧发生在写盘前的 float64 中间计算被转成 float32 之前。最稳健的做法是**在序列化前把
每个属性数组量化到一个远粗于 libm 误差(~1e-15 相对)的固定网格**，例如：

```python
# 写 PLY 前，对所有 float 属性统一量化（示意）
xyz     = np.round(xyz,     6).astype(np.float32)   # 位置量化到 1e-6 m
f_dc    = np.round(f_dc,    6).astype(np.float32)
opacity = np.round(opacity, 6).astype(np.float32)
scale   = np.round(scale,   6).astype(np.float32)
rot     = np.round(rot / np.linalg.norm(rot, axis=1, keepdims=True), 6).astype(np.float32)
```

量化后，两平台仅差 1e-15 的中间值会舍入到同一个小数，再转 float32 得到同一字节。
（若改用 image2 重生成，同样需保证最终写盘前做等价的确定性量化/离散化。）

## 交付物结构

```
handoff/deliverables/HANDOFF-002/
├── manifest.json          # schema v2, handoff_id=HANDOFF-002, 11 项 + 实测 sha256
├── house_wood_01.ply
├── ...(共 11 个 ply, 与 HANDOFF-001 同名同 asset_id)
└── scripts/               # 可选: 生成脚本 (量化版) 或 image2 流程说明
```

## 验收 (自动 + 可复现性)

我方运行：

```bash
python -m pipeline.validate_handoff handoff/deliverables/HANDOFF-002
```

沿用 HANDOFF-001 全部硬阈值（schema / asset_id 与路径安全 / SHA-256 / 坐标约定 /
数值有限、scale 正、四元数有效 / ply 可解析 / 数量区间 / 地面 z≈0 / 尺寸偏差 ≤±50% /
颜色 std / 不透明度）。

**新增可复现性门禁**：
- 我方在本机（Windows）连跑两次生成，要求 11 个 SHA run-to-run 完全一致。
- 跨平台一致性由本仓库计划新增的 **CI 矩阵 (ubuntu + windows)** 强制：同一提交在两平台生成，
  断言 11 个 SHA 相同。CI 矩阵由 Opus 侧搭建；在其就绪前，请在交付说明里标注你验证过的平台，
  并优先采用上述"写盘前量化"以从原理上保证跨平台一致。

全 PASS 后我方执行：

```bash
python -m pipeline.validate_handoff handoff/deliverables/HANDOFF-002 \
  --feedback-dir handoff --register --assets-dir assets
```

`--register` 会把 11 个素材 `replace()` 到新版本并**重新 baseline registry SHA**；
FEEDBACK 自动写入 `handoff/FEEDBACK-HANDOFF-002.md`。

## 分工备注

- 素材生成 / 设计 / 图像处理属 **GPT (image2) 范围**；本文档（规格与验收）由 Opus 出。
- Opus 侧配套：`.gitattributes`(已交) 治 CRLF；CI 跨平台矩阵(计划中)做可复现性强制门；
  `register` 的 fail-closed 语义不变（不会为迁就平台差异而弱化信任根）。
