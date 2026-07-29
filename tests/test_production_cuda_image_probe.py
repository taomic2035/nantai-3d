from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from cloud import probe_production_cuda_image as probe_module
from pipeline.production_cuda_image_release import (
    canonical_production_cuda_image_probe_bytes,
    load_production_cuda_image_probe_bytes,
)
from pipeline.production_cuda_runtime_lock import (
    LockedAuxiliaryFile,
    LockedBaseImage,
    LockedSourceArtifact,
    ProductionCudaRuntimeLock,
    canonical_production_cuda_runtime_lock_bytes,
)
from pipeline.production_runtime_evidence import (
    training_cli_schema_sha256,
)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _runtime_lock(
    *,
    required_imports: tuple[str, ...] = (
        "gsplat",
        "nerfstudio",
        "torch",
        "torchvision",
    ),
) -> ProductionCudaRuntimeLock:
    return ProductionCudaRuntimeLock.create(
        platform="linux/amd64",
        ubuntu_snapshot="20260701T000000Z",
        cuda_architectures=("7.5", "8.0", "8.6", "8.9", "9.0+PTX"),
        base_images=(
            LockedBaseImage(
                role="builder",
                identity=(
                    "nvidia/cuda:11.8.0-devel-ubuntu22.04@sha256:"
                    f"{_digest('builder-image')}"
                ),
                platform="linux/amd64",
                platform_manifest_digest=(
                    f"sha256:{_digest('builder-manifest')}"
                ),
            ),
            LockedBaseImage(
                role="runtime",
                identity=(
                    "nvidia/cuda:11.8.0-runtime-ubuntu22.04@sha256:"
                    f"{_digest('runtime-image')}"
                ),
                platform="linux/amd64",
                platform_manifest_digest=(
                    f"sha256:{_digest('runtime-manifest')}"
                ),
            ),
        ),
        source_artifacts=(
            LockedSourceArtifact(
                role="cpython-source",
                version="3.11.9",
                filename="Python-3.11.9.tar.xz",
                url=(
                    "https://www.python.org/ftp/python/3.11.9/"
                    "Python-3.11.9.tar.xz"
                ),
                byte_length=1,
                sha256=_digest("cpython-source"),
            ),
            LockedSourceArtifact(
                role="gsplat-sdist",
                version="1.4.0",
                filename="gsplat-1.4.0.tar.gz",
                url=(
                    "https://files.pythonhosted.org/packages/source/g/"
                    "gsplat/gsplat-1.4.0.tar.gz"
                ),
                byte_length=1,
                sha256=_digest("gsplat-sdist"),
                upstream_commit=(
                    "4d3a3b69db4de0326f983ccf7b7b255271a17b01"
                ),
            ),
            LockedSourceArtifact(
                role="nerfstudio-wheel",
                version="1.1.5",
                filename="nerfstudio-1.1.5-py3-none-any.whl",
                url=(
                    "https://files.pythonhosted.org/packages/py3/n/"
                    "nerfstudio/nerfstudio-1.1.5-py3-none-any.whl"
                ),
                byte_length=1,
                sha256=_digest("nerfstudio-wheel"),
                upstream_commit=(
                    "6b60855003011b2ca23c2fe3f8e2ca6314c69924"
                ),
            ),
            LockedSourceArtifact(
                role="torch-wheel",
                version="2.1.2+cu118",
                filename=(
                    "torch-2.1.2+cu118-cp311-cp311-linux_x86_64.whl"
                ),
                url=(
                    "https://download-r2.pytorch.org/whl/cu118/"
                    "torch-2.1.2%2Bcu118-cp311-cp311-linux_x86_64.whl"
                ),
                byte_length=1,
                sha256=_digest("torch-wheel"),
            ),
            LockedSourceArtifact(
                role="torchvision-wheel",
                version="0.16.2+cu118",
                filename=(
                    "torchvision-0.16.2+cu118-cp311-cp311-linux_x86_64.whl"
                ),
                url=(
                    "https://download-r2.pytorch.org/whl/cu118/"
                    "torchvision-0.16.2%2Bcu118-cp311-cp311-linux_x86_64.whl"
                ),
                byte_length=1,
                sha256=_digest("torchvision-wheel"),
            ),
        ),
        auxiliary_files=(
            LockedAuxiliaryFile(
                role="apt-build-lock",
                path="containers/production-cuda/apt-build.lock",
                byte_length=1,
                sha256=_digest("apt-build"),
            ),
            LockedAuxiliaryFile(
                role="apt-runtime-lock",
                path="containers/production-cuda/apt-runtime.lock",
                byte_length=1,
                sha256=_digest("apt-runtime"),
            ),
            LockedAuxiliaryFile(
                role="python-requirements-lock",
                path="containers/production-cuda/requirements.lock",
                byte_length=1,
                sha256=_digest("requirements"),
            ),
        ),
        required_imports=required_imports,
    )


