import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

import vla_wam_daily.storage as storage_module
from tests.factories import make_record
from vla_wam_daily.models import (
    AnalyzedPaperRecord,
    CacheEntry,
    DataFile,
    PaperRecord,
    RunStats,
)
from vla_wam_daily.storage import (
    atomic_write_json,
    cache_key,
    load_cache,
    load_data_file,
    save_successful_run,
)


def analyzed_record(record: PaperRecord | None = None) -> AnalyzedPaperRecord:
    payload = (record or make_record()).model_dump(mode="json")
    payload.pop("figure_gallery")
    return AnalyzedPaperRecord.model_validate(payload)


def cache_entry(record: AnalyzedPaperRecord | None = None) -> tuple[str, CacheEntry]:
    resolved = record or analyzed_record()
    key = cache_key(
        resolved.arxiv_id,
        resolved.version,
        resolved.provenance.model,
        resolved.provenance.prompt_version,
    )
    return key, CacheEntry(key=key, record=resolved)


def test_cache_key_changes_with_every_identity_component_without_delimiter_ambiguity() -> None:
    first = cache_key("2607.12345", 1, "deepseek-v4-pro", "1")

    assert first != cache_key("2607.12345", 2, "deepseek-v4-pro", "1")
    assert first != cache_key("2607.54321", 1, "deepseek-v4-pro", "1")
    assert first != cache_key("2607.12345", 1, "deepseek-v4-flash", "1")
    assert first != cache_key("2607.12345", 1, "deepseek-v4-pro", "2")
    assert cache_key("2607.12345", 1, "a:b", "c") != cache_key("2607.12345", 1, "a", "b:c")
    assert re.fullmatch(r"analysis:v1:[0-9a-f]{64}", first)


