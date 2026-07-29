import errno
import hashlib
import inspect
import json
import os
import re
import secrets
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Annotated

from pydantic import Field, TypeAdapter

from vla_wam_daily.models import (
    CacheEntry,
    DataFile,
    PaperRecord,
    RunStats,
    UtcDatetime,
)

ARXIV_ID_PATTERN = re.compile(r"^\d{4}\.\d{4,5}$")
CACHE_KEY_PREFIX = "analysis:v1:"
StrictPersistedInteger = Annotated[int, Field(strict=True)]
STRICT_PERSISTED_INTEGER = TypeAdapter(StrictPersistedInteger)
RUN_STATS_INTEGER_FIELDS = frozenset(RunStats.model_fields) - {"error_categories"}


def _canonical_nonblank(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be blank")
    if value != value.strip():
        raise ValueError(f"{name} must not contain surrounding whitespace")
    return value


def cache_key(arxiv_id: str, version: int, model: str, prompt_version: str) -> str:
    if not isinstance(arxiv_id, str) or ARXIV_ID_PATTERN.fullmatch(arxiv_id) is None:
        raise ValueError("arxiv_id must be an unversioned modern arXiv identifier")
    if isinstance(version, bool) or not isinstance(version, int):
        raise TypeError("version must be an integer")
    if version < 1:
        raise ValueError("version must be at least 1")
    components = {
        "arxiv_id": arxiv_id,
        "model": _canonical_nonblank("model", model),
        "prompt_version": _canonical_nonblank("prompt_version", prompt_version),
        "version": version,
    }
    canonical = json.dumps(
        components,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"{CACHE_KEY_PREFIX}{hashlib.sha256(canonical).hexdigest()}"


def _serialize_json(payload: object) -> str:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    )


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        try:
            handle = os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")
        except BaseException:
            os.close(descriptor)
            raise
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_descriptor = os.open(path.parent, directory_flags)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary_path.unlink(missing_ok=True)


def atomic_write_json(path: Path, payload: object) -> None:
    content = _serialize_json(payload)
    _atomic_write_text(path, content)


def _require_secure_directory_storage() -> None:
    required_flags = ("O_DIRECTORY", "O_NOFOLLOW")
    required_dir_fd_functions = (os.open, os.mkdir, os.unlink)
    try:
        replace_supports_dir_fds = {
            "src_dir_fd",
            "dst_dir_fd",
        }.issubset(inspect.signature(os.replace).parameters)
    except (TypeError, ValueError):
        replace_supports_dir_fds = False
    if (
        os.name != "posix"
        or any(not hasattr(os, flag) for flag in required_flags)
        or any(function not in os.supports_dir_fd for function in required_dir_fd_functions)
        or not replace_supports_dir_fds
    ):
        raise RuntimeError("secure directory-relative storage is unavailable on this platform")


def _directory_open_flags(*, nofollow: bool) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    if nofollow:
        flags |= os.O_NOFOLLOW
    return flags


def _unsafe_storage_path(name: str) -> ValueError:
    return ValueError(f"storage path resolves outside data directory: {name}")


def _create_trusted_parent_chain(parent: Path) -> None:
    missing: list[Path] = []
    current = parent
    while not current.exists():
        if current == current.parent:
            raise FileNotFoundError(f"no existing ancestor for data directory: {parent}")
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        with suppress(FileExistsError):
            os.mkdir(directory, 0o755)
        parent_descriptor = os.open(
            directory.parent,
            _directory_open_flags(nofollow=False),
        )
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)


def _open_data_root(data_dir: Path, *, create: bool) -> int | None:
    _require_secure_directory_storage()
    created = False
    if create:
        _create_trusted_parent_chain(data_dir.parent)
        try:
            os.mkdir(data_dir, 0o755)
            created = True
        except FileExistsError:
            pass
    if created:
        parent_descriptor = os.open(
            data_dir.parent,
            _directory_open_flags(nofollow=False),
        )
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
    try:
        return os.open(data_dir, _directory_open_flags(nofollow=False))
    except FileNotFoundError:
        if create:
            raise
        return None


