from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from production_release_fixtures import (  # noqa: E402
    modeled_artifact_records,
    modeled_entrypoints,
    modeled_payloads,
    modeled_public_evidence,
)

from pipeline.production_release_contract import (  # noqa: E402
    CHECKSUMS_NAME,
    PRODUCTION_RELEASE_NAME,
    build_production_receipt,
)
from pipeline.production_release_verifier import (  # noqa: E402
    verify_production_release_tree,
)
from pipeline.release_archive import canonical_json_bytes  # noqa: E402

FIXTURE_KIND = "modeled-contract-not-real-release"


def _write_modeled_tree(root: Path) -> None:
    root.mkdir()
    payloads = modeled_payloads()
    evidence = modeled_public_evidence()
    if evidence.get("fixture_kind") != FIXTURE_KIND:
        raise RuntimeError("modeled fixture label changed")
    for relative, (_role, payload) in payloads.items():
        destination = root.joinpath(*relative.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
    receipt = build_production_receipt(
        version="v1.0.0",
        source_commit="a" * 40,
        artifacts=modeled_artifact_records(),
        protected_roots=("web", "scripts", "pipeline", "evidence"),
        entrypoints=modeled_entrypoints(),
        public_evidence=evidence,
    )
    receipt_bytes = canonical_json_bytes(receipt)
    (root / PRODUCTION_RELEASE_NAME).write_bytes(receipt_bytes)
    checksum_rows = [
        f"{row['sha256']}  {row['path']}\n"
        for row in receipt["artifacts"]
    ]
    checksum_rows.append(
        f"{hashlib.sha256(receipt_bytes).hexdigest()}  "
        f"{PRODUCTION_RELEASE_NAME}\n"
    )
    (root / CHECKSUMS_NAME).write_bytes(
        "".join(sorted(checksum_rows)).encode("ascii")
    )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(
            "usage: probe_production_release_content_id.py OUTPUT",
            file=sys.stderr,
        )
        return 2
    output = Path(args[0])
    with tempfile.TemporaryDirectory(prefix="nantai-production-contract-") as raw:
        tree = Path(raw) / "tree"
        _write_modeled_tree(tree)
        report = verify_production_release_tree(tree)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(f"{report.package_content_id}\n".encode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
