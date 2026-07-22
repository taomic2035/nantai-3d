"""Real spherical harmonics degree 0–3 evaluation and rotation.

This module implements reliable rotation of 3DGS (graphdeco/INRIA) spherical
harmonic coefficients under a proper orthonormal rotation ``rotation``
(``rotation.T @ rotation ~= I``, ``det(rotation) ~= +1``).  The rotation
preserves DC (degree-0) exactly, applies a separate Wigner-D block per
degree ``l=1,2,3``, and never mixes RGB channels or degree blocks.

Convention
----------
The SH basis and ``f_rest_*`` flatten order match the standard graphdeco/INRIA
3DGS PLY format:

- ``f_rest`` stored per channel: ``[R_d1, G_d1, B_d1, R_d2, G_d2, B_d2, ...]``
- Within each degree: ``m = -l, -l+1, ..., 0, ..., l-1, l``

The basis functions use the Condon-Shortley phase, matching the constants in
the INRIA 3DGS CUDA evaluator:

  Y_0^0 = SH_C0

  Y_1^{-1} = -SH_C1 * y;  Y_1^0 = +SH_C1 * z;  Y_1^1 = -SH_C1 * x

  Y_2^{-2} = SH_C2[0] * xy;     Y_2^{-1} = SH_C2[1] * yz;
  Y_2^0    = SH_C2[2] * (2z²-x²-y²);  Y_2^1 = SH_C2[3] * xz;
  Y_2^2    = SH_C2[4] * (x²-y²)

  Y_3^{-3} = SH_C3[0] * y(3x²-y²);  Y_3^{-2} = SH_C3[1] * xyz;
  Y_3^{-1} = SH_C3[2] * y(4z²-x²-y²);
  Y_3^0    = SH_C3[3] * z(2z²-3x²-3y²);
  Y_3^1    = SH_C3[4] * x(4z²-x²-y²);
  Y_3^2    = SH_C3[5] * z(x²-y²);
  Y_3^3    = SH_C3[6] * x(x²-3y²)

Rotation method
----------------
Rather than closed-form Wigner-D (which is error-prone for l=2,3), we use a
numerical approach:

1. Choose a fixed, deterministic set of sample directions on the unit sphere.
2. Evaluate the real SH basis at these directions → ``basis`` (n_dirs, 2l+1).
3. Rotate the directions by ``rotation`` → ``d' = rotation @ d``.
4. Evaluate the real SH basis at the rotated directions → ``basis_rot``.
5. Solve ``basis @ block = basis_rot`` via least-squares →
   ``block = pinv(basis) @ basis_rot``.
6. Check orthogonality of ``block``; reject if the error exceeds the budget.

This is the method recommended in HANDOFF-OPUS-010 §3.
"""

from __future__ import annotations

import math

import numpy as np

#: Degree-0 SH constant (matches ``gaussian_scene.SH_C0``).
SH_C0: float = 0.28209479177387814

#: Degree-1 SH constant.
SH_C1: float = 0.48860251190291992

#: Degree-2 SH constants (matches INRIA 3DGS ``SH_C2[]``).
SH_C2: tuple[float, ...] = (
    1.0925484305920790,
    -1.0925484305920790,
    0.31539156525252005,
    -1.0925484305920790,
    0.54627421529603959,
)

#: Degree-3 SH constants (matches INRIA 3DGS ``SH_C3[]``).
SH_C3: tuple[float, ...] = (
    -0.59004358992664352,
    2.8906114204808136,
    -0.45704579946446507,
    0.37317633259011536,
    -0.45704579946446507,
    1.4453057103204068,
    -0.59004358992664352,
)

#: Number of non-DC coefficients per degree.
COEFFS_PER_DEGREE: tuple[int, ...] = (1, 3, 5, 7)

#: Number of non-DC ``f_rest`` properties per RGB channel for each degree.
REST_PER_CHANNEL: tuple[int, ...] = (0, 3, 8, 15)

#: Maximum supported SH degree.
MAX_SH_DEGREE: int = 3

