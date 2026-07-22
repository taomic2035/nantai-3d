"""TDD tests for degree 0–3 real spherical harmonics rotation.

Covers HANDOFF-OPUS-010 §4 acceptance:

1. Standard degree-3 PLY fixture flatten/reshape round-trip.
2. Identity rotation: all coefficients unchanged.
3. x/y/z axis 90° + arbitrary non-special axis.
4. Function-value invariance on >= 64 deterministic directions per degree.
5. Composition: rotate(rotation2, rotate(rotation1, c)) == rotate(rotation2 @ rotation1, c).
6. Inverse round-trip restores original coefficients.
7. DC unchanged, RGB channels don't cross-contaminate, degree blocks don't mix.
8. Improper / non-orthonormal / NaN rotation fail-closed.

Error budget: function-value ``atol <= 2e-10`` (float64 internal).  All error
budgets cite their source in-line.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from pipeline.spherical_harmonics import (
    BLOCK_ORTHOGONALITY_TOLERANCE,
    SH_C0,
    SH_C1,
    SH_C2,
    SH_C3,
    SHRotationError,
    _merge_sh_rest_by_degree,
    _split_sh_rest_by_degree,
    compute_sh_rotation_blocks,
    evaluate_real_sh,
    rotate_sh_coefficients,
    validate_rotation_matrix,
)

# Error budget: float64 internal precision for function-value invariance.
# Source: HANDOFF-OPUS-010 §4 — "函数值 atol <= 2e-10 (float64 内部)".
FUNC_ATOL: float = 2e-10

# Coefficient round-trip tolerance — tighter because it is pure float64
# arithmetic with no basis evaluation amplification.
COEFF_ATOL: float = 1e-12

# PLY float32 round-trip tolerance (used in gaussian_fidelity tests, not here).
# Source: float32 has ~7 significant digits; a conservative 1e-5 absolute
# budget covers typical 3DGS coefficient magnitudes.
_RNG = np.random.default_rng(seed=42)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _axis_angle_rotation(axis: np.ndarray, angle_rad: float) -> np.ndarray:
    """Proper orthonormal rotation matrix (Rodrigues' formula)."""
    axis = np.asarray(axis, dtype=np.float64)
    axis = axis / np.linalg.norm(axis)
    c = math.cos(angle_rad)
    s = math.sin(angle_rad)
    t = 1.0 - c
    x, y, z = axis
    return np.array([
        [t * x * x + c,     t * x * y - s * z, t * x * z + s * y],
        [t * x * y + s * z, t * y * y + c,     t * y * z - s * x],
        [t * x * z - s * y, t * y * z + s * x, t * z * z + c],
    ])


def _random_coeffs(n: int, degree: int) -> np.ndarray:
    """Random f_rest coefficients in INRIA flatten order, shape (n, total)."""
    total = 3 * sum(2 * ell + 1 for ell in range(1, degree + 1))
    return _RNG.standard_normal((n, total))


def _fibonacci_dirs(n: int) -> np.ndarray:
    """Deterministic unit directions (reuses the module's own spiral)."""
    golden = (1.0 + math.sqrt(5.0)) / 2.0
    indices = np.arange(n, dtype=np.float64)
    theta = 2.0 * math.pi * indices / golden
    phi = np.arccos(1.0 - 2.0 * (indices + 0.5) / n)
    dirs = np.stack([
        np.sin(phi) * np.cos(theta),
        np.sin(phi) * np.sin(theta),
        np.cos(phi),
    ], axis=-1)
    return dirs / np.linalg.norm(dirs, axis=1, keepdims=True)


def _evaluate_rest_color(
    sh_rest: np.ndarray,
    degree: int,
    directions: np.ndarray,
) -> np.ndarray:
    """Evaluate non-DC SH color for a single gaussian.

    ``sh_rest``: shape (total_rest,) — INRIA flatten order.
    Returns: shape (n_dirs, 3) — per-channel color contribution.
    """
    sh_rest_2d = sh_rest.reshape(1, -1)
    split = _split_sh_rest_by_degree(sh_rest_2d, degree)
    n_dirs = directions.shape[0]
    color = np.zeros((n_dirs, 3), dtype=np.float64)
    for l_idx, block in enumerate(split, start=1):
        basis = evaluate_real_sh(l_idx, directions)  # (n_dirs, 2l+1)
        coeffs = block[0]  # (3, 2l+1) — channel × coeff
        # color[:, ch] += sum_m basis[:, m] * coeffs[ch, m]
        color += basis @ coeffs.T  # (n_dirs, 2l+1) @ (2l+1, 3)
    return color


# ---------------------------------------------------------------------------
# 1. Flatten / reshape round-trip
# ---------------------------------------------------------------------------


def test_degree3_flatten_reshape_roundtrip_preserves_values() -> None:
    """Split + merge is a lossless inverse for degree-3 f_rest."""
    degree = 3
    # f_rest_k = k so we can verify exact positions.
    sh_rest = np.arange(45, dtype=np.float64).reshape(1, 45)
    split = _split_sh_rest_by_degree(sh_rest, degree)
    merged = _merge_sh_rest_by_degree(split, 1, degree)
    assert np.array_equal(merged, sh_rest)


def test_degree3_flatten_reshape_matches_inria_coefficient_channel_order() -> None:
    """The INRIA f_rest order is [m: RGB, m: RGB, ...] — coefficient-major.

    For degree 1, f_rest_0..8 map to:
      [m=-1: R,G,B, m=0: R,G,B, m=+1: R,G,B]

    So block[:, channel, m_idx] = m_idx * 3 + channel.
    """
    sh_rest = np.arange(9, dtype=np.float64).reshape(1, 9)
    split = _split_sh_rest_by_degree(sh_rest, degree=1)
    block = split[0]  # (1, 3, 3) — (n, channel, m_idx)
    for m_idx in range(3):
        for ch in range(3):
            expected = m_idx * 3 + ch
            assert block[0, ch, m_idx] == pytest.approx(expected), (
                f"block[channel={ch}, m_idx={m_idx}] = {block[0, ch, m_idx]}, "
                f"expected {expected}"
            )


def test_degree2_flatten_reshape_matches_inria_order() -> None:
    sh_rest = np.arange(24, dtype=np.float64).reshape(1, 24)  # 3*(3+5)
    split = _split_sh_rest_by_degree(sh_rest, degree=2)
    # degree 1 block: (1, 3, 3)
    d1 = split[0]
    for m_idx in range(3):
        for ch in range(3):
            assert d1[0, ch, m_idx] == pytest.approx(m_idx * 3 + ch)
    # degree 2 block: (1, 3, 5), offset 9
    d2 = split[1]
    for m_idx in range(5):
        for ch in range(3):
            expected = 9 + m_idx * 3 + ch
            assert d2[0, ch, m_idx] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# 2. Identity rotation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("degree", [0, 1, 2, 3])
def test_identity_rotation_leaves_coefficients_unchanged(degree: int) -> None:
    coeffs = _random_coeffs(n=4, degree=degree)
    if degree == 0:
        result = rotate_sh_coefficients(coeffs, (), degree)
        assert np.array_equal(result, coeffs)
        return
    blocks = compute_sh_rotation_blocks(np.eye(3), max_degree=degree)
    result = rotate_sh_coefficients(coeffs, blocks, degree)
    assert np.allclose(result, coeffs, atol=COEFF_ATOL)


# ---------------------------------------------------------------------------
# 3. Axis rotations (also covered by function-value invariance below)
# ---------------------------------------------------------------------------


def test_90deg_xyz_axis_rotations_produce_orthogonal_blocks() -> None:
    axes = [
        np.array([1.0, 0.0, 0.0]),
        np.array([0.0, 1.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
        np.array([1.0, 1.0, 1.0]),
    ]
    for axis in axes:
        rotation = _axis_angle_rotation(axis, math.pi / 2.0)
        blocks = compute_sh_rotation_blocks(rotation, max_degree=3)
        for l_idx, block in enumerate(blocks, start=1):
            size = 2 * l_idx + 1
            assert block.shape == (size, size)
            # Orthogonality: block @ block.T == I
            assert np.allclose(block @ block.T, np.eye(size), atol=BLOCK_ORTHOGONALITY_TOLERANCE)


# ---------------------------------------------------------------------------
# 4. Function-value invariance (trust root)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("degree", [1, 2, 3])
def test_function_value_invariance_64_directions(degree: int) -> None:
    """f_new(d) == f_old(rotation^T d) for >= 64 deterministic directions.

    This is the trust-root test: a Gaussian whose local frame rotates by
    rotation must keep its rendered color invariant.  f_new(d) uses the
    rotated coefficients at d; f_old(rotation^T d) uses the original
    coefficients at the pre-rotation view direction rotation^T d.
    """
    directions = _fibonacci_dirs(64)
    coeffs = _random_coeffs(n=1, degree=degree)
    # Non-trivial rotation: 37° about (1, 2, 3) — avoids special angles.
    rotation = _axis_angle_rotation(np.array([1.0, 2.0, 3.0]), math.radians(37.0))

    blocks = compute_sh_rotation_blocks(rotation, max_degree=degree)
    rotated = rotate_sh_coefficients(coeffs, blocks, degree)

    f_new = _evaluate_rest_color(rotated[0], degree, directions)
    # Original color at rotation^T @ d (pre-rotation view direction).
    old_dirs = directions @ rotation  # (d @ rotation^T)^T = rotation @ d^T^T... wait.
    # directions[i] is a row vector d.  rotation^T @ d = rotation^T applied to column d.
    # For row storage: (rotation^T @ d_col)^T = d_row @ rotation.
    old_dirs = directions @ rotation  # (64, 3) = d_row @ rotation = (rotation^T @ d_col)^T
    f_old = _evaluate_rest_color(coeffs[0], degree, old_dirs)

    assert np.allclose(f_new, f_old, atol=FUNC_ATOL), (
        f"degree {degree}: max function-value error = "
        f"{np.max(np.abs(f_new - f_old)):.3e}"
    )


@pytest.mark.parametrize(
    "axis",
    [
        np.array([1.0, 0.0, 0.0]),
        np.array([0.0, 1.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
    ],
)
def test_function_value_invariance_90deg_principal_axes(axis: np.ndarray) -> None:
    """90° rotations about principal axes — the most common real case."""
    degree = 3
    directions = _fibonacci_dirs(64)
    coeffs = _random_coeffs(n=1, degree=degree)
    rotation = _axis_angle_rotation(axis, math.pi / 2.0)

    blocks = compute_sh_rotation_blocks(rotation, max_degree=degree)
    rotated = rotate_sh_coefficients(coeffs, blocks, degree)

    f_new = _evaluate_rest_color(rotated[0], degree, directions)
    old_dirs = directions @ rotation
    f_old = _evaluate_rest_color(coeffs[0], degree, old_dirs)
    assert np.allclose(f_new, f_old, atol=FUNC_ATOL)


# ---------------------------------------------------------------------------
# 5. Composition: rotate(rotation2, rotate(rotation1, c)) == rotate(rotation2 @ rotation1, c)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("degree", [1, 2, 3])
def test_composition_law(degree: int) -> None:
    rotation1 = _axis_angle_rotation(np.array([1.0, 0.0, 0.0]), math.radians(30.0))
    rotation2 = _axis_angle_rotation(np.array([0.0, 1.0, 0.0]), math.radians(45.0))
    coeffs = _random_coeffs(n=2, degree=degree)

    blocks1 = compute_sh_rotation_blocks(rotation1, max_degree=degree)
    blocks2 = compute_sh_rotation_blocks(rotation2, max_degree=degree)
    blocks_comp = compute_sh_rotation_blocks(rotation2 @ rotation1, max_degree=degree)

    step1 = rotate_sh_coefficients(coeffs, blocks1, degree)
    step2 = rotate_sh_coefficients(step1, blocks2, degree)
    direct = rotate_sh_coefficients(coeffs, blocks_comp, degree)

    assert np.allclose(step2, direct, atol=FUNC_ATOL), (
        f"degree {degree}: max composition error = "
        f"{np.max(np.abs(step2 - direct)):.3e}"
    )


# ---------------------------------------------------------------------------
# 6. Inverse round-trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("degree", [1, 2, 3])
def test_inverse_rotation_restores_original_coefficients(degree: int) -> None:
    rotation = _axis_angle_rotation(np.array([1.0, 2.0, 3.0]), math.radians(37.0))
    coeffs = _random_coeffs(n=3, degree=degree)

    blocks_fwd = compute_sh_rotation_blocks(rotation, max_degree=degree)
    blocks_inv = compute_sh_rotation_blocks(rotation.T, max_degree=degree)

    rotated = rotate_sh_coefficients(coeffs, blocks_fwd, degree)
    restored = rotate_sh_coefficients(rotated, blocks_inv, degree)

    assert np.allclose(restored, coeffs, atol=FUNC_ATOL), (
        f"degree {degree}: max inverse error = "
        f"{np.max(np.abs(restored - coeffs)):.3e}"
    )


# ---------------------------------------------------------------------------
# 7. DC unchanged, RGB no cross-contamination, degree blocks don't mix
# ---------------------------------------------------------------------------


def test_dc_degree0_is_noop() -> None:
    """Degree-0 rotation returns coefficients unchanged (DC is invariant)."""
    coeffs = _RNG.standard_normal((2, 0))  # degree 0 has 0 f_rest columns
    result = rotate_sh_coefficients(coeffs, (), degree=0)
    assert np.array_equal(result, coeffs)


def test_rgb_channels_do_not_cross_contaminate() -> None:
    """Setting only R coefficients leaves G and B at zero after rotation."""
    degree = 3
    coeffs = np.zeros((1, 45), dtype=np.float64)
    # Set only R-channel coefficients: f_rest indices 0, 3, 6, 9, 12, ...
    # INRIA order: [m: R,G,B, m: R,G,B, ...] → R is at k % 3 == 0.
    for k in range(0, 45, 3):
        coeffs[0, k] = _RNG.standard_normal()

    rotation = _axis_angle_rotation(np.array([1.0, 1.0, 1.0]), math.radians(50.0))
    blocks = compute_sh_rotation_blocks(rotation, max_degree=degree)
    rotated = rotate_sh_coefficients(coeffs, blocks, degree)

    # Split and check G (k % 3 == 1) and B (k % 3 == 2) remain zero.
    for k in range(45):
        if k % 3 != 0:
            assert rotated[0, k] == pytest.approx(0.0, abs=COEFF_ATOL), (
                f"channel cross-contamination at f_rest[{k}]: {rotated[0, k]}"
            )


def test_degree_blocks_do_not_mix() -> None:
    """Setting only degree-1 coefficients leaves degree-2 and 3 unchanged."""
    degree = 3
    coeffs = np.zeros((1, 45), dtype=np.float64)
    # Degree 1 occupies f_rest[0:9], degree 2 [9:24], degree 3 [24:45].
    coeffs[0, 0:9] = _RNG.standard_normal(9)
    d2_before = coeffs[0, 9:24].copy()
    d3_before = coeffs[0, 24:45].copy()

    rotation = _axis_angle_rotation(np.array([2.0, 1.0, 3.0]), math.radians(65.0))
    blocks = compute_sh_rotation_blocks(rotation, max_degree=degree)
    rotated = rotate_sh_coefficients(coeffs, blocks, degree)

    assert np.allclose(rotated[0, 9:24], d2_before, atol=COEFF_ATOL), (
        "degree-2 block was contaminated by degree-1 rotation"
    )
    assert np.allclose(rotated[0, 24:45], d3_before, atol=COEFF_ATOL), (
        "degree-3 block was contaminated by degree-1 rotation"
    )


# ---------------------------------------------------------------------------
# 8. Fail-closed: improper / non-orthonormal / NaN rotation
# ---------------------------------------------------------------------------


def test_improper_rotation_det_negative_fails_closed() -> None:
    # Reflection through xy-plane: det = -1.
    rotation = np.diag([1.0, 1.0, -1.0])
    with pytest.raises(SHRotationError, match="determinant|improper"):
        compute_sh_rotation_blocks(rotation, max_degree=3)


def test_non_orthonormal_matrix_fails_closed() -> None:
    rotation = np.eye(3) + 0.01 * np.ones((3, 3))
    with pytest.raises(SHRotationError, match="orthonormal"):
        compute_sh_rotation_blocks(rotation, max_degree=1)


def test_nan_rotation_fails_closed() -> None:
    rotation = np.eye(3)
    rotation[0, 0] = float("nan")
    with pytest.raises(SHRotationError, match="NaN|Inf|finite"):
        compute_sh_rotation_blocks(rotation, max_degree=1)


def test_wrong_shape_rotation_fails_closed() -> None:
    rotation = np.eye(2)
    with pytest.raises(SHRotationError, match="3, 3|shape"):
        compute_sh_rotation_blocks(rotation, max_degree=1)


def test_validate_rotation_matrix_accepts_proper_rotation() -> None:
    rotation = _axis_angle_rotation(np.array([1.0, 2.0, 3.0]), math.radians(37.0))
    validated = validate_rotation_matrix(rotation)
    assert np.allclose(validated, rotation)


def test_rotate_sh_coefficients_rejects_wrong_column_count() -> None:
    blocks = compute_sh_rotation_blocks(np.eye(3), max_degree=3)
    bad_coeffs = np.zeros((1, 10), dtype=np.float64)  # degree 3 needs 45
    with pytest.raises(SHRotationError, match="columns|requires"):
        rotate_sh_coefficients(bad_coeffs, blocks, degree=3)


def test_rotate_sh_coefficients_rejects_insufficient_blocks() -> None:
    blocks = compute_sh_rotation_blocks(np.eye(3), max_degree=1)
    coeffs = _random_coeffs(n=1, degree=3)
    with pytest.raises(SHRotationError, match="blocks"):
        rotate_sh_coefficients(coeffs, blocks, degree=3)


def test_rotate_sh_coefficients_rejects_non_finite_output() -> None:
    """If rotation somehow produces non-finite output, it fails closed."""
    degree = 1
    coeffs = np.array([[float("inf"), 0.0, 0.0, 0.0, 0.0, 0.0,
                        0.0, 0.0, 0.0]], dtype=np.float64)
    rotation = _axis_angle_rotation(np.array([1.0, 0.0, 0.0]), math.radians(30.0))
    blocks = compute_sh_rotation_blocks(rotation, max_degree=degree)
    with pytest.raises(SHRotationError, match="NaN|Inf|finite"):
        rotate_sh_coefficients(coeffs, blocks, degree)


# ---------------------------------------------------------------------------
# Bonus: SH constants match INRIA CUDA evaluator
# ---------------------------------------------------------------------------


def test_sh_constants_match_inria_cuda() -> None:
    """Verify the SH constants against the INRIA CUDA forward.cu values."""
    assert SH_C0 == pytest.approx(0.28209479177387814)
    assert SH_C1 == pytest.approx(0.48860251190291992)
    assert SH_C2 == (
        pytest.approx(1.0925484305920790),
        pytest.approx(-1.0925484305920790),
        pytest.approx(0.31539156525252005),
        pytest.approx(-1.0925484305920790),
        pytest.approx(0.54627421529603959),
    )
    assert SH_C3 == (
        pytest.approx(-0.59004358992664352),
        pytest.approx(2.8906114204808136),
        pytest.approx(-0.45704579946446507),
        pytest.approx(0.37317633259011536),
        pytest.approx(-0.45704579946446507),
        pytest.approx(1.4453057103204068),
        pytest.approx(-0.59004358992664352),
    )


def test_degree1_basis_signs_match_cuda_neg_y_pos_z_neg_x() -> None:
    """The CUDA evaluator uses -SH_C1*y, +SH_C1*z, -SH_C1*x for degree 1."""
    dirs = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    basis = evaluate_real_sh(1, dirs)
    # m=-1 (col 0): -SH_C1 * y → [0, -SH_C1, 0]
    assert basis[0, 0] == pytest.approx(0.0, abs=1e-15)
    assert basis[1, 0] == pytest.approx(-SH_C1)
    assert basis[2, 0] == pytest.approx(0.0, abs=1e-15)
    # m=0 (col 1): +SH_C1 * z → [0, 0, SH_C1]
    assert basis[0, 1] == pytest.approx(0.0, abs=1e-15)
    assert basis[1, 1] == pytest.approx(0.0, abs=1e-15)
    assert basis[2, 1] == pytest.approx(SH_C1)
    # m=+1 (col 2): -SH_C1 * x → [-SH_C1, 0, 0]
    assert basis[0, 2] == pytest.approx(-SH_C1)
    assert basis[1, 2] == pytest.approx(0.0, abs=1e-15)
    assert basis[2, 2] == pytest.approx(0.0, abs=1e-15)


def test_block_orthogonality_tolerance_is_strict() -> None:
    """The orthogonality budget must be tight enough to catch real errors.

    The INRIA SH_C3 constants have ~2e-8 float64 precision loss, so the
    tolerance is 1e-6 (50x margin).  It must still be tight enough to reject
    genuinely wrong rotations, which produce O(1) orthogonality errors.
    """
    assert BLOCK_ORTHOGONALITY_TOLERANCE <= 1e-4
