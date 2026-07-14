# FEEDBACK — HANDOFF-001

**验收结果: ✅ 全部通过 (11/11)**

## 逐项结果

| asset_id | 结果 | 问题 |
|---|---|---|
| house_wood_01 | PASS | — |
| house_wood_02 | PASS | — |
| house_stone_01 | PASS | — |
| house_thatch_01 | PASS | — |
| house_barn_01 | PASS | — |
| tree_pine_01 | PASS | — |
| tree_broadleaf_01 | PASS | — |
| tree_bamboo_01 | PASS | — |
| stone_wall_01 | PASS | — |
| stone_lamp_01 | PASS | — |
| fence_wood_01 | PASS | — |

## 后续动作

- 导入注册表: `python -m pipeline.validate_handoff handoff/deliverables/HANDOFF-001 --register`

## 人工备注（注册后复审）

### What

- 当前机器已完成 11/11 注册，全部 `origin=gpt-mock`；注册 PLY 与交付 PLY 的
  SHA-256 逐项一致。
- building 和 prop renderer 路径可实例化正式素材；vegetation 路径仍不消费 registry。

### Why

自动验收只证明素材格式合格；“已注册”不等于 11 类素材均已被默认世界消费，也不等于
fresh clone 能获得被 `.gitignore` 排除的 PLY。

### Tradeoff

保留 PLY 不进普通 Git可以控制仓库体积，但必须另有 LFS、制品下载或 deterministic
generator 的可复现入口。

### Open Questions

- vegetation cluster 的实例数量、LOD 与素材分发方案尚未确定。

### Next Action

注册动作已完成，不要重复执行本文件上方的自动建议；后续整改与回球要求见
`FEEDBACK-ARCH-P0-002.md`。