def _open_relative_directory(
    parent_descriptor: int,
    name: str,
    *,
    create: bool,
) -> int | None:
    flags = _directory_open_flags(nofollow=True)
    try:
        return os.open(name, flags, dir_fd=parent_descriptor)
    except FileNotFoundError:
        if not create:
            return None
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise _unsafe_storage_path(name) from error
        raise
    try:
        os.mkdir(name, 0o755, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
    except FileExistsError:
        pass
    try:
        return os.open(name, flags, dir_fd=parent_descriptor)
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise _unsafe_storage_path(name) from error
        raise


def _close_storage_descriptors(
    descriptors: Sequence[int],
    primary_error: BaseException | None,
) -> None:
    close_errors: list[OSError] = []
    for descriptor in reversed(descriptors):
        try:
            os.close(descriptor)
        except OSError as close_error:
            close_errors.append(close_error)
    if not close_errors:
        return
    if primary_error is not None:
        for recorded_error in close_errors:
            primary_error.add_note(f"storage descriptor close failed: {recorded_error}")
        return
    first_error, *remaining_errors = close_errors
    for additional_error in remaining_errors:
        first_error.add_note(f"additional storage descriptor close failure: {additional_error}")
    raise first_error


@contextmanager
def _open_save_directories(
    data_dir: Path,
    *,
    need_archive: bool,
) -> Iterator[tuple[int, int, int | None]]:
    descriptors: list[int] = []
    primary_error: BaseException | None = None
    try:
        root_descriptor = _open_data_root(data_dir, create=True)
        if root_descriptor is None:  # pragma: no cover - create=True cannot return None
            raise RuntimeError("data directory was not created")
        descriptors.append(root_descriptor)
        cache_descriptor = _open_relative_directory(
            root_descriptor,
            "cache",
            create=True,
        )
        if cache_descriptor is None:  # pragma: no cover - create=True cannot return None
            raise RuntimeError("cache directory was not created")
        descriptors.append(cache_descriptor)
        archive_descriptor: int | None = None
        if need_archive:
            archive_descriptor = _open_relative_directory(
                root_descriptor,
                "archive",
                create=True,
            )
            if archive_descriptor is None:  # pragma: no cover - create=True cannot return None
                raise RuntimeError("archive directory was not created")
            descriptors.append(archive_descriptor)
        yield root_descriptor, cache_descriptor, archive_descriptor
    except BaseException as error:
        primary_error = error
        raise
    finally:
        _close_storage_descriptors(descriptors, primary_error)


def _open_temporary_at(directory_descriptor: int, target_name: str) -> tuple[int, str]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    for _ in range(128):
        temporary_name = f".{target_name}.{secrets.token_hex(12)}.tmp"
        try:
            descriptor = os.open(
                temporary_name,
                flags,
                0o600,
                dir_fd=directory_descriptor,
            )
        except FileExistsError:
            continue
        return descriptor, temporary_name
    raise FileExistsError(f"could not allocate temporary file for {target_name}")


def _atomic_write_text_at(
    directory_descriptor: int,
    target_name: str,
    content: str,
) -> None:
    descriptor, temporary_name = _open_temporary_at(
        directory_descriptor,
        target_name,
    )
    try:
        try:
            handle = os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")
        except BaseException:
            os.close(descriptor)
            raise
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(
            temporary_name,
            target_name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
        os.fsync(directory_descriptor)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary_name, dir_fd=directory_descriptor)


def _read_text_at(directory_descriptor: int, name: str) -> str | None:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_descriptor)
    except FileNotFoundError:
        return None
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise _unsafe_storage_path(name) from error
        raise
    try:
        handle = os.fdopen(descriptor, "r", encoding="utf-8")
    except BaseException:
        os.close(descriptor)
        raise
    with handle:
        return handle.read()


def _validate_strict_integer(value: object) -> None:
    STRICT_PERSISTED_INTEGER.validate_python(value, strict=True)


def _validate_persisted_analysis(value: object) -> None:
    if not isinstance(value, Mapping):
        return
    if "relevance_score" in value:
        _validate_strict_integer(value["relevance_score"])


def _validate_persisted_run_stats(value: object) -> None:
    if not isinstance(value, Mapping):
        return
    for field in RUN_STATS_INTEGER_FIELDS:
        if field in value:
            _validate_strict_integer(value[field])
    error_categories = value.get("error_categories")
    if isinstance(error_categories, Mapping):
        for count in error_categories.values():
            _validate_strict_integer(count)


def _validate_persisted_record(value: object) -> None:
    if not isinstance(value, Mapping):
        return
    if "version" in value:
        _validate_strict_integer(value["version"])
    _validate_persisted_analysis(value.get("analysis"))
    gallery = value.get("figure_gallery")
    if not isinstance(gallery, Mapping):
        return
    figures = gallery.get("figures")
    if not isinstance(figures, list):
        return
    for figure in figures:
        if isinstance(figure, Mapping) and "number" in figure:
            _validate_strict_integer(figure["number"])


def _validate_data_file_payload(raw: object) -> None:
    if isinstance(raw, dict):
        _validate_persisted_run_stats(raw.get("stats"))
        papers = raw.get("papers")
        if isinstance(papers, list):
            for paper in papers:
                _validate_persisted_record(paper)


def _load_data_file_text(content: str) -> DataFile:
    raw = json.loads(content)
    _validate_data_file_payload(raw)
    return DataFile.model_validate(raw)


def load_data_file(path: Path) -> DataFile | None:
    if not path.exists():
        return None
    return _load_data_file_text(path.read_text(encoding="utf-8"))


def _validated_cache_entry(top_level_key: str, value: object) -> CacheEntry:
    if isinstance(value, CacheEntry):
        value = value.model_dump(mode="json")
    if isinstance(value, Mapping):
        _validate_persisted_record(value.get("record"))
    entry = CacheEntry.model_validate(value)
    if top_level_key != entry.key:
        raise ValueError("top-level cache key must match CacheEntry.key")
    expected = cache_key(
        entry.record.arxiv_id,
        entry.record.version,
        entry.record.provenance.model,
        entry.record.provenance.prompt_version,
    )
    if entry.key != expected:
        raise ValueError("cache key must match record identity and provenance")
    return entry


