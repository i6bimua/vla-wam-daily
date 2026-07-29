import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
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
StrictPersistedVersion = Annotated[int, Field(strict=True, ge=1)]
STRICT_PERSISTED_VERSION = TypeAdapter(StrictPersistedVersion)


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


def load_data_file(path: Path) -> DataFile | None:
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        papers = raw.get("papers")
        if isinstance(papers, list):
            for paper in papers:
                _validate_persisted_record_version(paper)
    return DataFile.model_validate(raw)


def _validate_persisted_record_version(value: object) -> None:
    if isinstance(value, Mapping) and "version" in value:
        STRICT_PERSISTED_VERSION.validate_python(value["version"], strict=True)


def _validated_cache_entry(top_level_key: str, value: object) -> CacheEntry:
    if isinstance(value, CacheEntry):
        value = value.model_dump(mode="json")
    if isinstance(value, Mapping):
        _validate_persisted_record_version(value.get("record"))
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


def load_cache(data_dir: Path) -> dict[str, CacheEntry]:
    path = data_dir / "cache/analyses.json"
    _ensure_data_path_is_contained(data_dir, path)
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("analysis cache root must be a JSON object")
    return _validated_cache(raw)


def _validated_paper(record: PaperRecord) -> PaperRecord:
    payload = record.model_dump(mode="python")
    _validate_persisted_record_version(payload)
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


def _ensure_data_path_is_contained(data_dir: Path, path: Path) -> None:
    root = data_dir.resolve(strict=False)
    resolved_parent = path.parent.resolve(strict=False)
    resolved_path = path.resolve(strict=False)
    if not resolved_parent.is_relative_to(root) or not resolved_path.is_relative_to(root):
        raise ValueError(f"storage path resolves outside data directory: {path}")


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

    archive_files: dict[Path, DataFile] = {}
    by_month: dict[str, list[PaperRecord]] = {}
    for paper in validated_published:
        by_month.setdefault(paper.published_at.strftime("%Y-%m"), []).append(paper)
    cache_path = data_dir / "cache" / "analyses.json"
    latest_path = data_dir / "latest.json"
    archive_paths = {month: data_dir / "archive" / f"{month}.json" for month in by_month}
    for path in (cache_path, latest_path, *archive_paths.values()):
        _ensure_data_path_is_contained(data_dir, path)
    for month, records in sorted(by_month.items()):
        path = archive_paths[month]
        current = load_data_file(path)
        merged = merge_records(current.papers if current is not None else (), records)
        archive_files[path] = _data_file(
            generated_at=generated_at,
            stats=validated_stats,
            papers=merged,
        )

    # Keep latest last: it is the public commit marker for a fully persisted run.
    payloads: dict[Path, object] = {
        cache_path: {
            key: entry.model_dump(mode="json") for key, entry in sorted(validated_cache.items())
        },
        **{
            path: archive.model_dump(mode="json") for path, archive in sorted(archive_files.items())
        },
        latest_path: latest.model_dump(mode="json"),
    }
    serialized = {path: _serialize_json(payload) for path, payload in payloads.items()}
    for path, content in serialized.items():
        _atomic_write_text(path, content)
