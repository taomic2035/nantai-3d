"""nerfstudio CLI argv schema contract for ``cloud/train_3dgs_nerfstudio.sh``.

Why this exists
---------------
``tests/test_p1_canary_e2e.py::TestP1CanaryStubArgv`` proves the cloud script's
ns-train argv agrees with the request intent (P0-2 closure in ``e587a23``),
but it uses a **stub** ns-train. A stub cannot prove that the **real**
nerfstudio CLI accepts these flags — that requires a cloud GPU instance
(HANDOFF-GLM-007 §1, item 3).

This module is the next-best credential-free guarantee: a **static schema
contract** that pins every flag the cloud script constructs against the
nerfstudio CLI schema as documented in official docs / source. When the
cloud script adds or renames a flag, ``TestCloudNerfstudioArgvSchema`` goes
RED until either (a) the flag is added to this schema with a documentation
reference, or (b) the script change is reverted.

Honest boundary (must remain in docstring)
------------------------------------------
This schema is **static documentation**, not a real-CLI acceptance test.

- It does NOT prove nerfstudio actually accepts these flags at runtime.
- It does NOT prove flag compatibility across nerfstudio versions
  (0.3.x vs 1.0.x may rename or deprecate flags).
- It DOES catch the failure mode where someone edits the cloud script and
  silently introduces an undocumented flag, breaks the canonical flag
  spelling, or drops a flag the request intent claims to pass.

Sources for each flag are inline below. Every entry must cite an official
nerfstudio doc, source file, or reproducible community reference.
"""
from __future__ import annotations

from typing import TypedDict


class FlagSpec(TypedDict):
    """One CLI flag used by the cloud script, pinned to its nerfstudio schema.

    ``present_in_cloud_script`` is filled at runtime by the test, not here.
    The fields below are the contract; the test asserts the cloud script
    only uses flags declared here.
    """

    flag: str
    """Canonical flag spelling (with leading --, hyphen form, tyro default)."""

    cli: str
    """ns-train / ns-process-data / ns-export / ns-viewer."""

    subcommand: str | None
    """Subcommand (e.g. splatfacto, gaussian-splat, images, video, pointcloud)."""

    value_type: str
    """int / str / bool / path / enum, as documented."""

    doc_source: str
    """Where the flag's existence and spelling is documented."""

    notes: str
    """Version sensitivity, deprecation warnings, or caveats."""


