import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_assets_import_does_not_require_errno_edeadlock() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import errno, importlib; "
                "hasattr(errno, 'EDEADLOCK') and delattr(errno, 'EDEADLOCK'); "
                "importlib.import_module('pipeline.assets')"
            ),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