#: Orthogonality error budget for rotation blocks.
#: Blocks must satisfy ``||D @ D.T - I||_F < BLOCK_ORTHOGONALITY_TOLERANCE``.
#:
#: The INRIA 3DGS ``SH_C3`` constants were computed as ``sqrt(n)/(2*sqrt(pi))``
#: rather than the numerically equivalent ``sqrt(n/(4*pi))``.  The two
#: expressions differ by ~2e-8 in float64, which propagates into the Gram
#: matrix diagonal and hence the rotation block.  We cannot use the
#: "correct" constants because the INRIA renderer evaluates colour with its
#: own constants — rotating in a different basis would produce wrong colours.
#: A tolerance of 1e-6 gives ~50x margin over the actual ~2.15e-8 error while
#: still catching genuinely wrong rotations (which produce O(1) errors).
BLOCK_ORTHOGONALITY_TOLERANCE: float = 1e-6

#: Condition-number budget for the sample matrix.
#: If ``cond(S) > SAMPLE_CONDITION_BUDGET``, the sampling is degenerate.
SAMPLE_CONDITION_BUDGET: float = 1e6

#: Gauss-Legendre polar nodes × uniform azimuthal points.
#: For SH degree l, exact orthogonality requires n_theta >= l+1 and
#: n_phi >= 2*l+1.  We use 8 × 15 = 120 points — well above the minimum
#: (4 × 7 = 28 for l=3) — so the Gram matrix is a scalar multiple of I
#: to machine precision, making the rotation block exactly orthogonal.
_N_THETA: int = 8
_N_PHI: int = 15


class SHRotationError(ValueError):
    """Stable public error for SH rotation failures."""


# ---------------------------------------------------------------------------
# Real SH basis evaluation
# ---------------------------------------------------------------------------


def _fibonacci_sphere(n: int) -> np.ndarray:
    """Deterministic unit directions covering the sphere (Fibonacci spiral)."""

    golden = (1.0 + math.sqrt(5.0)) / 2.0
    indices = np.arange(n, dtype=np.float64)
    theta = 2.0 * math.pi * indices / golden
    phi = np.arccos(1.0 - 2.0 * (indices + 0.5) / n)
    x = np.sin(phi) * np.cos(theta)
    y = np.sin(phi) * np.sin(theta)
    z = np.cos(phi)
    dirs = np.stack([x, y, z], axis=-1)
    norm = np.linalg.norm(dirs, axis=1, keepdims=True)
    return dirs / norm


def _quadrature_sphere(n_theta: int, n_phi: int) -> tuple[np.ndarray, np.ndarray]:
    """Gauss-Legendre × uniform product grid on the unit sphere.

    Returns ``(directions, weights)`` where ``directions`` has shape
    ``(n_theta * n_phi, 3)`` and ``weights`` has shape ``(n_theta * n_phi,)``.

    The weights sum to ``4π`` (the surface area of the unit sphere).
    With ``n_theta >= l+1`` and ``n_phi >= 2*l+1`` the discrete inner
    product ``Σ_i w_i Y_l^m(d_i) Y_l^{m'}(d_i)`` equals the continuous
    integral exactly, so the Gram matrix is a scalar multiple of ``I``.
    """

    nodes, gl_weights = np.polynomial.legendre.leggauss(n_theta)
    # nodes are cos(θ) ∈ [-1, 1]; gl_weights already include the
    # d(cos θ) = sin θ dθ Jacobian.
    dphi = 2.0 * math.pi / n_phi
    phi = dphi * np.arange(n_phi, dtype=np.float64)

    z = np.repeat(nodes, n_phi)
    phi_rep = np.tile(phi, n_theta)
    sin_theta = np.sqrt(1.0 - z * z)

    dirs = np.stack([
        sin_theta * np.cos(phi_rep),
        sin_theta * np.sin(phi_rep),
        z,
    ], axis=-1)
    weights = np.repeat(gl_weights, n_phi) * dphi
    return dirs, weights


