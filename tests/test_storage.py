import json
import os
import re
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

import vla_wam_daily.storage as storage_module
from tests.factories import make_gallery, make_record
from vla_wam_daily.figures import figure_cache_key
from vla_wam_daily.models import (
    AnalyzedPaperRecord,
    CacheEntry,
    DataFile,
    FigureCacheEntry,
    PaperRecord,
    RunStats,
)
from vla_wam_daily.storage import (
    atomic_write_json,
    cache_key,
    load_cache,
    load_data_file,
    load_figure_cache,
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


def figure_cache_entry(
    *,
    arxiv_id: str = "2607.12345",
    version: int = 1,
) -> tuple[str, FigureCacheEntry]:
    key = figure_cache_key(arxiv_id, version)
    return key, FigureCacheEntry(
        key=key,
        gallery=make_gallery(arxiv_id=arxiv_id, version=version),
    )


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
    assert load_figure_cache(tmp_path) == {}


def test_save_and_load_figure_metadata_without_image_bytes(tmp_path: Path) -> None:
    key, entry = figure_cache_entry()

    save_successful_run(
        tmp_path,
        [make_record()],
        {},
        RunStats(published=1, figure_available=1),
        datetime(2026, 7, 29, tzinfo=UTC),
        figure_cache={key: entry},
    )

    loaded = load_figure_cache(tmp_path)
    assert loaded[key].gallery.figures[0].number == 1
    raw = (tmp_path / "cache/figures.json").read_text(encoding="utf-8")
    assert "https://arxiv.org/html/" in raw
    assert "data:image/" not in raw
    assert "base64," not in raw


def test_load_figure_cache_rejects_non_object_root(tmp_path: Path) -> None:
    path = tmp_path / "cache/figures.json"
    path.parent.mkdir(parents=True)
    path.write_text("[]\n", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON object"):
        load_figure_cache(tmp_path)


def test_load_figure_cache_rejects_top_level_and_entry_key_mismatch(
    tmp_path: Path,
) -> None:
    _, entry = figure_cache_entry()
    path = tmp_path / "cache/figures.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"2607.54321:v1": entry.model_dump(mode="json")}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="top-level figure cache key"):
        load_figure_cache(tmp_path)


def test_load_figure_cache_rejects_gallery_identity_mismatch(tmp_path: Path) -> None:
    key, entry = figure_cache_entry()
    payload = entry.model_dump(mode="json")
    gallery_payload = payload["gallery"]
    assert isinstance(gallery_payload, dict)
    gallery_payload["html_url"] = "https://arxiv.org/html/2607.54321v1"
    figures_payload = gallery_payload["figures"]
    assert isinstance(figures_payload, list)
    for figure_payload in figures_payload:
        assert isinstance(figure_payload, dict)
        figure_payload["source_url"] = str(figure_payload["source_url"]).replace(
            "2607.12345v1",
            "2607.54321v1",
        )
        figure_payload["image_urls"] = [
            str(image_url).replace("2607.12345v1", "2607.54321v1")
            for image_url in figure_payload["image_urls"]
        ]
    path = tmp_path / "cache/figures.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({key: payload}), encoding="utf-8")

    with pytest.raises(ValidationError, match="figure cache key and gallery identities"):
        load_figure_cache(tmp_path)


@pytest.mark.parametrize("number", ["1", 1.0, True])
def test_load_figure_cache_rejects_non_integer_figure_number(
    tmp_path: Path,
    number: object,
) -> None:
    key, entry = figure_cache_entry()
    payload = entry.model_dump(mode="json")
    gallery_payload = payload["gallery"]
    assert isinstance(gallery_payload, dict)
    figures_payload = gallery_payload["figures"]
    assert isinstance(figures_payload, list)
    first_figure = figures_payload[0]
    assert isinstance(first_figure, dict)
    first_figure["number"] = number
    path = tmp_path / "cache/figures.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({key: payload}), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_figure_cache(tmp_path)


def test_load_figure_cache_rejects_embedded_data_image(tmp_path: Path) -> None:
    key, entry = figure_cache_entry()
    payload = entry.model_dump(mode="json")
    gallery_payload = payload["gallery"]
    assert isinstance(gallery_payload, dict)
    figures_payload = gallery_payload["figures"]
    assert isinstance(figures_payload, list)
    first_figure = figures_payload[0]
    assert isinstance(first_figure, dict)
    first_figure["image_urls"] = ["data:image/png;base64,AAAA"]
    path = tmp_path / "cache/figures.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({key: payload}), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_figure_cache(tmp_path)


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


def test_load_cache_rejects_string_version_in_persisted_json(tmp_path: Path) -> None:
    key, entry = cache_entry()
    payload = entry.model_dump(mode="json")
    record_payload = payload["record"]
    assert isinstance(record_payload, dict)
    record_payload["version"] = "1"
    path = tmp_path / "cache/analyses.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({key: payload}), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_cache(tmp_path)


@pytest.mark.parametrize("published", ["1", True])
def test_data_file_loader_rejects_non_integer_published_stats(
    tmp_path: Path,
    published: object,
) -> None:
    payload = DataFile(
        generated_at=datetime(2026, 7, 27, 2, tzinfo=UTC),
        stats=RunStats(published=1),
        papers=[make_record()],
    ).model_dump(mode="json")
    stats_payload = payload["stats"]
    assert isinstance(stats_payload, dict)
    stats_payload["published"] = published
    path = tmp_path / "latest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_data_file(path)


@pytest.mark.parametrize("score", ["8", 8.0])
def test_data_file_loader_rejects_non_integer_relevance_score(
    tmp_path: Path,
    score: object,
) -> None:
    payload = DataFile(
        generated_at=datetime(2026, 7, 27, 2, tzinfo=UTC),
        stats=RunStats(published=1),
        papers=[make_record()],
    ).model_dump(mode="json")
    paper_payload = payload["papers"][0]
    assert isinstance(paper_payload, dict)
    analysis_payload = paper_payload["analysis"]
    assert isinstance(analysis_payload, dict)
    analysis_payload["relevance_score"] = score
    path = tmp_path / "latest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_data_file(path)


@pytest.mark.parametrize("score", ["8", 8.0])
def test_load_cache_rejects_non_integer_relevance_score(
    tmp_path: Path,
    score: object,
) -> None:
    key, entry = cache_entry()
    payload = entry.model_dump(mode="json")
    record_payload = payload["record"]
    assert isinstance(record_payload, dict)
    analysis_payload = record_payload["analysis"]
    assert isinstance(analysis_payload, dict)
    analysis_payload["relevance_score"] = score
    path = tmp_path / "cache/analyses.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({key: payload}), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_cache(tmp_path)


def test_normal_persisted_json_loads_datetime_urls_and_json_arrays() -> None:
    data = load_data_file(Path("tests/fixtures/data/latest.json"))
    cache = load_cache(Path("tests/fixtures/data"))
    figure_cache = load_figure_cache(Path("tests/fixtures/data"))

    assert data is not None
    assert isinstance(data.generated_at, datetime)
    assert isinstance(data.papers[0].authors, tuple)
    assert str(data.papers[0].resources.arxiv_url).startswith("https://arxiv.org/")
    assert isinstance(next(iter(cache.values())).record.authors, tuple)
    assert figure_cache == {}


@pytest.mark.parametrize("data_dir", [Path("data"), Path("tests/fixtures/data")])
def test_seed_figure_caches_are_valid_empty_objects(data_dir: Path) -> None:
    assert load_figure_cache(data_dir) == {}
    assert (data_dir / "cache/figures.json").read_bytes() == b"{}\n"


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


def test_save_rejects_duplicate_published_identity_before_writing(tmp_path: Path) -> None:
    record = make_record()

    with pytest.raises(ValueError, match="duplicate published paper identity"):
        save_successful_run(
            tmp_path,
            [record, record],
            {},
            RunStats(published=2),
            datetime(2026, 7, 27, 2, tzinfo=UTC),
        )

    assert not (tmp_path / "latest.json").exists()
    assert not (tmp_path / "cache/analyses.json").exists()
    assert not (tmp_path / "archive/2026-07.json").exists()


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


def test_atomic_json_fsyncs_file_then_parent_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fsync_targets: list[str] = []
    real_fsync = os.fsync

    def track_fsync(descriptor: int) -> None:
        mode = os.fstat(descriptor).st_mode
        fsync_targets.append("directory" if stat.S_ISDIR(mode) else "file")
        real_fsync(descriptor)

    monkeypatch.setattr(storage_module.os, "fsync", track_fsync)
    atomic_write_json(tmp_path / "payload.json", {"durable": True})

    assert fsync_targets == ["file", "directory"]


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


def test_later_output_failure_keeps_latest_as_last_commit_marker_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated_at = datetime(2026, 7, 27, 2, tzinfo=UTC)
    first = make_record(version=1)
    first_key, first_entry = cache_entry(analyzed_record(first))
    save_successful_run(
        tmp_path,
        [first],
        {first_key: first_entry},
        RunStats(published=1),
        generated_at,
    )
    latest_before = (tmp_path / "latest.json").read_bytes()
    archive_before = (tmp_path / "archive/2026-07.json").read_bytes()
    second = make_record(version=2)
    second_key, second_entry = cache_entry(analyzed_record(second))
    archive_path = tmp_path / "archive/2026-07.json"
    real_replace = os.replace

    def fail_archive_replace(
        source: os.PathLike[str],
        target: os.PathLike[str],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        if Path(target).name == archive_path.name:
            raise OSError("archive replace failed")
        real_replace(
            source,
            target,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(storage_module.os, "replace", fail_archive_replace)
    with pytest.raises(OSError, match="archive replace failed"):
        save_successful_run(
            tmp_path,
            [first, second],
            {
                first_key: first_entry,
                second_key: second_entry,
            },
            RunStats(published=2),
            generated_at + timedelta(days=1),
        )

    assert second_key in load_cache(tmp_path)
    assert (tmp_path / "archive/2026-07.json").read_bytes() == archive_before
    assert (tmp_path / "latest.json").read_bytes() == latest_before
    assert list(tmp_path.rglob(".*.tmp")) == []


def test_figure_cache_write_failure_keeps_latest_as_last_commit_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated_at = datetime(2026, 7, 27, 2, tzinfo=UTC)
    figure_key, figure_entry = figure_cache_entry()
    save_successful_run(
        tmp_path,
        [make_record()],
        {},
        RunStats(published=1, figure_available=1),
        generated_at,
        figure_cache={figure_key: figure_entry},
    )
    latest_before = (tmp_path / "latest.json").read_bytes()
    figures_before = (tmp_path / "cache/figures.json").read_bytes()
    real_replace = os.replace

    def fail_figure_replace(
        source: os.PathLike[str],
        target: os.PathLike[str],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        if Path(target).name == "figures.json":
            raise OSError("figure cache replace failed")
        real_replace(
            source,
            target,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(storage_module.os, "replace", fail_figure_replace)
    with pytest.raises(OSError, match="figure cache replace failed"):
        save_successful_run(
            tmp_path,
            [make_record(version=2)],
            {},
            RunStats(published=1, figure_available=1),
            generated_at + timedelta(days=1),
            figure_cache={figure_key: figure_entry},
        )

    assert (tmp_path / "cache/figures.json").read_bytes() == figures_before
    assert (tmp_path / "latest.json").read_bytes() == latest_before
    assert list(tmp_path.rglob(".*.tmp")) == []


def test_omitted_figure_cache_does_not_overwrite_existing_file(tmp_path: Path) -> None:
    figure_key, figure_entry = figure_cache_entry()
    save_successful_run(
        tmp_path,
        [make_record()],
        {},
        RunStats(published=1, figure_available=1),
        datetime(2026, 7, 27, 2, tzinfo=UTC),
        figure_cache={figure_key: figure_entry},
    )
    figures_before = (tmp_path / "cache/figures.json").read_bytes()

    save_successful_run(
        tmp_path,
        [],
        {},
        RunStats(),
        datetime(2026, 7, 28, 2, tzinfo=UTC),
    )

    assert (tmp_path / "cache/figures.json").read_bytes() == figures_before
    assert figure_key in load_figure_cache(tmp_path)


def test_explicit_empty_figure_cache_overwrites_existing_file(tmp_path: Path) -> None:
    figure_key, figure_entry = figure_cache_entry()
    save_successful_run(
        tmp_path,
        [make_record()],
        {},
        RunStats(published=1, figure_available=1),
        datetime(2026, 7, 27, 2, tzinfo=UTC),
        figure_cache={figure_key: figure_entry},
    )

    save_successful_run(
        tmp_path,
        [],
        {},
        RunStats(),
        datetime(2026, 7, 28, 2, tzinfo=UTC),
        figure_cache={},
    )

    assert load_figure_cache(tmp_path) == {}
    assert (tmp_path / "cache/figures.json").read_text(encoding="utf-8") == "{}\n"


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


def test_save_revalidates_figure_cache_before_writing_any_file(tmp_path: Path) -> None:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    figure_key, figure_entry = figure_cache_entry()
    save_successful_run(
        tmp_path,
        [make_record()],
        {},
        RunStats(published=1, figure_available=1),
        now,
        figure_cache={figure_key: figure_entry},
    )
    latest_before = (tmp_path / "latest.json").read_bytes()
    analyses_before = (tmp_path / "cache/analyses.json").read_bytes()
    figures_before = (tmp_path / "cache/figures.json").read_bytes()
    archive_before = (tmp_path / "archive/2026-07.json").read_bytes()
    invalid_entry = FigureCacheEntry.model_construct(
        key=figure_key,
        gallery=make_gallery(arxiv_id="2607.54321"),
    )

    with pytest.raises(ValidationError, match="figure cache key and gallery identities"):
        save_successful_run(
            tmp_path,
            [make_record(arxiv_id="2607.54321")],
            {},
            RunStats(published=1, figure_available=1),
            now + timedelta(days=1),
            figure_cache={figure_key: invalid_entry},
        )

    assert (tmp_path / "latest.json").read_bytes() == latest_before
    assert (tmp_path / "cache/analyses.json").read_bytes() == analyses_before
    assert (tmp_path / "cache/figures.json").read_bytes() == figures_before
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


def test_save_allows_a_normal_not_yet_created_nested_data_directory(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"

    save_successful_run(
        data_dir,
        [],
        {},
        RunStats(),
        datetime(2026, 7, 27, 2, tzinfo=UTC),
    )

    assert load_data_file(data_dir / "latest.json") is not None
    assert load_cache(data_dir) == {}


def test_save_rejects_a_missing_parent_chain_before_creating_output(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "a/b/data"

    with pytest.raises(FileNotFoundError, match="data_dir parent must already exist"):
        save_successful_run(
            data_dir,
            [],
            {},
            RunStats(),
            datetime(2026, 7, 27, 2, tzinfo=UTC),
        )

    assert not (tmp_path / "a").exists()


def test_save_directory_cleanup_closes_every_fd_without_masking_business_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "cache").mkdir(parents=True)
    (data_dir / "archive").mkdir()
    real_close = os.close
    close_attempts: list[int] = []

    def fail_first_close(descriptor: int) -> None:
        close_attempts.append(descriptor)
        real_close(descriptor)
        if len(close_attempts) == 1:
            raise OSError("injected close failure")

    monkeypatch.setattr(storage_module.os, "close", fail_first_close)

    with (
        pytest.raises(RuntimeError, match="business failure") as error,
        storage_module._open_save_directories(
            data_dir,
            need_archive=True,
        ),
    ):
        raise RuntimeError("business failure")

    assert len(close_attempts) == 3
    assert any("injected close failure" in note for note in getattr(error.value, "__notes__", ()))


def test_save_fails_closed_without_nofollow_directory_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(storage_module.os, "O_NOFOLLOW")

    with pytest.raises(RuntimeError, match="secure directory-relative storage"):
        save_successful_run(
            tmp_path / "data",
            [],
            {},
            RunStats(),
            datetime(2026, 7, 27, 2, tzinfo=UTC),
        )

    assert not (tmp_path / "data").exists()


def test_save_fails_closed_without_replace_dir_fd_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def replace_without_dir_fds(
        _source: os.PathLike[str],
        _target: os.PathLike[str],
    ) -> None:
        raise AssertionError("path-based replace must not be attempted")

    monkeypatch.setattr(storage_module.os, "replace", replace_without_dir_fds)

    with pytest.raises(RuntimeError, match="secure directory-relative storage"):
        save_successful_run(
            tmp_path / "data",
            [],
            {},
            RunStats(),
            datetime(2026, 7, 27, 2, tzinfo=UTC),
        )

    assert not (tmp_path / "data").exists()


def test_save_rejects_cache_directory_symlink_escape_before_any_write(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    outside = tmp_path / "outside"
    data_dir.mkdir()
    outside.mkdir()
    (data_dir / "cache").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="outside data directory"):
        save_successful_run(
            data_dir,
            [],
            {},
            RunStats(),
            datetime(2026, 7, 27, 2, tzinfo=UTC),
        )

    assert not (outside / "analyses.json").exists()
    assert not (data_dir / "latest.json").exists()


def test_save_rejects_archive_directory_symlink_escape_before_any_write(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    outside = tmp_path / "outside"
    data_dir.mkdir()
    outside.mkdir()
    (data_dir / "archive").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="outside data directory"):
        save_successful_run(
            data_dir,
            [make_record()],
            {},
            RunStats(published=1),
            datetime(2026, 7, 27, 2, tzinfo=UTC),
        )

    assert not (outside / "2026-07.json").exists()
    assert not (data_dir / "cache/analyses.json").exists()
    assert not (data_dir / "latest.json").exists()


def test_save_rejects_symlinked_archive_file_before_reading_or_writing(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    archive_dir = data_dir / "archive"
    outside_archive = tmp_path / "outside-archive.json"
    archive_dir.mkdir(parents=True)
    outside_archive.write_bytes(Path("tests/fixtures/data/archive/2026-07.json").read_bytes())
    outside_before = outside_archive.read_bytes()
    (archive_dir / "2026-07.json").symlink_to(outside_archive)

    with pytest.raises(ValueError, match="outside data directory"):
        save_successful_run(
            data_dir,
            [make_record()],
            {},
            RunStats(published=1),
            datetime(2026, 7, 27, 2, tzinfo=UTC),
        )

    assert outside_archive.read_bytes() == outside_before
    assert not (data_dir / "cache/analyses.json").exists()
    assert not (data_dir / "latest.json").exists()


def test_save_holds_cache_directory_fd_across_symlink_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    cache_dir = data_dir / "cache"
    held_cache_dir = data_dir / "held-cache"
    outside = tmp_path / "outside"
    cache_dir.mkdir(parents=True)
    outside.mkdir()
    real_serialize = storage_module._serialize_json
    swapped = False

    def swap_cache_then_serialize(payload: object) -> str:
        nonlocal swapped
        if not swapped:
            swapped = True
            cache_dir.rename(held_cache_dir)
            cache_dir.symlink_to(outside, target_is_directory=True)
        return real_serialize(payload)

    monkeypatch.setattr(storage_module, "_serialize_json", swap_cache_then_serialize)
    save_successful_run(
        data_dir,
        [],
        {},
        RunStats(),
        datetime(2026, 7, 27, 2, tzinfo=UTC),
    )

    assert not (outside / "analyses.json").exists()
    assert (held_cache_dir / "analyses.json").exists()
    assert load_data_file(data_dir / "latest.json") is not None


def test_save_writes_figure_cache_through_held_directory_fd_after_symlink_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    cache_dir = data_dir / "cache"
    held_cache_dir = data_dir / "held-cache"
    outside = tmp_path / "outside"
    cache_dir.mkdir(parents=True)
    outside.mkdir()
    figure_key, figure_entry = figure_cache_entry()
    real_atomic_write = storage_module._atomic_write_text_at
    swapped = False

    def write_then_swap(
        directory_descriptor: int,
        target_name: str,
        content: str,
    ) -> None:
        nonlocal swapped
        real_atomic_write(directory_descriptor, target_name, content)
        if target_name == "analyses.json" and not swapped:
            swapped = True
            cache_dir.rename(held_cache_dir)
            cache_dir.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(storage_module, "_atomic_write_text_at", write_then_swap)
    save_successful_run(
        data_dir,
        [],
        {},
        RunStats(),
        datetime(2026, 7, 27, 2, tzinfo=UTC),
        figure_cache={figure_key: figure_entry},
    )

    assert not (outside / "figures.json").exists()
    assert (held_cache_dir / "figures.json").exists()
    assert load_data_file(data_dir / "latest.json") is not None


def test_load_cache_rejects_cache_directory_symlink_escape(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    outside = tmp_path / "outside"
    data_dir.mkdir()
    outside.mkdir()
    key, entry = cache_entry()
    (outside / "analyses.json").write_text(
        json.dumps({key: entry.model_dump(mode="json")}),
        encoding="utf-8",
    )
    (data_dir / "cache").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="outside data directory"):
        load_cache(data_dir)


def test_load_figure_cache_rejects_cache_directory_symlink_escape(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    outside = tmp_path / "outside"
    data_dir.mkdir()
    outside.mkdir()
    key, entry = figure_cache_entry()
    (outside / "figures.json").write_text(
        json.dumps({key: entry.model_dump(mode="json")}),
        encoding="utf-8",
    )
    (data_dir / "cache").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="outside data directory"):
        load_figure_cache(data_dir)


def test_load_figure_cache_rejects_symlinked_cache_file(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    cache_dir = data_dir / "cache"
    cache_dir.mkdir(parents=True)
    outside = tmp_path / "outside-figures.json"
    key, entry = figure_cache_entry()
    outside.write_text(
        json.dumps({key: entry.model_dump(mode="json")}),
        encoding="utf-8",
    )
    (cache_dir / "figures.json").symlink_to(outside)

    with pytest.raises(ValueError, match="outside data directory"):
        load_figure_cache(data_dir)


def test_load_cache_attempts_every_close_before_raising_close_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    cache_path = data_dir / "cache/analyses.json"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_bytes(Path("tests/fixtures/data/cache/analyses.json").read_bytes())
    real_close = os.close
    close_attempts: list[int] = []

    def fail_first_close(descriptor: int) -> None:
        close_attempts.append(descriptor)
        real_close(descriptor)
        if len(close_attempts) == 1:
            raise OSError("injected load close failure")

    monkeypatch.setattr(storage_module.os, "close", fail_first_close)

    with pytest.raises(OSError, match="injected load close failure"):
        load_cache(data_dir)

    assert len(close_attempts) == 2


def test_load_cache_close_error_does_not_mask_read_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "cache").mkdir(parents=True)
    real_close = os.close
    close_attempts: list[int] = []

    def fail_first_close(descriptor: int) -> None:
        close_attempts.append(descriptor)
        real_close(descriptor)
        if len(close_attempts) == 1:
            raise OSError("injected load close failure")

    def fail_read(_directory_descriptor: int, _name: str) -> str | None:
        raise RuntimeError("injected cache read failure")

    monkeypatch.setattr(storage_module.os, "close", fail_first_close)
    monkeypatch.setattr(storage_module, "_read_text_at", fail_read)

    with pytest.raises(RuntimeError, match="injected cache read failure") as error:
        load_cache(data_dir)

    assert len(close_attempts) == 2
    assert any(
        "injected load close failure" in note for note in getattr(error.value, "__notes__", ())
    )


def test_first_data_directory_creation_fsyncs_its_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    parent_identity = (tmp_path.stat().st_dev, tmp_path.stat().st_ino)
    fsynced_directories: list[tuple[int, int]] = []
    real_fsync = os.fsync

    def track_fsync(descriptor: int) -> None:
        metadata = os.fstat(descriptor)
        if stat.S_ISDIR(metadata.st_mode):
            fsynced_directories.append((metadata.st_dev, metadata.st_ino))
        real_fsync(descriptor)

    monkeypatch.setattr(storage_module.os, "fsync", track_fsync)
    save_successful_run(
        data_dir,
        [],
        {},
        RunStats(),
        datetime(2026, 7, 27, 2, tzinfo=UTC),
    )

    assert parent_identity in fsynced_directories


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


def test_data_file_loader_rejects_string_version_in_persisted_json(
    tmp_path: Path,
) -> None:
    payload = DataFile(
        generated_at=datetime(2026, 7, 27, 2, tzinfo=UTC),
        stats=RunStats(published=1),
        papers=[make_record()],
    ).model_dump(mode="json")
    paper_payload = payload["papers"][0]
    assert isinstance(paper_payload, dict)
    paper_payload["version"] = "1"
    path = tmp_path / "latest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_data_file(path)


@pytest.mark.parametrize(
    "path",
    [
        Path("tests/fixtures/data/latest.json"),
        Path("tests/fixtures/data/archive/2026-07.json"),
    ],
)
def test_fixture_generation_time_is_not_before_paper_events(path: Path) -> None:
    data = load_data_file(path)
    assert data is not None

    event_times = [
        event_time
        for paper in data.papers
        for event_time in (
            paper.published_at,
            paper.updated_at,
            paper.provenance.analyzed_at,
            paper.figure_gallery.checked_at,
        )
    ]
    assert data.generated_at >= max(event_times)
