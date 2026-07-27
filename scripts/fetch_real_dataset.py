#!/usr/bin/env python3
"""Fetch or offline-verify a pinned real-dataset source."""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.real_dataset import (  # noqa: E402
    DatasetEvidenceError,
    HfDatasetSource,
    canonical_model_bytes,
    load_real_dataset_source,
)
from pipeline.real_dataset_fetch import (  # noqa: E402
    DatasetDownloadError,
    fetch_hf_dataset,
    verify_hf_dataset,
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch or verify a pinned Nantai real dataset"
    )
    parser.add_argument("source_json", type=Path)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()

    try:
        source = load_real_dataset_source(args.source_json)
        if not isinstance(source, HfDatasetSource):
            raise DatasetDownloadError("this command requires an hf-dataset source")
        if args.verify_only:
            receipt = verify_hf_dataset(source, args.workspace)
        else:
            receipt = fetch_hf_dataset(source, args.workspace)
        lock_bytes = (args.workspace / "dataset-lock.json").read_bytes()
        print(f"source_sha256={_sha256(canonical_model_bytes(source))}")
        print(f"lock_sha256={_sha256(lock_bytes)}")
        print(f"receipt_sha256={_sha256(canonical_model_bytes(receipt))}")
        return 0
    except (DatasetEvidenceError, DatasetDownloadError, OSError) as exc:
        print(f"dataset fetch failed: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