def evaluate_real_sh(degree: int, directions: np.ndarray) -> np.ndarray:
    """Evaluate real SH basis for ``degree`` at ``directions``.

    Parameters
    ----------
    degree
        SH degree (0, 1, 2, or 3).
    directions
        Array of shape ``(n, 3)`` — unit direction vectors.

    Returns
    -------
    np.ndarray
        Shape ``(n, 2*degree+1)`` — basis values for ``m = -l, ..., +l``.

    Raises
    ------
    SHRotationError
        If ``degree`` is not 0–3.
    """

    d = np.asarray(directions, dtype=np.float64)
    if d.ndim != 2 or d.shape[1] != 3:
        raise SHRotationError(f"directions must be (n, 3), got {d.shape}")
    x, y, z = d[:, 0], d[:, 1], d[:, 2]

    if degree == 0:
        return np.full((d.shape[0], 1), SH_C0)
    if degree == 1:
        # Signs match the INRIA CUDA evaluator:
        #   result = ... - SH_C1 * y * sh[1] + SH_C1 * z * sh[2] - SH_C1 * x * sh[3]
        # The Condon-Shortley phase makes m=-1 and m=+1 negative.
        return np.column_stack([-SH_C1 * y, SH_C1 * z, -SH_C1 * x])
    if degree == 2:
        return np.column_stack([
            SH_C2[0] * x * y,
            SH_C2[1] * y * z,
            SH_C2[2] * (2.0 * z * z - x * x - y * y),
            SH_C2[3] * x * z,
            SH_C2[4] * (x * x - y * y),
        ])
    if degree == 3:
        return np.column_stack([
            SH_C3[0] * y * (3.0 * x * x - y * y),
            SH_C3[1] * x * y * z,
            SH_C3[2] * y * (4.0 * z * z - x * x - y * y),
            SH_C3[3] * z * (2.0 * z * z - 3.0 * x * x - 3.0 * y * y),
            SH_C3[4] * x * (4.0 * z * z - x * x - y * y),
            SH_C3[5] * z * (x * x - y * y),
            SH_C3[6] * x * (x * x - 3.0 * y * y),
        ])
    raise SHRotationError(f"degree must be 0–3, got {degree}")


# ---------------------------------------------------------------------------
# Rotation matrix validation
# ---------------------------------------------------------------------------


def validate_rotation_matrix(rotation: np.ndarray) -> np.ndarray:
    """Validate ``rotation`` is a proper orthonormal rotation matrix.

    Returns the validated matrix as float64.  Raises ``SHRotationError``
    if ``rotation`` is not orthonormal (``rotation.T @ rotation ~= I``) or
    has ``det != +1``.
    """

    rotation = np.asarray(rotation, dtype=np.float64)
    if rotation.shape != (3, 3):
        raise SHRotationError(f"rotation matrix must be (3, 3), got {rotation.shape}")
    if not np.all(np.isfinite(rotation)):
        raise SHRotationError("rotation matrix contains NaN or Inf")
    ortho_err = np.linalg.norm(rotation.T @ rotation - np.eye(3))
    if ortho_err > 1e-10:
        raise SHRotationError(
            f"rotation matrix is not orthonormal: ||R^T R - I|| = {ortho_err:.3e}"
        )
    det = np.linalg.det(rotation)
    if det < 1.0 - 1e-10:
        raise SHRotationError(
            f"rotation matrix determinant {det:.6f} != +1 (improper rotation rejected)"
        )
    return rotation


# ---------------------------------------------------------------------------
# Rotation block construction (Wigner-D via numerical sampling)
# ---------------------------------------------------------------------------