def _validated_cache(cache: Mapping[str, object]) -> dict[str, CacheEntry]:
    validated: dict[str, CacheEntry] = {}
    for key, value in cache.items():
        if not isinstance(key, str):
            raise TypeError("cache keys must be strings")
        validated[key] = _validated_cache_entry(key, value)
    return validated


def _load_cache_text(content: str) -> dict[str, CacheEntry]:
    raw = json.loads(content)
    if not isinstance(raw, dict):
        raise ValueError("analysis cache root must be a JSON object")
    return _validated_cache(raw)


def load_cache(data_dir: Path) -> dict[str, CacheEntry]:
    root_descriptor = _open_data_root(data_dir, create=False)
    if root_descriptor is None:
        return {}
    cache_descriptor: int | None = None
    try:
        cache_descriptor = _open_relative_directory(
            root_descriptor,
            "cache",
            create=False,
        )
        if cache_descriptor is None:
            return {}
        content = _read_text_at(cache_descriptor, "analyses.json")
        if content is None:
            return {}
        return _load_cache_text(content)
    finally:
        if cache_descriptor is not None:
            os.close(cache_descriptor)
        os.close(root_descriptor)


def _validated_paper(record: PaperRecord) -> PaperRecord:
    payload = record.model_dump(mode="python")
    _validate_persisted_record(payload)
    return PaperRecord.model_validate(payload, strict=True)


def _validated_stats(stats: RunStats) -> RunStats:
    return RunStats.model_validate(stats.model_dump(mode="python"), strict=True)


def merge_records(
    existing: Sequence[PaperRecord],
    incoming: Sequence[PaperRecord],
) -> list[PaperRecord]:
    merged = {(paper.arxiv_id, paper.version): _validated_paper(paper) for paper in existing}
    for paper in incoming:
        validated = _validated_paper(paper)
        merged[(validated.arxiv_id, validated.version)] = validated
    return sorted(
        merged.values(),
        key=lambda paper: (
            paper.published_at,
            paper.updated_at,
            paper.arxiv_id,
            paper.version,
        ),
        reverse=True,
    )


def _data_file(
    *,
    generated_at: UtcDatetime,
    stats: RunStats,
    papers: Sequence[PaperRecord],
) -> DataFile:
    return DataFile.model_validate(
        {
            "generated_at": generated_at,
            "stats": stats.model_dump(mode="python"),
            "papers": tuple(paper.model_dump(mode="python") for paper in papers),
        },
        strict=True,
    )


def _reject_duplicate_published_identities(published: Sequence[PaperRecord]) -> None:
    seen: set[tuple[str, int]] = set()
    for paper in published:
        identity = paper.arxiv_id, paper.version
        if identity in seen:
            raise ValueError(
                f"duplicate published paper identity: {paper.arxiv_id}v{paper.version}"
            )
        seen.add(identity)


def save_successful_run(
    data_dir: Path,
    published: Sequence[PaperRecord],
    cache: Mapping[str, CacheEntry],
    stats: RunStats,
    generated_at: UtcDatetime,
) -> None:
    validated_published = [_validated_paper(paper) for paper in published]
    _reject_duplicate_published_identities(validated_published)
    validated_cache = _validated_cache(cache)
    validated_stats = _validated_stats(stats)
    latest = _data_file(
        generated_at=generated_at,
        stats=validated_stats,
        papers=validated_published,
    )

    by_month: dict[str, list[PaperRecord]] = {}
    for paper in validated_published:
        by_month.setdefault(paper.published_at.strftime("%Y-%m"), []).append(paper)

    with _open_save_directories(
        data_dir,
        need_archive=bool(by_month),
    ) as (root_descriptor, cache_descriptor, archive_descriptor):
        archive_files: dict[str, DataFile] = {}
        for month, records in sorted(by_month.items()):
            if archive_descriptor is None:  # pragma: no cover - guarded by need_archive
                raise RuntimeError("archive directory is unavailable")
            filename = f"{month}.json"
            current_content = _read_text_at(archive_descriptor, filename)
            current = _load_data_file_text(current_content) if current_content is not None else None
            merged = merge_records(
                current.papers if current is not None else (),
                records,
            )
            archive_files[filename] = _data_file(
                generated_at=generated_at,
                stats=validated_stats,
                papers=merged,
            )

        cache_content = _serialize_json(
            {key: entry.model_dump(mode="json") for key, entry in sorted(validated_cache.items())}
        )
        archive_contents = {
            filename: _serialize_json(archive.model_dump(mode="json"))
            for filename, archive in sorted(archive_files.items())
        }
        latest_content = _serialize_json(latest.model_dump(mode="json"))

        # Keep latest last: it is the public commit marker for a fully persisted run.
        _atomic_write_text_at(cache_descriptor, "analyses.json", cache_content)
        if archive_descriptor is not None:
            for filename, content in archive_contents.items():
                _atomic_write_text_at(archive_descriptor, filename, content)
        _atomic_write_text_at(root_descriptor, "latest.json", latest_content)
