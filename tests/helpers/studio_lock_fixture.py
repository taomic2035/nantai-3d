"""Child process used by cross-process Studio file-lock tests."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from pipeline.studio_jobs import ProjectFileLock


def main() -> int:
    lock = ProjectFileLock(Path(sys.argv[1]), role=sys.argv[2])
    if not lock.acquire(blocking=False):
        print("contended", flush=True)
        return 2
    print("acquired", flush=True)
    try:
        sys.stdin.buffer.read(1)
    finally:
        lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