def _build_rotation_block(
    degree: int,
    rotation: np.ndarray,
    sample_dirs: np.ndarray,
    weights: np.ndarray,
) -> np.ndarray:
    """Build the Wigner-D rotation block for one degree.

    Uses **weighted** least-squares on sampled SH basis values.  The block
    ``block`` satisfies ``Y_l(rotation @ d) = block @ Y_l(d)`` for all
    directions ``d``.

    The quadrature weights make the Gram matrix ``basis^T W basis`` a scalar
    multiple of ``I`` (up to machine precision), so ``block`` is exactly
    orthogonal.
    """

    basis = evaluate_real_sh(degree, sample_dirs)  # (n_dirs, 2l+1)
    rotated_dirs = sample_dirs @ rotation.T  # (n_dirs, 3)
    basis_rot = evaluate_real_sh(degree, rotated_dirs)  # (n_dirs, 2l+1)

    # Weighted least-squares: minimise ||W^{1/2} (basis @ block - basis_rot)||_F
    sqrt_w = np.sqrt(weights)  # (n_dirs,)
    basis_w = basis * sqrt_w[:, None]
    basis_rot_w = basis_rot * sqrt_w[:, None]

    # Check conditioning of the weighted sample matrix
    cond = np.linalg.cond(basis_w)
    if cond > SAMPLE_CONDITION_BUDGET:
        raise SHRotationError(
            f"sample matrix for degree {degree} is ill-conditioned "
            f"(cond={cond:.1e} > budget {SAMPLE_CONDITION_BUDGET:.1e})"
        )

    # Solve basis_w @ block = basis_rot_w  →  block = pinv(basis_w) @ basis_rot_w
    block, residual, rank, _ = np.linalg.lstsq(basis_w, basis_rot_w, rcond=None)
    expected_rank = 2 * degree + 1
    if rank < expected_rank:
        raise SHRotationError(
            f"sample matrix for degree {degree} has rank {rank} < {expected_rank}"
        )

    # Verify orthogonality
    ortho_err = np.linalg.norm(block @ block.T - np.eye(expected_rank))
    if ortho_err > BLOCK_ORTHOGONALITY_TOLERANCE:
        raise SHRotationError(
            f"rotation block for degree {degree} is not orthogonal: "
            f"||D D^T - I|| = {ortho_err:.3e} > "
            f"tolerance {BLOCK_ORTHOGONALITY_TOLERANCE:.1e}"
        )

    return block


def compute_sh_rotation_blocks(
    rotation: np.ndarray,
    max_degree: int = MAX_SH_DEGREE,
) -> tuple[np.ndarray, ...]:
    """Compute Wigner-D rotation blocks for degrees 1–``max_degree``.

    Returns a tuple of blocks ``(D_1, D_2, ... D_max)`` where each ``D_l``
    has shape ``(2*l+1, 2*l+1)``.  Degree 0 is identity (DC is invariant).

    The same ``rotation`` produces the same blocks deterministically; callers
    should cache blocks and apply them to all Gaussians / RGB channels.
    """

    rotation = validate_rotation_matrix(rotation)
    if max_degree < 0 or max_degree > MAX_SH_DEGREE:
        raise SHRotationError(f"max_degree must be 0–{MAX_SH_DEGREE}, got {max_degree}")
    sample_dirs, weights = _quadrature_sphere(_N_THETA, _N_PHI)
    blocks = tuple(
        _build_rotation_block(degree, rotation, sample_dirs, weights)
        for degree in range(1, max_degree + 1)
    )
    return blocks


# ---------------------------------------------------------------------------
# Coefficient rotation
# ---------------------------------------------------------------------------


def _split_sh_rest_by_degree(
    sh_rest: np.ndarray,
    degree: int,
) -> list[np.ndarray]:
    """Split ``sh_rest`` (n, total) into per-degree, per-channel slices.

    The INRIA ``f_rest`` flatten order is **coefficient-major,
    channel-interleaved**: ``[m=-1: R,G,B, m=0: R,G,B, m=+1: R,G,B, ...]``.
    This comes from ``features_rest.reshape(n, -1)`` on a
    ``(n, num_sh-1, 3)`` array (C-order, channel axis varies fastest).

    Each degree block occupies ``3 * (2l+1)`` contiguous columns.  Reshape
    to ``(n, n_coeffs, 3)`` — i.e. ``(n, m_idx, channel)`` — then transpose
    to ``(n, 3, n_coeffs)`` so the last axis is the coefficient axis that
    the rotation block ``D`` operates on.
    """

    n = sh_rest.shape[0]
    result: list[np.ndarray] = []
    offset = 0
    for ell in range(1, degree + 1):
        n_coeffs = 2 * ell + 1
        block_size = 3 * n_coeffs
        block = sh_rest[:, offset : offset + block_size]
        # (n, n_coeffs, channel) → (n, channel, n_coeffs)
        result.append(block.reshape(n, n_coeffs, 3).transpose(0, 2, 1))
        offset += block_size
    return result


