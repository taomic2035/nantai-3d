# REVIEW-CODEX-015 — Production frame quality 实测分布

> 日期：2026-07-20
> Reviewer / runner：Codex
> 状态：Task 4 完成；Windows Blender runtime 已接通，候选 v2 baseline
> 有实测依据，但尚未接入正式 production runner

## 结论

锁定的 Blender 4.5.11 LTS 已对六台代表相机完成 fresh preflight：

- `camera-ground-route-010`：15 个 upper/middle `<2m` 命中，preflight 拒绝；
- `camera-ground-route-039`：5 个 upper/middle `<2m` 命中，preflight 拒绝；
- `011`、`025`、`026`、`034`：0 个命中，进入真实六层渲染。

四台实渲相机各自生成 RGB、depth、normal、instance、semantic、camera metadata
六份产物，共 24 份。逐字节 SHA-256 与 journal 中的 artifact 记录全部一致。

当前 runner 的旧门只有 `valid_pixel_ratio >= 0.75`，因此四帧在
`render-journal.json` 中均为 `rejected`。这不等于候选 v2 八规则也全部拒绝：

- 普通 controls `011`、`025`、`026` 在候选 v2 baseline 下全部通过；
- `034` 的 43,493 个 `<2m` 像素全部属于 instance `130`，
  `near-instance-dominance=1.000000 > 0.70`，因此被实测 v2 规则拒绝；
- 这替代了 `REVIEW-CODEX-014` 中指出的手填 `0.80`，现在 `034` 的拒绝来自
  实际 depth/normal/instance/semantic 字节。

候选 v2 baseline 可以进入 runner 集成，但在集成、内容绑定报告和 fresh
端到端复核完成前，**不能**据此把现有 journal 帧提升为 verified，也不能解锁
`req-5-pose-quality-fail-closed`。

## 运行身份

私有产物根：

```text
.nantai-studio/sv-prod-win/task4-controls-8bb3a75
```

| 身份 | SHA-256 / ID |
|---|---|
| Codex runtime 修复提交 | `8bb3a75e9437cff9394a8733f5f2ebf94c3c19e0` |
| build adapter | `windows-textured-v2` |
| build ID | `4f38ecf49ff8182e02c426df314dab90b91502673164330d3b704f234d02f1dc` |
| Blender executable | `0949e462f677c3e341913a838c6e2f54cc1c811ccb6f281ae9b3ff5926a2b255` |
| renderer script | `7bf916459d5d730d9b5568b27265a855cbc4fda1cce04301cda3f3159756139b` |
| `.blend` | `fa8cc4aabfe5049f2025e9d2ab34739c0914d87aa78a8fbda21ad86299cbebac` |
| build report | `aaf3a6b9fb6f48b3336e55f44f203504d58782a95a2738d70ee773464471e065` |
| render ID | `65c56cc686a5011df35a745acc5a540510ddc7961a8bbf47f694d8848ce56b3a` |
| final journal | `2b36f5c1dc353037e320ab1856603d83092c998a0029e6410b6492720bdf7167` |
| preflight request | `5ee4fa815b7d29e6823e9eaa59d8bda65abccdaf2eea04dd3b2f150469ada45f` |
| preflight report | `35cad8f3e87acb4b3322303cdf1d9c3b03ddab454dd78f5d9f4c58d8baa80d86` |
| raw layer audit JSON | `8f733e2f640151b65c83981a2b66c91d684d6dcbf14a386c4ff4eb4cdc666ec2` |

`raw layer audit JSON` 是私有文件
`layer-statistics-v2.json`，由锁定 Blender/OpenImageIO 重新解码已发布的
四层数值产物并调用 renderer 的 `_production_layer_counts` 得到。

## Request / report 身份

逐帧 request SHA 是从 journal 绑定的 immutable inputs 重建 canonical v3 request
后计算；逐帧 runtime report SHA 由 journal 记录，并由 journal statistics、
metadata 和六份 artifact 重新构造 canonical v2 report 后逐字节复算一致。

