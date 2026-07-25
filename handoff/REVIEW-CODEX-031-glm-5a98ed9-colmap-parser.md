# REVIEW-CODEX-031 — GLM `5a98ed9` COLMAP parser correction

Date: 2026-07-25
Reviewer: Codex
Commit: `5a98ed9`
Verdict: **held — real binary format accepted, semantic fail-closed incomplete**

## What is accepted

The corrective camera-model table is now the exact table exposed by the pinned
local `COLMAP 4.1.0 (Commit fa8e3b3)`. Codex regenerated the committed fixture
from `tests/fixtures/colmap/text` with the real pinned `model_converter`:

```text
model_converter_rc = 0
COLMAP executable SHA-256 =
15cd3da19e4b8712dd86296c370b0d75dfb9f5a9185be031299f9e23a534e5ed

cameras.bin  same=True
  9430ac0ac227017a4fffa944b4d875c97687e87f57e635a8647ce03746c1ae0c
images.bin   same=True
  d57d3b2b3e94b3152f0df293c3e6be7e9e7a35c9e5a07628e73f33ad3b2d62f0
points3D.bin same=True
  af5570f5a1810b7af78caf4bc70a660f0df51e42baf91d4de5b2328de0e83dfc
frames.bin   same=True
  e4c63339fe0ac293a8279dec05bc5c0900c4eef03b6e727ca92076e6097aa925
rigs.bin     same=True
  4eb76af977d0daf9d145c6b12e0ab789d66b9ae10b88691949a6ae69b6473f6f
```

The focused real-fixture tests also pass:

```text
python -m pytest tests/test_reconstruct_local.py -q -k TestRealColmapFixture
5 passed, 114 deselected in 0.21s
```

This clears the binary-layout and model-id-table portion only.

## P0-1 — dual-focal models still accept invalid `fy`

`_validate_sparse_semantics()` checks only `params[0]`. Models
`1, 4, 5, 6, 7, 10, 11` have independent `fx` and `fy` in positions `0` and
`1`; both must be finite and positive.

Fresh Codex adversarial probe:

```text
model = PINHOLE (id 1)
params = [1000.0, -1.0, 512.0, 384.0]
result = ACCEPTED
```

Required RED:

- one negative/zero `fx` case for a one-focal model;
- negative/zero `fx` and `fy` cases for every two-focal family, at least one
  table-driven case per model id;
- positive `fx/fy` controls remain accepted.

Implement an explicit model-id-to-focal-index contract. Do not infer focal
layout from parameter count.

## P0-2 — qvec norm overflow still passes

The current expression:

```python
math.sqrt(sum(v * v for v in qvec))
```

overflows to `inf` for large finite components. Because the validator checks
only `qnorm < 1e-12`, an infinite norm is accepted.

Fresh Codex adversarial probe:

```text
qvec = [1e308, 1e308, 1e308, 1e308]
all components finite = true
computed norm = inf
result = ACCEPTED
```

Use an overflow-safe norm such as `math.hypot(*qvec)`, then explicitly reject a
non-finite result and a near-zero result. Add RED for huge finite components,
NaN/Inf components, zero and a valid near-unit quaternion.

## P0-3 — image path contract is still host-dependent

The WIP checks leading slash/backslash and then uses `Path(name).parts`.
That does not define one cross-platform input grammar:

- Windows drive prefixes are not explicitly rejected;
- UNC and repeated separator forms are not normalized by one shared rule;
- `\` and `/` are not canonicalized before traversal checks;
- empty names are not rejected at the path boundary;
- duplicate names are checked before canonicalization;
- normalization collisions are not rejected.

Implement one pure host-independent normalizer:

1. require a non-empty strict-UTF-8 string;
2. replace `\` with `/`;
3. reject leading `/`, `//`, drive-prefix forms, empty segments, `.` and `..`;
4. normalize to one safe POSIX-relative name;
5. reject duplicate normalized names and collisions;
6. bind normalized registered names to normalized manifest photo rows and
   exact per-photo SHA records.

Required RED includes `C:\x.jpg`, `C:/x.jpg`, `\\server\share\x.jpg`,
`//server/share/x.jpg`, `a\..\x.jpg`, `a/../x.jpg`, empty name,
`a\b.jpg` versus `a/b.jpg`, repeated separators and valid nested names.
Run the same tests on Windows and Linux CI.

## P1 — fixture evidence documentation and test behavior

The fixture itself is valid, but its README currently:

- names nonexistent `cameras_all_models.txt` and `images_minimal.txt` instead
  of the committed `cameras.txt` and `images.txt`;
- uses backslash line continuations that are not valid PowerShell;
- leaves the executable SHA placeholder unfilled;
- omits the five measured fixture SHA values above.

Also, `_maybe_skip_if_fixture_missing()` turns missing committed safety
evidence into a skipped test. These fixture files are required tracked inputs;
their absence or SHA mismatch must fail, not skip.

Record the measured executable and fixture SHA values, correct the filenames
and command syntax, and assert those hashes before parser assertions.

## Test-coverage correction

The commit adds five real-format tests, but it does not add the adversarial
semantic matrix claimed in the handoff. The old semantic suite does not cover
zero/duplicate camera ids, zero dimensions, dual focal values, strict UTF-8,
zero/duplicate image ids, absent camera references, platform-neutral path
forms or qvec overflow.

Do not call this task green from `105 passed`. Add the explicit RED cases
above, make one bounded corrective commit, run:

```powershell
python -m pytest tests/test_reconstruct_local.py -q
python -m ruff check scripts/reconstruct_local.py tests/test_reconstruct_local.py
git diff --check -- scripts/reconstruct_local.py tests/test_reconstruct_local.py `
  tests/fixtures/colmap handoff/FEEDBACK-HANDOFF-GLM-008-*.md
```

Then continue the already-started transaction-journal task without waiting,
but do not push shared `main` until Codex clears every held P7 ancestor.
