from __future__ import annotations

import os
from pathlib import Path

import pytest

import pipeline.production_release_fs as release_fs

pytestmark = pytest.mark.production_mutation


@pytest.mark.skipif(
    os.name != "posix" or not Path("/proc/self/fd").is_dir(),
    reason="Linux dirfd semantics are required",
)
def test_bound_parent_survives_lexical_directory_replacement(
    tmp_path: Path,
) -> None:
    original = tmp_path / "original"
    replacement = tmp_path / "replacement"
    original.mkdir()
    replacement.mkdir()

    with release_fs.open_bound_directory(original) as bound:
        moved = tmp_path / "moved"
        original.rename(moved)
        replacement.rename(original)
        with bound.create_file("payload.bin") as payload:
            payload.write_all(b"bound")
            payload.finish()
        bound.fsync()
        with pytest.raises(
            release_fs.ProductionReleaseMutationError,
            match="lexical identity changed",
        ):
            bound.verify_lexical_identity()

    assert (moved / "payload.bin").read_bytes() == b"bound"
    assert not (original / "payload.bin").exists()


def test_non_linux_rejects_before_first_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    calls: list[object] = []
    monkeypatch.setattr(release_fs.sys, "platform", "win32")
    monkeypatch.setattr(
        release_fs.os,
        "mkdir",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    with pytest.raises(
        release_fs.ProductionReleaseMutationUnsupportedError,
        match="Linux",
    ):
        release_fs.open_bound_directory(parent)

    assert calls == []


@pytest.mark.skipif(
    os.name != "posix",
    reason="Linux dirfd semantics are required",
)
def test_created_file_failure_is_retained_and_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    original_fstat = release_fs.os.fstat

    def fail_after_create(descriptor: int) -> os.stat_result:
        observed = original_fstat(descriptor)
        if observed.st_size == 0 and not os.path.isdir(
            f"/proc/self/fd/{descriptor}"
        ):
            raise OSError("injected")
        return observed

    with release_fs.open_bound_directory(parent) as bound:
        monkeypatch.setattr(release_fs.os, "fstat", fail_after_create)
        with pytest.raises(
            release_fs.ProductionReleaseMutationError
        ) as raised:
            bound.create_file("retained.bin")

    assert raised.value.published == ("retained.bin",)
    assert raised.value.retained == ("retained.bin",)
    assert (parent / "retained.bin").exists()


@pytest.mark.skipif(
    os.name != "posix",
    reason="Linux dirfd semantics are required",
)
def test_components_are_single_names_and_no_replace(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "existing").write_bytes(b"sentinel")

    with release_fs.open_bound_directory(parent) as bound:
        with pytest.raises(ValueError, match="single path component"):
            bound.create_file("../escape")
        with pytest.raises(FileExistsError):
            bound.create_file("existing")

    assert (parent / "existing").read_bytes() == b"sentinel"
    assert not (tmp_path / "escape").exists()


@pytest.mark.skipif(
    os.name != "posix",
    reason="Linux dirfd semantics are required",
)
def test_post_create_fsync_failure_reports_exact_retained_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    with release_fs.open_bound_directory(parent) as bound:
        payload = bound.create_file("retained.bin")
        payload.write_all(b"x")
        monkeypatch.setattr(
            release_fs.os,
            "fsync",
            lambda _descriptor: (_ for _ in ()).throw(OSError("injected")),
        )
        with pytest.raises(
            release_fs.ProductionReleaseMutationError,
            match="finish failed",
        ) as raised:
            payload.finish()
        monkeypatch.undo()
        payload.close()

    assert raised.value.published == ("retained.bin",)
    assert raised.value.retained == ("retained.bin",)


@pytest.mark.skipif(
    os.name != "posix",
    reason="Linux dirfd semantics are required",
)
def test_child_name_swap_is_detected_against_held_file(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    with release_fs.open_bound_directory(parent) as bound:
        payload = bound.create_file("payload.bin")
        payload.write_all(b"held")
        payload.finish()
        (parent / "payload.bin").rename(parent / "held.bin")
        (parent / "payload.bin").write_bytes(b"replacement")

        with pytest.raises(
            release_fs.ProductionReleaseMutationError,
            match="child identity changed",
        ) as raised:
            bound.verify_child_identity("payload.bin", payload)
        payload.close()

    assert raised.value.published == ("payload.bin",)
    assert raised.value.retained == ("payload.bin",)


def test_close_bound_capabilities_attempts_every_close_and_merges_state() -> None:
    calls: list[str] = []

    class CloseProbe:
        def __init__(
            self,
            name: str,
            error: Exception | None = None,
        ) -> None:
            self.name = name
            self.error = error

        def close(self) -> None:
            calls.append(self.name)
            if self.error is not None:
                raise self.error

    first_error = release_fs.ProductionReleaseMutationError(
        "first close failed",
        published=("public/first.bin",),
        retained=("public/first.bin",),
    )
    second_error = release_fs.ProductionReleaseMutationError(
        "second close failed",
        published=("public/second.bin",),
        retained=("private/second.bin",),
    )

    error = release_fs.close_bound_capabilities_best_effort(
        (
            CloseProbe("first", first_error),
            CloseProbe("middle"),
            CloseProbe("second", second_error),
            CloseProbe("last"),
        )
    )

    assert calls == ["first", "middle", "second", "last"]
    assert error is not None
    assert error.published == ("public/first.bin", "public/second.bin")
    assert error.retained == ("public/first.bin", "private/second.bin")
    assert isinstance(error.__cause__, ExceptionGroup)
    assert error.__cause__.exceptions == (first_error, second_error)


def test_close_bound_capabilities_returns_none_when_every_close_succeeds() -> None:
    calls: list[str] = []

    class CloseProbe:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            calls.append(self.name)

    error = release_fs.close_bound_capabilities_best_effort(
        (CloseProbe("first"), None, CloseProbe("last"))
    )

    assert error is None
    assert calls == ["first", "last"]


def test_production_mutation_modules_expose_no_cleanup_or_replace_path() -> None:
    root = Path(__file__).resolve().parents[1]
    modules = (
        root / "pipeline/production_release_fs.py",
        root / "pipeline/production_release_builder.py",
        root / "pipeline/production_release_assets.py",
        root / "pipeline/production_release_privacy.py",
        root / "pipeline/production_release_verifier.py",
    )
    forbidden = (
        ".unlink(",
        ".rmdir(",
        "shutil.rmtree(",
        "os.replace(",
        "os.rename(",
        "TemporaryDirectory(",
        "matches_real_directory_identity",
        "capture_real_directory_identity",
    )

    for module in modules:
        source = module.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{module.name} contains {token}"