| camera | request SHA-256 | report content SHA-256 | runtime report SHA-256 | wall clock |
|---|---|---|---|---:|
| `011` | `2f70d41be6a22e6591bf6e5a0875dfb397fcaa0c30c23949c74da1d71368c1c4` | `b31f4eaa39d657dca13ae4ba2d6e63e297e4d4c5c6c1fb068e199431ef063a4f` | `5ff1beab7715c892cdac6eff6a967cecf021e59fe9c5becaf41130bce3d37865` | 14.609 s |
| `025` | `61e44c9f6b21e7b16ffe037c59b27ba39d67e8ddb67dc05100b3b33c53b3b9b7` | `354c586aec67e2cb7525e20e3e6e1b8ff0d435bf569bcfee94eb38fc779ca352` | `072ceb4778c8e8aa16fb25f14eb248e1852cdd8a105c2d88d0cdc8fdc7c4e58b` | 14.315 s |
| `026` | `317f9c8c6df460de7948070c1ceab94c452b257a17ee543eaf76d178052e1c16` | `6645a367c8519ee998e5a13013bef843c127519f37be3f8d2096f984fc2a6d2f` | `eede56c2a4ba840acedcffd41a38c1319525b8eb0ac4d44593124b22f8f5f74f` | 13.174 s |
| `034` | `b67b91ce1868b48c38b7260be72f2fe36947df6d082d222d2e10e10f63e153e9` | `cf767d0dcfa278956dfbafa3a4722c516a473c7d891a68b5c6deff5e8466f56f` | `16a5e95c31d648ed009908da3ef1612787de4247b9426cd957b9cdb6ccba38e5` | 13.861 s |

## v2 测量语义

以下语义必须随 policy 版本化，不能在函数体里隐含：

- depth-near：严格 `< 2.0m`；
- upper region：图像顶端的 rows `[0, 288)`，共 `294,912` 像素；
- ground semantic IDs：`(1,)`；
- sky/background semantic ID：`0`；
- valid depth denominator：全帧 `589,824` 像素；
- near-depth denominator：valid-depth pixels；
- near-instance dominance denominator：near-depth pixels；
- upper-instance / upper-ground denominator：upper-region pixels；
- ratio：按 6 位小数四舍五入；
- instance `0` 表示没有 canonical instance；terrain/creek/sky 仍可具有有效
  depth、normal、semantic，不能把 instance `0` 自动当作 invalid geometry。

## 原始整数统计

固定分母：`total_pixel_count=589,824`，
`upper_pixel_count=294,912`。

| camera | valid depth | valid normal | registered instance | valid semantic | sky | upper ground | near depth |
|---|---:|---:|---:|---:|---:|---:|---:|
| `011` | 349,101 | 349,101 | 185,586 | 349,101 | 240,723 | 975 | 0 |
| `025` | 426,863 | 426,863 | 370,487 | 426,863 | 162,961 | 0 | 0 |
| `026` | 359,799 | 359,799 | 177,719 | 359,799 | 230,025 | 21,858 | 0 |
| `034` | 406,487 | 406,487 | 319,103 | 406,487 | 183,337 | 7,833 | 43,493 |

| camera | dominant near instance / pixels | dominant upper instance / pixels |
|---|---|---|
| `011` | none / 0 | `6` / 19,364 |
| `025` | none / 0 | `30` / 55,726 |
| `026` | none / 0 | `47` / 32,441 |
| `034` | `130` / 43,493 | `130` / 87,345 |

## Ratio 分布

`registered-instance` 是诊断量，不是当前 v2 八规则中的通过门。

| ratio | `011` | `025` | `026` | `034` |
|---|---:|---:|---:|---:|
| valid depth | 0.591873 | 0.723712 | 0.610011 | 0.689167 |
| valid normal | 0.591873 | 0.723712 | 0.610011 | 0.689167 |
| registered instance | 0.314646 | 0.628131 | 0.301309 | 0.541014 |
| valid semantic | 0.591873 | 0.723712 | 0.610011 | 0.689167 |
| sky | 0.408127 | 0.276288 | 0.389989 | 0.310833 |
| upper ground | 0.003306 | 0.000000 | 0.074117 | 0.026560 |
| near depth | 0.000000 | 0.000000 | 0.000000 | 0.106997 |
| near instance dominance | 0.000000 | 0.000000 | 0.000000 | **1.000000** |
| upper instance dominance | 0.065660 | 0.188958 | 0.110002 | 0.296173 |

