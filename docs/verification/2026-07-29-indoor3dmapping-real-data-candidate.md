# Indoor3Dmapping 真实数据候选审计 — 2026-07-29

## 结论

这份 CC BY 4.0 数据已用于真实 RGB、深度、位姿和浏览器消费链的诊断，但当前候选
**拒绝进入 accepted real-photo SfM**。RGB-D 融合物只允许作为
`dc-point-preview / preview-proxy` 使用，不是 3DGS、不是实测对齐，也不是
Production V1 候选资产。

所有原始媒体、COLMAP 数据库、融合点云、空间块、日志与 Viewer 副本均位于忽略的
`.nantai-studio/`，未进入 Git 或 Release。

## 数据源与权利

| 项目 | 已核验事实 |
|---|---|
| 来源 | [Zenodo Indoor3Dmapping](https://zenodo.org/records/6367381)，DOI `10.5281/zenodo.6367381` |
| 许可 | CC BY 4.0 |
| 官方归档 | 123,593,016 bytes |
| 官方 MD5 | `9d9e21f5031b408dbeaffd457ec36965` |
| 本地归档 SHA-256 | `e7c7c528c6fd8bf3325100d1f92c22de02a398586cad9d21cb61f6ffdf849023` |
| 内容 | 99 张 1920×960 RGB 全景、99 张 dense depth、99 张 sparse depth、发布的 XYZ 米制位置与四元数 |

许可证明这份外部测试输入可用于当前诊断，不会自动证明任一派生模型质量、坐标轴
解释、控制点精度或正式发布资格。

## COLMAP 结果与拒绝依据

三个独立方案均使用 99 张真实全景输入。表中只列每次最大模型；零散双帧小模型不
计作覆盖成功。

| 方案 | 注册 | 最长连续缺失 | 3D 点 | 重投影均值 | 对发布位置的 Sim3 |
|---|---:|---:|---:|---:|---|
| spherical | 56/99 | 43 | 4,099 | 0.808 px | RMS 5.982 m；median 5.271 m；p95 8.938 m；max 12.408 m |
| rig sequential | 25/99 | 59 | 2,528 | 0.714 px | RMS 6.539 m；median 6.360 m；p95 9.784 m；max 11.542 m |
| rig + spatial priors | 23/99 | 59 | 2,380 | 0.734 px | RMS 6.757 m；median 6.846 m；p95 9.902 m；max 11.643 m |

三次运行都存在大段未注册视角和米级位姿残差，因此不能生成
`quality_accepted=true`，也不能作为 non-mock CUDA 训练的 production prerequisite。
较低的像素重投影误差只说明各自已注册子图内部可以拟合，不能补偿场景覆盖和外部
位姿一致性的失败。

## RGB-D 诊断候选

深度按数据集编码 `(65535 - raw) * 16 / 65535` 解码，`raw=0` 视为无效。发布资料
没有给出足以唯一确定 panorama/depth、设备体坐标与世界坐标关系的轴向合同。

自动重叠排名并不稳定：

| 搜索 | 最优假设 | trimmed NN | `≤0.3 m` 比例 |
|---|---|---:|---:|
| v1 | inverse quaternion、horizontal sign -1、yaw 173° | 0.14245 m | 0.501 |
| v2/v3 | forward quaternion、horizontal sign +1、yaw -122° | 0.15297 m | 0.512 |

用于浏览器诊断的 inverse quaternion、sign -1、yaw 155° 是物理上较可解释的预览
假设，但不是权威标定。它生成 841,174 个 DC 彩色点、72 个 5 m 空间块和 LOD
0/1/2；manifest 明确保留：

```text
accepted_real_photo_sfm=false
full_3dgs=false
render_fidelity=dc-point-preview
geometry_usability=preview-proxy
camera_axis_calibration=unpublished-overlap-ranked-hypothesis
frame_id=indoor3dmapping-published-metric-axis-unverified
trust_effect=none
```

## 真实浏览器 QA

本次浏览器实跑发现并关闭了两个仓库级缺陷：

1. orbit 模式曾错误地按相机位置而不是 `controls.target` 选择空间块，导致真实大坐标
   场景显示 `0/72`；提交 `a86739e` 后 orbit 加载 `21/72`，自由漫游位置加载
   `30/72`。
2. 未声明 full 3DGS 的空间数据曾错误进入 Spark；提交 `9bce813` 后只有显式
   `source.full_3dgs=true` 且 `source.render_fidelity=full-3dgs` 才能进入 Spark，
   本候选正确降级为 `dc-point-chunks`，并从 PLY `r/g/b` 或 `f_dc_*` 恢复颜色。

最终实跑可见真实彩色点云，HUD 同时显示 artifact/viewer fidelity 均为
`dc-point-preview`，点数用 `points` 表述，控制台无运行时错误。内部视角仍稀疏且
轴向未验证，因此这里只证明真实数据可被如实消费，不构成画质、漫游或 human review
验收。

## Production 五门状态

| 门 | 本候选结果 |
|---|---|
| 权利明确的真实输入 | 通过当前外部诊断用途；正式产品素材仍需单独确认范围 |
| accepted real-photo SfM | **失败** |
| fresh non-mock CUDA 3DGS | 未执行 |
| 实测控制点与米制对齐 | 未执行；发布位置和未知轴向不能替代控制点 |
| 同一 scene 的真实 Viewer/human QA | 仅完成诊断 point-preview，未通过正式验收 |

因此 Production V1 的五个外部门仍未闭合。下一次高价值实跑应使用权利明确、密集
重叠的新采集，先通过 accepted SfM，再在固定云 GPU runtime 上训练 non-mock 3DGS，
随后完成独立控制点对齐和 receipt-bound Viewer/human QA。