@pytest.mark.parametrize(
    ("arxiv_id", "version", "model", "prompt_version"),
    [
        ("not-an-id", 1, "deepseek-v4-pro", "1"),
        ("2607.12345", 0, "deepseek-v4-pro", "1"),
        ("2607.12345", True, "deepseek-v4-pro", "1"),
        ("2607.12345", 1, " ", "1"),
        ("2607.12345", 1, " deepseek-v4-pro", "1"),
        ("2607.12345", 1, "deepseek-v4-pro", "\n"),
    ],
)
def test_cache_key_rejects_noncanonical_components(
    arxiv_id: str,
    version: int,
    model: str,
    prompt_version: str,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        cache_key(arxiv_id, version, model, prompt_version)


def test_missing_data_and_cache_load_as_empty(tmp_path: Path) -> None:
    assert load_data_file(tmp_path / "latest.json") is None
    assert load_cache(tmp_path) == {}


def test_cache_round_trip_uses_internal_analyzed_record_without_figure_gallery(
    tmp_path: Path,
) -> None:
    key, entry = cache_entry()

    save_successful_run(
        tmp_path,
        [make_record()],
        {key: entry},
        RunStats(published=1),
        datetime(2026, 7, 27, tzinfo=UTC),
    )

    loaded = load_cache(tmp_path)
    assert type(loaded[key].record) is AnalyzedPaperRecord
    assert not hasattr(loaded[key].record, "figure_gallery")
    assert "figure_gallery" not in (tmp_path / "cache/analyses.json").read_text(encoding="utf-8")


def test_load_cache_rejects_non_object_root(tmp_path: Path) -> None:
    path = tmp_path / "cache/analyses.json"
    path.parent.mkdir(parents=True)
    path.write_text("[]\n", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON object"):
        load_cache(tmp_path)


def test_load_cache_rejects_top_level_and_entry_key_mismatch(tmp_path: Path) -> None:
    key, entry = cache_entry()
    path = tmp_path / "cache/analyses.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"different-key": entry.model_dump(mode="json")}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="top-level cache key"):
        load_cache(tmp_path)


@pytest.mark.parametrize("changed_field", ["arxiv_id", "version", "model", "prompt_version"])
def test_load_cache_rejects_key_that_disagrees_with_record_identity_or_provenance(
    tmp_path: Path,
    changed_field: str,
) -> None:
    key, entry = cache_entry()
    payload = entry.model_dump(mode="json")
    record_payload = payload["record"]
    assert isinstance(record_payload, dict)
    if changed_field == "arxiv_id":
        record_payload["arxiv_id"] = "2607.54321"
    elif changed_field == "version":
        record_payload["version"] = 2
    else:
        provenance = record_payload["provenance"]
        assert isinstance(provenance, dict)
        provenance[changed_field] = f"different-{changed_field}"
    path = tmp_path / "cache/analyses.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({key: payload}), encoding="utf-8")

    with pytest.raises(ValueError, match="record identity and provenance"):
        load_cache(tmp_path)


def test_load_cache_applies_strict_model_validation(tmp_path: Path) -> None:
    key, entry = cache_entry()
    payload = entry.model_dump(mode="json")
    record_payload = payload["record"]
    assert isinstance(record_payload, dict)
    record_payload["unexpected"] = "not allowed"
    path = tmp_path / "cache/analyses.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({key: payload}), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_cache(tmp_path)


def test_latest_is_replaced_by_each_successful_batch_including_an_empty_batch(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    save_successful_run(
        tmp_path,
        [make_record()],
        {},
        RunStats(published=1),
        now,
    )

    save_successful_run(
        tmp_path,
        [],
        {},
        RunStats(),
        now + timedelta(days=1),
    )

    latest = load_data_file(tmp_path / "latest.json")
    assert latest is not None
    assert latest.generated_at == now + timedelta(days=1)
    assert latest.papers == ()
    assert latest.stats.published == 0
    archive = load_data_file(tmp_path / "archive/2026-07.json")
    assert archive is not None
    assert [(paper.arxiv_id, paper.version) for paper in archive.papers] == [("2607.12345", 1)]


def test_archives_merge_idempotently_preserve_versions_and_partition_by_published_month(
    tmp_path: Path,
) -> None:
    july_time = datetime(2026, 7, 27, tzinfo=UTC)
    august_time = datetime(2026, 8, 1, tzinfo=UTC)
    version_one = make_record(version=1)
    version_two = make_record(version=2)
    august = make_record(arxiv_id="2608.10001").model_copy(
        update={"published_at": august_time, "updated_at": august_time}
    )

    save_successful_run(
        tmp_path,
        [version_one],
        {},
        RunStats(published=1),
        july_time,
    )
    save_successful_run(
        tmp_path,
        [version_one, version_two, august],
        {},
        RunStats(published=3),
        august_time,
    )
    save_successful_run(
        tmp_path,
        [version_one, version_two, august],
        {},
        RunStats(published=3),
        august_time,
    )

    july = load_data_file(tmp_path / "archive/2026-07.json")
    august_archive = load_data_file(tmp_path / "archive/2026-08.json")
    assert july is not None
    assert august_archive is not None
    assert [(paper.arxiv_id, paper.version) for paper in july.papers] == [
        ("2607.12345", 2),
        ("2607.12345", 1),
    ]
    assert [(paper.arxiv_id, paper.version) for paper in august_archive.papers] == [
        ("2608.10001", 1)
    ]


def test_archive_sort_has_a_deterministic_total_order(tmp_path: Path) -> None:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    records = [
        make_record(arxiv_id="2607.10001"),
        make_record(arxiv_id="2607.30001"),
        make_record(arxiv_id="2607.20001"),
    ]

    save_successful_run(
        tmp_path,
        list(reversed(records)),
        {},
        RunStats(published=3),
        now,
    )
    first_bytes = (tmp_path / "archive/2026-07.json").read_bytes()
    save_successful_run(
        tmp_path,
        records,
        {},
        RunStats(published=3),
        now,
    )
    second_bytes = (tmp_path / "archive/2026-07.json").read_bytes()

    archive = load_data_file(tmp_path / "archive/2026-07.json")
    assert archive is not None
    assert [paper.arxiv_id for paper in archive.papers] == [
        "2607.30001",
        "2607.20001",
        "2607.10001",
    ]
    assert second_bytes == first_bytes


def test_atomic_json_is_utf8_deterministic_and_newline_terminated(tmp_path: Path) -> None:
    path = tmp_path / "payload.json"
    atomic_write_json(path, {"z": 1, "a": "机器人"})
    first = path.read_bytes()
    atomic_write_json(path, {"a": "机器人", "z": 1})

    assert path.read_bytes() == first
    assert first == '{\n  "a": "机器人",\n  "z": 1\n}\n'.encode()


def temporary_files_for(path: Path) -> list[Path]:
    return list(path.parent.glob(f".{path.name}.*.tmp"))


def test_atomic_json_serialization_failure_preserves_target_without_temp_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "payload.json"
    path.write_text('{"old": true}\n', encoding="utf-8")

    with pytest.raises(TypeError):
        atomic_write_json(path, {"invalid": object()})

    assert path.read_text(encoding="utf-8") == '{"old": true}\n'
    assert temporary_files_for(path) == []


def test_atomic_json_fsync_failure_preserves_target_and_cleans_temp_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "payload.json"
    path.write_text('{"old": true}\n', encoding="utf-8")

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("fsync failed")

    monkeypatch.setattr(storage_module.os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="fsync failed"):
        atomic_write_json(path, {"new": True})

    assert path.read_text(encoding="utf-8") == '{"old": true}\n'
    assert temporary_files_for(path) == []


def test_atomic_json_replace_failure_preserves_target_and_cleans_temp_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "payload.json"
    path.write_text('{"old": true}\n', encoding="utf-8")

    def fail_replace(_source: os.PathLike[str], _target: os.PathLike[str]) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(storage_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        atomic_write_json(path, {"new": True})

    assert path.read_text(encoding="utf-8") == '{"old": true}\n'
    assert temporary_files_for(path) == []


def test_save_validates_and_serializes_every_payload_before_writing_any_file(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    first = make_record()
    key, entry = cache_entry()
    save_successful_run(
        tmp_path,
        [first],
        {key: entry},
        RunStats(published=1),
        now,
    )
    latest_before = (tmp_path / "latest.json").read_bytes()
    cache_before = (tmp_path / "cache/analyses.json").read_bytes()
    archive_before = (tmp_path / "archive/2026-07.json").read_bytes()
    changed_record = analyzed_record().model_copy(
        update={
            "provenance": analyzed_record().provenance.model_copy(
                update={"model": "different-model"}
            )
        }
    )
    invalid_entry = CacheEntry(key=key, record=changed_record)

    with pytest.raises(ValueError, match="record identity and provenance"):
        save_successful_run(
            tmp_path,
            [make_record(arxiv_id="2607.54321")],
            {key: invalid_entry},
            RunStats(published=1),
            now + timedelta(days=1),
        )

    assert (tmp_path / "latest.json").read_bytes() == latest_before
    assert (tmp_path / "cache/analyses.json").read_bytes() == cache_before
    assert (tmp_path / "archive/2026-07.json").read_bytes() == archive_before


def test_save_revalidates_constructed_models_before_creating_output(tmp_path: Path) -> None:
    valid = make_record()
    constructed_fields = {field: getattr(valid, field) for field in PaperRecord.model_fields}
    invalid = PaperRecord.model_construct(**{**constructed_fields, "version": 0})

    with pytest.raises(ValidationError):
        save_successful_run(
            tmp_path,
            [invalid],
            {},
            RunStats(published=1),
            datetime(2026, 7, 27, tzinfo=UTC),
        )

    assert not (tmp_path / "latest.json").exists()
    assert not (tmp_path / "cache/analyses.json").exists()


def test_data_file_loader_rejects_invalid_public_records(tmp_path: Path) -> None:
    payload = DataFile(
        generated_at=datetime(2026, 7, 27, tzinfo=UTC),
        stats=RunStats(published=1),
        papers=[make_record()],
    ).model_dump(mode="json")
    paper_payload = payload["papers"][0]
    assert isinstance(paper_payload, dict)
    paper_payload.pop("figure_gallery")
    path = tmp_path / "latest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_data_file(path)