| ratio | min | median | max |
|---|---:|---:|---:|
| valid depth | 0.591873 | 0.649589 | 0.723712 |
| valid normal | 0.591873 | 0.649589 | 0.723712 |
| registered instance | 0.301309 | 0.427830 | 0.628131 |
| valid semantic | 0.591873 | 0.649589 | 0.723712 |
| sky | 0.276288 | 0.350411 | 0.408127 |
| upper ground | 0.000000 | 0.014933 | 0.074117 |
| near depth | 0.000000 | 0.000000 | 0.106997 |
| near instance dominance | 0.000000 | 0.000000 | 1.000000 |
| upper instance dominance | 0.065660 | 0.149480 | 0.296173 |

## Candidate baseline 判定

实测支持采纳当前 v2 candidate thresholds：

| rule | direction | threshold |
|---|---|---:|
| valid-depth-pixel-ratio | minimum | 0.30 |
| valid-normal-pixel-ratio | minimum | 0.30 |
| valid-semantic-pixel-ratio | minimum | 0.30 |
| sky-dominance | maximum | 0.55 |
| upper-ground-dominance | maximum | 0.30 |
| depth-near-concentration | maximum | 0.35 |
| near-instance-dominance | maximum | 0.70 |
| upper-instance-dominance | maximum | 0.70 |

| camera | statistics SHA-256 | v2 candidate verdict | evidence |
|---|---|---|---|
| `011` | `d81cd24fea842df8b76b9c7b84d0c6608fcd5ea01eebd7bc7c7adf2395161d0c` | pass | 八项均在阈值内 |
| `025` | `317899866eec90b710fe342747434761fb004f43d4ff162fa3e94c58d533bd89` | pass | 八项均在阈值内 |
| `026` | `743ed3a2012ec83741e6e07a8f86d00fef49b96c3a754324b33a25f184e7baaf` | pass | 八项均在阈值内 |
| `034` | `0cadb3c02d6b22b80907bc30025286316a2064dd24fc7eb79be1aa69529af68c` | reject | `near-instance-dominance=1.0 > 0.70` |

这些 threshold 只获得“可接入 runner 做下一轮验证”的批准，不获得
metric、measured geometry 或 training-suitable 信任提升。

## 六份 artifact SHA-256

### camera-ground-route-011

| kind | bytes | SHA-256 |
|---|---:|---|
| RGB | 736,137 | `32ea19e0783082f3c993f5985366b6890117263cbef3161f66e7579f7a790f6d` |
| depth | 1,210,208 | `6c1c6e0769e3e9bd9ce40292f492c16b89fb2003b8090fa0b5b5beddbc834670` |
| normal | 4,153,314 | `bb521c0f4568db12702c65aef09e7b8a7435237e00846242c8b7ba178b1cbacf` |
| instance | 6,838 | `a6409a6ac03fd25efcf553e62849df2077a21ac71c1cb0d061beb232b2a2504a` |
| semantic | 7,134 | `300fcf6a48bf53c989a14ee281b540e9b239c4ee68f6a8c3767b9f532137419a` |
| camera metadata | 5,353 | `e6f7d48b37031745ebd3f4ec2bcaa315ac90a6e3886f5a6b096fb5dc23827d23` |

### camera-ground-route-025

| kind | bytes | SHA-256 |
|---|---:|---|
| RGB | 814,996 | `2c0a3a6ece57946baa57f0c861f8da79eb2edcc8dfc1e5a4cfbb4c045138dd3e` |
| depth | 1,460,912 | `844becf4b47dcf3ccd853d93e9704b47dc0d7fbc5b563730f2c752ed5fc34a6c` |
| normal | 5,082,705 | `a5c9f645a78158d1dfeba1b09377c3fcf828d50fde58bc70022d24af099e7dda` |
| instance | 8,148 | `d32d56fbfc5bc6a8f91f1901475fe54dcc785143512ec5b9fc37bdba85d24f31` |
| semantic | 7,331 | `c011fd42b49a03900f8100db877c10151622b5206ff1ed756ea3c20688d8ec41` |
| camera metadata | 5,077 | `b177a836ba5b54087afa1a9f23808ce33f81abcf91f736c36ec92f4a9ed55698` |