def _help_output() -> bytes:
    return (
        b"usage: ns-train splatfacto [OPTIONS]\n"
        b"  --data PATH\n"
        b"  --help\n"
        b"  --machine.seed INT\n"
        b"  --max-num-iterations INT\n"
        b"  --output-dir PATH\n"
        b"  --viewer.quit-on-train-completion BOOL\n"
    )


def _version_output(**overrides: object) -> bytes:
    values = {
        "python_version": "3.11.9",
        "torch_version": "2.1.2+cu118",
        "torch_cuda_version": "11.8",
        "torchvision_version": "0.16.2+cu118",
        **overrides,
    }
    return (
        json.dumps(values, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("ascii")


@dataclass
class ProbeFixture:
    lock_path: Path
    executables: dict[str, Path]
    which_calls: list[str]
    run_calls: list[tuple[str, ...]]
    import_calls: list[str]
    package_calls: list[str]
    version_stdout: bytes = _version_output()
    help_stdout: bytes = _help_output()

    def which(self, name: str) -> str | None:
        self.which_calls.append(name)
        path = self.executables.get(name)
        return None if path is None else str(path)

    def run(self, argv, **kwargs):
        command = tuple(str(item) for item in argv)
        self.run_calls.append(command)
        assert kwargs["capture_output"] is True
        assert kwargs["check"] is False
        assert 0 < kwargs["timeout"] <= 30
        if "-c" in command:
            return SimpleNamespace(
                returncode=0,
                stdout=self.version_stdout,
                stderr=b"",
            )
        return SimpleNamespace(
            returncode=0,
            stdout=self.help_stdout,
            stderr=b"",
        )

    def package_version(self, name: str) -> str:
        self.package_calls.append(name)
        return {"nerfstudio": "1.1.5", "gsplat": "1.4.0"}[name]

    def import_module(self, name: str) -> object:
        self.import_calls.append(name)
        return object()


def _probe_fixture(tmp_path: Path) -> ProbeFixture:
    lock_path = tmp_path / "runtime-lock.json"
    lock_path.write_bytes(
        canonical_production_cuda_runtime_lock_bytes(_runtime_lock())
    )
    executables: dict[str, Path] = {}
    for name in ("python", "ns-train", "ns-export"):
        path = tmp_path / name
        path.write_bytes(f"{name}\n".encode("ascii"))
        path.chmod(0o755)
        executables[name] = path
    return ProbeFixture(
        lock_path=lock_path,
        executables=executables,
        which_calls=[],
        run_calls=[],
        import_calls=[],
        package_calls=[],
    )


@pytest.fixture(autouse=True)
def _linux_amd64(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        probe_module,
        "_runtime_platform",
        lambda: "linux/amd64",
    )


def _collect(fixture: ProbeFixture):
    return probe_module.collect_image_probe(
        fixture.lock_path,
        which=fixture.which,
        run_command=fixture.run,
        package_version=fixture.package_version,
        module_importer=fixture.import_module,
    )


def test_probe_derives_versions_executables_and_cli_schema(
    tmp_path: Path,
) -> None:
    fixture = _probe_fixture(tmp_path)

    probe = _collect(fixture)

    assert probe.python_version == "3.11.9"
    assert probe.torch_version == "2.1.2+cu118"
    assert probe.torch_cuda_version == "11.8"
    assert probe.torchvision_version == "0.16.2+cu118"
    assert probe.nerfstudio_version == "1.1.5"
    assert probe.gsplat_version == "1.4.0"
    assert [item.role for item in probe.executables] == [
        "ns-export",
        "ns-train",
        "python",
    ]
    assert fixture.which_calls == ["ns-export", "ns-train", "python"]
    assert fixture.package_calls == ["nerfstudio", "gsplat"]
    assert fixture.import_calls == [
        "gsplat",
        "nerfstudio",
        "torch",
        "torchvision",
    ]
    assert probe.training_cli_schema_sha256 == (
        training_cli_schema_sha256(
            trainer_name="nerfstudio-splatfacto",
            observed_options=probe.training_cli_options,
        )
    )
    assert load_production_cuda_image_probe_bytes(
        canonical_production_cuda_image_probe_bytes(probe)
    ) == probe
    assert all(
        "cuda.is_available" not in " ".join(call)
        for call in fixture.run_calls
    )


def test_probe_rejects_symlink_executable(tmp_path: Path) -> None:
    fixture = _probe_fixture(tmp_path)
    target = fixture.executables["ns-train"]
    link = tmp_path / "linked-ns-train"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    fixture.executables["ns-train"] = link

    with pytest.raises(
        probe_module.ProductionCudaImageProbeError,
        match="regular non-link executable",
    ):
        _collect(fixture)


def test_probe_rejects_executable_replacement_during_observation(
    tmp_path: Path,
) -> None:
    fixture = _probe_fixture(tmp_path)
    original_run = fixture.run

    def replacing_run(argv, **kwargs):
        result = original_run(argv, **kwargs)
        if "-c" in tuple(str(item) for item in argv):
            fixture.executables["python"].write_bytes(b"replacement\n")
        return result

    fixture.run = replacing_run  # type: ignore[method-assign]

    with pytest.raises(
        probe_module.ProductionCudaImageProbeError,
        match="changed during probe",
    ):
        _collect(fixture)


@pytest.mark.parametrize(
    "bad_output",
    (
        b"\xff\n",
        b"x" * (1024 * 1024 + 1),
    ),
    ids=("non-ascii", "oversized"),
)
def test_probe_rejects_non_ascii_or_oversized_output(
    tmp_path: Path,
    bad_output: bytes,
) -> None:
    fixture = _probe_fixture(tmp_path)
    fixture.help_stdout = bad_output

    with pytest.raises(
        probe_module.ProductionCudaImageProbeError,
        match="bounded ASCII",
    ):
        _collect(fixture)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("python_version", "3.11.10"),
        ("torch_version", "2.4.1+cu118"),
        ("torch_cuda_version", "12.8"),
        ("torchvision_version", "0.16.1+cu118"),
    ),
)
def test_probe_rejects_wrong_observed_version(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    fixture = _probe_fixture(tmp_path)
    fixture.version_stdout = _version_output(**{field: value})

    with pytest.raises(
        probe_module.ProductionCudaImageProbeError,
        match="version",
    ):
        _collect(fixture)


def test_probe_rejects_wrong_package_version(tmp_path: Path) -> None:
    fixture = _probe_fixture(tmp_path)

    def wrong_version(name: str) -> str:
        return "1.1.4" if name == "nerfstudio" else "1.4.0"

    fixture.package_version = wrong_version  # type: ignore[method-assign]

    with pytest.raises(
        probe_module.ProductionCudaImageProbeError,
        match="version",
    ):
        _collect(fixture)


def test_probe_rejects_missing_training_option(tmp_path: Path) -> None:
    fixture = _probe_fixture(tmp_path)
    fixture.help_stdout = _help_output().replace(b"  --data PATH\n", b"")

    with pytest.raises(
        probe_module.ProductionCudaImageProbeError,
        match="required training CLI",
    ):
        _collect(fixture)


def test_probe_rejects_import_failure(tmp_path: Path) -> None:
    fixture = _probe_fixture(tmp_path)

    def fail_import(name: str) -> object:
        raise ImportError(name)

    fixture.import_module = fail_import  # type: ignore[method-assign]

    with pytest.raises(
        probe_module.ProductionCudaImageProbeError,
        match="required import",
    ):
        _collect(fixture)


def test_probe_rejects_runtime_lock_drift(tmp_path: Path) -> None:
    fixture = _probe_fixture(tmp_path)
    original_run = fixture.run

    def drift_lock(argv, **kwargs):
        result = original_run(argv, **kwargs)
        fixture.lock_path.write_bytes(
            canonical_production_cuda_runtime_lock_bytes(
                _runtime_lock(
                    required_imports=(
                        "gsplat",
                        "nerfstudio",
                        "torch",
                        "torchmetrics",
                        "torchvision",
                    )
                )
            )
        )
        return result

    fixture.run = drift_lock  # type: ignore[method-assign]

    with pytest.raises(
        probe_module.ProductionCudaImageProbeError,
        match="runtime lock changed",
    ):
        _collect(fixture)


def test_probe_rejects_unexpected_platform(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _probe_fixture(tmp_path)
    monkeypatch.setattr(
        probe_module,
        "_runtime_platform",
        lambda: "linux/arm64",
    )

    with pytest.raises(
        probe_module.ProductionCudaImageProbeError,
        match="linux/amd64",
    ):
        _collect(fixture)

    assert fixture.which_calls == []


def test_probe_rejects_attempted_gpu_success_input(tmp_path: Path) -> None:
    fixture = _probe_fixture(tmp_path)
    fixture.version_stdout = _version_output(cuda_available=True)

    with pytest.raises(
        probe_module.ProductionCudaImageProbeError,
        match="version probe",
    ):
        _collect(fixture)


def test_probe_rejects_nonzero_command(tmp_path: Path) -> None:
    fixture = _probe_fixture(tmp_path)

    def failing_run(argv, **kwargs):
        return SimpleNamespace(returncode=2, stdout=b"", stderr=b"failed")

    fixture.run = failing_run  # type: ignore[method-assign]

    with pytest.raises(
        probe_module.ProductionCudaImageProbeError,
        match="command failed",
    ):
        _collect(fixture)


def test_cli_refuses_existing_output_before_collecting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "image-probe.json"
    output.write_text("existing", encoding="ascii")
    called = False

    def unexpected_collect(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("collect must not run")

    monkeypatch.setattr(
        probe_module,
        "collect_image_probe",
        unexpected_collect,
    )

    assert probe_module.main(
        [
            "--runtime-lock",
            str(tmp_path / "runtime-lock.json"),
            "--output",
            str(output),
        ]
    ) == 2
    assert called is False
    assert output.read_text(encoding="ascii") == "existing"


def test_probe_source_never_queries_gpu_availability() -> None:
    source = Path(probe_module.__file__).read_text(encoding="utf-8")

    assert "cuda" + ".is_available" not in source
