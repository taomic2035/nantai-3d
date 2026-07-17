# FEEDBACK · HANDOFF-CODEX-004 大重建分块流式

> 回执：Codex（Web Viewer lane）
> 日期：2026-07-17
> 对应规格：`handoff/HANDOFF-CODEX-004-stream-large-reconstructions.md`

## 结论

Viewer 对 `kind: "spatial-chunks"` 的消费代码已接通并通过本地自动化门禁：

- 以每块真实 `aabb` 到相机的水平距离调度，只选择附近的已烘焙块。
- 自动 LOD 保持 `2` 近、`1` 中、`0` 远；手动 LOD override 仍可用。
- Spark 可用时共享一个 `SparkRenderer`、按块加载全 3DGS PLY；Spark 不可用时保留按块
  DC point preview，且不会把 fallback 声称为全保真 3DGS。
- 块内坐标保持源 ENU 绝对坐标，只做统一 ENU→Three 右手旋转，不增加
  `world_offset` 或 per-chunk 平移。
- 只接受显式静态 `schema_version: 1` / `kind: "spatial-chunks"` manifest；
  出现 `grid` 即拒绝，因此真实重建绝不会被投影为 `on_demand:true`。
- 子 PLY 只能通过安全的 manifest 相对路径解析，拒绝绝对 URL、父目录逃逸、反斜杠、
  query/hash 等路径注入。
- provenance 标注只读顶层 `source`。缺失字段保持 `unknown`；分块不会提升
  `geometry_usability`、frame、units 或其它信任声称。
- 取景优先使用合法 `core_bounds`，缺失或非法时回退 `bounds`。`core_bounds` 只控制
  相机/雾/网格取景，不裁掉 core 外高斯；全量 `bounds` 仍是真实几何范围。

## 本次补齐：真实消费 `lod_fractions`

既有调度已按距离选 LOD，但没有读取 HANDOFF 新增的 `lod_fractions`。现已补齐：

- `web/viewer/spatial-reconstruction.mjs` 从 manifest 读取选中层级的声明密度。
- 合法密度必须是有限数且在 `(0, 1]`；不使用 8%/30% 等隐藏默认值。
- 仅当 chunk 同时有正安全整数 `point_count` 和合法密度时，才计算
  `ceil(point_count × lod_fraction)`。
- Spark 与 DC 两条流式路径都在 bridge 可读的 renderer state 中报告：
  `active_estimated_points` 与 `active_lod_fractions`。
- 任一活跃块证据不完整时，两项汇总均返回 `null`，不会拿部分合计冒充全部。
- HUD 只对完整证据显示 `~N splats`；波浪号明确它是按声明密度计算的近似值，不是重新
  解析 PLY 后的实测顶点数。

对应小步提交：

- `8eeb128 feat(viewer): consume declared chunk density`
- `42ddc96 feat(viewer): report streamed chunk density`

两笔提交均带：

```text
Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>
```

## 新鲜验证

执行：

```powershell
node --test web/viewer/*.test.mjs
```

结果：`106 passed / 0 failed`。

其中覆盖：

- 静态 spatial manifest 验证、AABB 距离与 LOD 选择；
- 声明密度读取、非法/缺失密度 fail-closed；
- Spark chunk 加载/替换/淘汰/失败清理/异步 supersede；
- DC point chunk fallback 的加载/替换/淘汰/失败隔离；
- provenance 只信 `source`；
- `core_bounds` 取景及旧产物回退；
- Viewer HUD/bridge 契约；
- 无 CDN 运行时依赖与 vendored SHA/许可证锁定。

## 仍未完成，不能过度声称

本回执验收的是 **Viewer 消费链路**，不是最终真实场景质量：

- 尚未用用户真实的 100 张图片或 1GB 视频完成 COLMAP 位姿、外部云 GPU 3DGS 训练和
  held-out 质量评估。
- 分块真实重建没有程序化续渲能力；走出 `chunks.json` 的有限几何范围就是无内容，不是
  “无限世界”。
- `~N splats` 是 manifest 声明驱动的估算。若要实测传输字节、GPU 峰值和交互帧率，仍须
  对最终训练 PLY 分块后做浏览器性能验收。
- 天空、玻璃、水面、无纹理区域和未拍摄体积的重建缺陷不会因 Viewer 分块而消失。

因此 `HANDOFF-CODEX-004` 的代码交办可关闭；项目级“真实 360° 任意坐标漫游”仍等待真实
采集/训练产物与端到端验收。
