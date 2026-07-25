"""Static argv-schema contract test for ``cloud/train_3dgs_nerfstudio.sh``.

This test does NOT run nerfstudio. It statically parses the cloud script and
asserts every CLI flag it constructs is registered in
``cloud/ns_train_argv_schema.py`` with a documentation source. When the cloud
script is edited to add or rename a flag without updating the schema, this
test goes RED.

Why this is not a real-CLI acceptance test
------------------------------------------
A real nerfstudio CLI acceptance test requires a cloud GPU instance
(HANDOFF-GLM-007 §1 item 3). This test only proves:

- the cloud script does not silently introduce an undocumented flag;
- the documented canonical spelling (tyro hyphen form) is preserved;
- the required flag set per invocation does not regress.

It does NOT prove:
- nerfstudio actually accepts the flags at runtime;
- flag compatibility across nerfstudio versions (0.3.x vs 1.0.x);
- runtime semantics of flag values (e.g. boolean bareword parsing).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from cloud.ns_train_argv_schema import (
    NERFSTUDIO_CLI_SCHEMA,
    REQUIRED_FLAGS_PER_INVOCATION,
)

_ROOT = Path(__file__).resolve().parent.parent
_CLOUD_SCRIPT = _ROOT / "cloud" / "train_3dgs_nerfstudio.sh"


# Tokens the script uses to invoke each nerfstudio CLI tool.
# We detect invocations by looking for these names as the first token of
# a command (after a line start, pipe, or `&&`).
_CLI_INVOCATIONS = ("ns-process-data", "ns-train", "ns-export", "ns-viewer")


def _extract_cli_invocations(script_text: str) -> list[tuple[str, list[str]]]:
    """Return a list of (cli_name, argv_tokens) for each invocation in the
    cloud script.

    This is a minimal parser: it walks lines, finds lines beginning (after
    whitespace) with one of _CLI_INVOCATIONS, and collects tokens until the
    line continuation ends. Multi-line continuations joined by `\\` are
    collapsed into a single logical line before tokenizing.
    """
    # Collapse line continuations: join any `\\n-` line ending in backslash
    # with the following line.
    joined = re.sub(r"\\\s*\n\s*", " ", script_text)
    invocations: list[tuple[str, list[str]]] = []
    for line in joined.splitlines():
        stripped = line.strip()
        # Skip comments and blank lines.
        if not stripped or stripped.startswith("#"):
            continue
        # Find the cli invocation at the start of the stripped line.
        for cli in _CLI_INVOCATIONS:
            if stripped == cli or stripped.startswith(cli + " "):
                # shlex split would be ideal but the cloud script uses bash
                # parameter expansion ($VAR); we only need flag names, which
                # start with --. Keep this simple and explicit.
                tokens = stripped.split()
                invocations.append((cli, tokens))
                break
    return invocations


def _flags_in_tokens(tokens: list[str]) -> set[str]:
    """Return the set of --flag tokens appearing in argv tokens.

    Flags of the form --machine.seed are kept whole. Flag values (the token
    after a flag) are excluded because they don't start with --.
    """
    return {t for t in tokens if t.startswith("--")}


# ---------------------------------------------------------------------
# Contract: every flag the cloud script uses is registered in the schema.
# ---------------------------------------------------------------------


def test_no_undeclared_flags_in_cloud_script() -> None:
    """Every --flag in cloud/train_3dgs_nerfstudio.sh must appear in
    NERFSTUDIO_CLI_SCHEMA with a documentation source.

    RED if someone adds a flag without registering it. This is the primary
    guard against silently introducing an undocumented nerfstudio flag.
    """
    script = _CLOUD_SCRIPT.read_text(encoding="utf-8")
    invocations = _extract_cli_invocations(script)
    assert invocations, "no CLI invocations parsed from cloud script"

    all_flags: set[str] = set()
    for _cli, tokens in invocations:
        all_flags |= _flags_in_tokens(tokens)

    # Flags we accept but don't require schema entries for (script-level
    # options consumed by the bash script itself, not by nerfstudio).
    # Keep this allowlist minimal; anything nerfstudio-facing must be in the schema.
    script_only_flags: set[str] = set()  # intentionally empty

    undeclared = all_flags - set(NERFSTUDIO_CLI_SCHEMA) - script_only_flags
    assert not undeclared, (
        "cloud script uses flags not declared in NERFSTUDIO_CLI_SCHEMA: "
        f"{sorted(undeclared)}. Add them to "
        "cloud/ns_train_argv_schema.py with a doc_source, or revert the "
        "cloud script change.")


# ---------------------------------------------------------------------
# Contract: required flags per CLI invocation are present.
# ---------------------------------------------------------------------


def test_required_flags_present_per_invocation() -> None:
    """Each ns-* invocation in the cloud script must pass at least the
    flags in REQUIRED_FLAGS_PER_INVOCATION for that CLI.

    RED if a required flag is dropped (e.g. someone removes --machine.seed
    from ns-train, breaking reproducibility).
    """
    script = _CLOUD_SCRIPT.read_text(encoding="utf-8")
    invocations = _extract_cli_invocations(script)

    by_cli: dict[str, set[str]] = {}
    for cli, tokens in invocations:
        by_cli.setdefault(cli, set()).update(_flags_in_tokens(tokens))

    for cli, required in REQUIRED_FLAGS_PER_INVOCATION.items():
        actual = by_cli.get(cli, set())
        missing = required - actual
        assert not missing, (
            f"{cli} invocation missing required flags: {sorted(missing)}. "
            "Restore them or update REQUIRED_FLAGS_PER_INVOCATION with a "
            "documented rationale.")


# ---------------------------------------------------------------------
# Contract: every schema entry has a non-empty doc_source.
# ---------------------------------------------------------------------


def test_every_schema_flag_has_doc_source() -> None:
    """No schema entry may have an empty doc_source. A flag without a
    documentation citation is an unverifiable claim and must not be
    registered."""
    for flag, _spec in NERFSTUDIO_CLI_SCHEMA.items():
        assert _spec["flag"] == flag, f"key {flag!r} mismatches spec.flag"
        assert _spec["doc_source"], f"{flag} has empty doc_source"
        assert _spec["cli"], f"{flag} has empty cli"
        assert _spec["value_type"], f"{flag} has empty value_type"


# ---------------------------------------------------------------------
# Contract: the canonical tyro hyphen spelling is preserved.
# ---------------------------------------------------------------------


def test_schema_uses_canonical_hyphen_spelling() -> None:
    """nerfstudio uses tyro, which accepts both --output-dir (hyphen) and
    --output_dir (underscore). The cloud script and schema must use the
    hyphen form to match official nerfstudio docs.

    RED if a schema entry uses the underscore form, which would silently
    diverge from the documented canonical form.
    """
    for flag, _spec in NERFSTUDIO_CLI_SCHEMA.items():
        assert "_" not in flag, (
            f"{flag} uses underscore form; nerfstudio canonical docs use "
            "the hyphen form (e.g. --output-dir, --max-num-iterations).")


# ---------------------------------------------------------------------
# Honest boundary: this test file must document its own limitation.
# ---------------------------------------------------------------------


def test_static_schema_contract_does_not_claim_real_cli_acceptance() -> None:
    """Guard against this test being misread as a real-CLI acceptance test.

    The module docstring must explicitly say it does NOT prove nerfstudio
    accepts these flags at runtime. If someone edits the docstring to drop
    that disclaimer, this test goes RED.
    """
    doc = __doc__ or ""
    assert "does NOT prove" in doc, (
        "module docstring must state it does NOT prove nerfstudio accepts "
        "the flags at runtime")
    assert "cloud GPU instance" in doc, (
        "module docstring must cite the cloud-GPU-instance requirement")


# ---------------------------------------------------------------------
# Sanity: the cloud script file exists and is non-empty.
# ---------------------------------------------------------------------


@pytest.mark.parametrize("cli", list(REQUIRED_FLAGS_PER_INVOCATION))
def test_cloud_script_invokes_each_required_cli(cli: str) -> None:
    """The cloud script must invoke each CLI listed in
    REQUIRED_FLAGS_PER_INVOCATION at least once. RED if a CLI is dropped
    (e.g. someone removes ns-export, breaking the export-to-PLY step)."""
    script = _CLOUD_SCRIPT.read_text(encoding="utf-8")
    invocations = _extract_cli_invocations(script)
    clis_used = {cli for cli, _ in invocations}
    assert cli in clis_used, (
        f"{cli} not invoked by cloud script; required by "
        "REQUIRED_FLAGS_PER_INVOCATION.")