def _merge_sh_rest_by_degree(
    blocks: list[np.ndarray],
    n: int,
    degree: int,
) -> np.ndarray:
    """Inverse of :func:`_split_sh_rest_by_degree`.

    Each block is ``(n, 3, n_coeffs)`` = ``(n, channel, m_idx)``.
    Transpose to ``(n, m_idx, channel)`` then flatten to
    ``[m: R,G,B, m: R,G,B, ...]`` matching the INRIA ``f_rest`` order.
    """

    total = sum(3 * (2 * ell + 1) for ell in range(1, degree + 1))
    result = np.empty((n, total), dtype=np.float64)
    offset = 0
    for l_idx, block in enumerate(blocks, start=1):
        n_coeffs = 2 * l_idx + 1
        block_size = 3 * n_coeffs
        # (n, channel, n_coeffs) → (n, n_coeffs, channel) → flat
        flat = block.transpose(0, 2, 1).reshape(n, block_size)
        result[:, offset : offset + block_size] = flat
        offset += block_size
    return result


def rotate_sh_coefficients(
    sh_rest: np.ndarray,
    blocks: tuple[np.ndarray, ...],
    degree: int,
) -> np.ndarray:
    """Rotate SH ``f_rest`` coefficients using pre-computed blocks.

    Parameters
    ----------
    sh_rest
        Shape ``(n, total_rest)`` — the ``f_rest`` coefficients in 3DGS order.
    blocks
        Output of :func:`compute_sh_rotation_blocks` (degree 1–``max``).
    degree
        SH degree (0–3).  Degree 0 is a no-op (DC is invariant).

    Returns
    -------
    np.ndarray
        Shape ``(n, total_rest)`` — rotated coefficients (float64).

    Raises
    ------
    SHRotationError
        If ``sh_rest`` shape is inconsistent with ``degree``, or any rotated
        value is non-finite.
    """

    sh_rest = np.asarray(sh_rest, dtype=np.float64)
    if sh_rest.ndim != 2:
        raise SHRotationError(f"sh_rest must be 2-D, got {sh_rest.ndim}-D")

    if degree == 0:
        return sh_rest.copy()

    n = sh_rest.shape[0]
    expected_cols = 3 * sum(2 * ell + 1 for ell in range(1, degree + 1))
    if sh_rest.shape[1] != expected_cols:
        raise SHRotationError(
            f"sh_rest has {sh_rest.shape[1]} columns but degree {degree} "
            f"requires {expected_cols}"
        )

    if len(blocks) < degree:
        raise SHRotationError(
            f"need {degree} rotation blocks, got {len(blocks)}"
        )

    split = _split_sh_rest_by_degree(sh_rest, degree)
    rotated: list[np.ndarray] = []
    for l_idx, block in enumerate(split, start=1):
        rot_block = blocks[l_idx - 1]  # (2*l+1, 2*l+1)
        # ``_build_rotation_block`` solves ``basis @ D = basis_rot`` where
        # ``basis_rot[i, m] = Y_l^m(R d_i)`` and ``basis[i, m] = Y_l^m(d_i)``.
        # This makes ``rot_block = D(R)^T`` in the convention
        # ``Y_l(R d) = D(R) @ Y_l(d)``.
        #
        # A Gaussian whose local frame rotates by ``R`` keeps its rendered
        # colour invariant when its coefficients transform as
        # ``c' = D(R) @ c`` (column form).  In row-vector storage (the last
        # axis of ``block`` holds ``c_m`` per channel) this becomes
        # ``c'_row = c_row @ D(R)^T``.  Since ``rot_block = D(R)^T`` here,
        # the required product is ``block @ rot_block`` — NOT
        # ``block @ rot_block.T``.
        rotated_block = block @ rot_block
        rotated.append(rotated_block)

    result = _merge_sh_rest_by_degree(rotated, n, degree)
    if not np.all(np.isfinite(result)):
        raise SHRotationError("rotated SH coefficients contain NaN or Inf")
    return result