### camera-ground-route-026

| kind | bytes | SHA-256 |
|---|---:|---|
| RGB | 809,997 | `4dd53b7e3e5bf97499adc2d313b5f450017d958a1b65d7322a2d5617aa95223f` |
| depth | 1,322,298 | `0c2ea1cee0373c28d7b215693a5e5e4122d42f6dc245df114cc3f48c94eb8759` |
| normal | 4,302,022 | `3b8252c3ae94bcfe2df3fe3b88823787c525cf829ad357097a99b516fe9b12bb` |
| instance | 7,250 | `f5918aee9372ea9166c4f18d1c82fd6e27bcb3fe91ec16e5c76bd42f6e0ba4c3` |
| semantic | 7,765 | `ff9e0e1f216fbc6b5471db0095d703948bdf6f1a380891531326bc55bcd255c4` |
| camera metadata | 5,342 | `07cbc65cc7e0d94bcf5ecc14c69b8f047d39f778b06195c16f3d16c623efa755` |

### camera-ground-route-034

| kind | bytes | SHA-256 |
|---|---:|---|
| RGB | 884,690 | `fef5ca88af55ea7e8acc5eb9cd450ce0c1074f11b8c4ded03e5dda2ef8e48c86` |
| depth | 1,474,897 | `22ec6c75c647c02190b36160830f1dbab467e04fff9412f9e597411e2f577325` |
| normal | 4,860,484 | `dd2ea520a1e5b500d3ba46a197dd4994c1d2c955d250ed98fbf4b99c09cd1237` |
| instance | 11,428 | `eaf908f28e461ca9862a7984eb14cfa7fe254ae743bc76106586d1d90488c940` |
| semantic | 10,771 | `c49d03fa2405d893ffbbd0362f7f04e63189304c5b4b8a0b143594d3e36242e1` |
| camera metadata | 5,345 | `a3cc06a7ce40b80b6b07abf10b4ee92733f675f53cd05ec78cf8a824399a5be9` |

## RGB 次级复核

- `011`：道路、民居、挡墙与植被可辨；没有单一近物遮满视野。
- `025`：中近景建筑和庭院清楚，树和小型绿化占比合理。
- `026`：路线向远处连续，桥/廊结构可见，但当前合成几何与材质仍明显简化。
- `034`：斜穿廊桥/木构件占据近景和上部视野；与 instance `130` 的
  `near-instance-dominance=1.0` 一致。

RGB 只用于解释数值，不能代替 depth/normal/instance/semantic 的机器证据。

## 已验证与仍未交付

已验证：

1. Windows v2 build adapter 能在 Blender 内部接受 authoritative
   `nv_build_id`，不会再被误判为 Mac local preview；
2. 失败 journal 可安全回到 `rendering`，不会携带旧 error/duration；
3. 六相机 preflight 与四相机六层实渲可实际执行；
4. 24 份 artifact 的 SHA/size 与 journal 一致；
5. raw v2 counts 来自已发布的真实帧字节；
6. 普通 controls 与 `034` 在 candidate baseline 下形成可区分分布。

仍未交付：

1. 正式 runner 仍只执行旧 `valid_pixel_ratio >= 0.75`，没有自动生成并绑定
   `ProductionFrameQualityRequestV2/ReportV2`；
2. frame request/report 文件当前为临时产物，运行结束后只在 journal 留下
   内容身份；Task 5 §3 runner 应持久化可复核的 canonical evidence；
3. 本轮是 130-instance build，不是待完成的 175-instance
   `EnvironmentModulePlan` 正式构建；
4. `010/039` 的 topology-aware replacement pose、fresh preflight、六层复渲、
   前后 RGB 比较仍属于 Task 5 §3；
5. 所有证据保持 `synthetic=true`、frame `verification_level=L0`、
   `trust_effect=none-quality-filter-only`。