# Every flag the cloud script is allowed to construct, keyed by flag name.
# Adding a new flag to the cloud script without registering it here fails
# TestCloudNerfstudioArgvSchema::test_no_undeclared_flags_in_cloud_script.
NERFSTUDIO_CLI_SCHEMA: dict[str, FlagSpec] = {
    # ---- ns-process-data (data preprocessing, runs COLMAP internally) ----
    "--data": {
        "flag": "--data",
        "cli": "ns-process-data",
        "subcommand": "images|video",
        "value_type": "path",
        "doc_source": (
            "nerfstudio CLI reference; ns-process-data images|video --data "
            "<path> --output-dir <path> is the canonical form in every "
            "documented tutorial (CSDN 136658887, 147613223) and the "
            "nerfstudio first_dataset tutorial."
        ),
        "notes": "Also used by ns-train and ns-export with the same spelling.",
    },
    "--output-dir": {
        "flag": "--output-dir",
        "cli": "ns-process-data|ns-train|ns-export",
        "subcommand": "images|video|splatfacto|gaussian-splat",
        "value_type": "path",
        "doc_source": (
            "nerfstudio tyro dataclass field `output_dir`; tyro accepts both "
            "--output-dir (hyphen) and --output_dir (underscore). The cloud "
            "script uses the hyphen form, matching official docs and the "
            "nerfstudio source `configs/base_config.py`."
        ),
        "notes": "Hyphen form is canonical per tyro default behavior.",
    },
    # ---- ns-train splatfacto ----
    "--max-num-iterations": {
        "flag": "--max-num-iterations",
        "cli": "ns-train",
        "subcommand": "splatfacto|nerfacto",
        "value_type": "int",
        "doc_source": (
            "nerfstudio TrainerConfig.max_num_iterations; exposed as a "
            "top-level ns-train flag in every documented tutorial "
            "(CSDN 136658887, 147613223, 130307992)."
        ),
        "notes": "Top-level flag, not --trainer.max-num-iterations.",
    },
    "--machine.seed": {
        "flag": "--machine.seed",
        "cli": "ns-train",
        "subcommand": "splatfacto|nerfacto",
        "value_type": "int",
        "doc_source": (
            "nerfstudio MachineConfig.seed field "
            "(nerfstudio/configs/machine_config.py); exposed as "
            "--machine.seed via tyro dotted-path. Confirmed by community "
            "config dumps (CSDN SDFStudio 128942521: "
            "MachineConfig(seed=42, num_gpus=1, ...))."
        ),
        "notes": "Dotted-path form required; --seed is not a top-level flag.",
    },
    "--viewer.quit-on-train-completion": {
        "flag": "--viewer.quit-on-train-completion",
        "cli": "ns-train",
        "subcommand": "splatfacto|nerfacto",
        "value_type": "bool (True/False as bareword)",
        "doc_source": (
            "nerfstudio ViewerConfig.quit_on_train_completion; exposed as "
            "--viewer.quit-on-train-completion via tyro. Used in RTX 50 "
            "Windows guides (CSDN 155609552) and nerfstudio time-optimization "
            "articles (CSDN 151258638)."
        ),
        "notes": "Boolean must be passed as bareword True/False, not --flag.",
    },
    "--orientation-method": {
        "flag": "--orientation-method",
        "cli": "ns-train",
        "subcommand": "nerfstudio-data",
        "value_type": "enum",
        "doc_source": (
            "Nerfstudio 1.1.5 source "
            "nerfstudio/data/dataparsers/nerfstudio_dataparser.py; "
            "NerfstudioDataParserConfig.orientation_method."
        ),
        "notes": "Production prepared data requires the literal value none.",
    },
    "--center-method": {
        "flag": "--center-method",
        "cli": "ns-train",
        "subcommand": "nerfstudio-data",
        "value_type": "enum",
        "doc_source": (
            "Nerfstudio 1.1.5 source "
            "nerfstudio/data/dataparsers/nerfstudio_dataparser.py; "
            "NerfstudioDataParserConfig.center_method."
        ),
        "notes": "Production prepared data requires the literal value none.",
    },
    "--auto-scale-poses": {
        "flag": "--auto-scale-poses",
        "cli": "ns-train",
        "subcommand": "nerfstudio-data",
        "value_type": "bool (True/False as bareword)",
        "doc_source": (
            "Nerfstudio 1.1.5 source "
            "nerfstudio/data/dataparsers/nerfstudio_dataparser.py; "
            "NerfstudioDataParserConfig.auto_scale_poses."
        ),
        "notes": "Production prepared data requires False.",
    },
    "--scale-factor": {
        "flag": "--scale-factor",
        "cli": "ns-train",
        "subcommand": "nerfstudio-data",
        "value_type": "float",
        "doc_source": (
            "Nerfstudio 1.1.5 source "
            "nerfstudio/data/dataparsers/nerfstudio_dataparser.py; "
            "NerfstudioDataParserConfig.scale_factor."
        ),
        "notes": "Production prepared data requires exactly 1.0.",
    },
    # ---- ns-export gaussian-splat ----
    "--load-config": {
        "flag": "--load-config",
        "cli": "ns-export|ns-viewer|ns-render",
        "subcommand": "gaussian-splat|pointcloud|video|images",
        "value_type": "path",
        "doc_source": (
            "nerfstudio ns-export reference; --load-config <config.yml> "
            "selects the trained checkpoint to export. Canonical form in "
            "every documented tutorial (CSDN 149749918, 151267549, 151259079)."
        ),
        "notes": "Path to the trainer-generated config.yml under outputs/.",
    },
    "--output-filename": {
        "flag": "--output-filename",
        "cli": "ns-export",
        "subcommand": "gaussian-splat",
        "value_type": "str",
        "doc_source": (
            "Nerfstudio 1.1.5 source nerfstudio/scripts/exporter.py; "
            "ExportGaussianSplat.output_filename."
        ),
        "notes": (
            "The 1.1.5 default is splat.ply; production explicitly requests "
            "point_cloud.ply."
        ),
    },
}


# Minimum required flags per CLI invocation the cloud script makes.
# The cloud script must pass every flag listed here for the corresponding
# CLI; dropping one fails TestCloudNerfstudioArgvSchema::test_required_flags_present.
REQUIRED_FLAGS_PER_INVOCATION: dict[str, set[str]] = {
    "ns-process-data": {"--data", "--output-dir"},
    "ns-train": {
        "--data",
        "--output-dir",
        "--max-num-iterations",
        "--machine.seed",
        "--orientation-method",
        "--center-method",
        "--auto-scale-poses",
        "--scale-factor",
    },
    "ns-export": {
        "--load-config",
        "--output-dir",
        "--output-filename",
    },
}
