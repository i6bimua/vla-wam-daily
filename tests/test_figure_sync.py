from datetime import UTC, datetime
from pathlib import Path

from tests.factories import make_gallery, make_record
from vla_wam_daily.figure_sync import synchronize_figure_assets
from vla_wam_daily.figures import figure_cache_key
from vla_wam_daily.models import (
    FigureAsset,
    FigureCacheEntry,
    FigureGallery,
    FigureRecoveryStatus,
    FigureStatus,
    RunStats,
)
from vla_wam_daily.storage import (
    load_archives,
    load_data_file,
    load_figure_cache,
    save_figure_sync,
    save_successful_run,
)


def gallery_with_local_paths(gallery: FigureGallery) -> FigureGallery:
    identity = gallery.html_url.path.removeprefix("/html/")
    arxiv_id, version = identity.rsplit("v", maxsplit=1)
    figures = tuple(
        figure.model_copy(
            update={
                "cached_image_paths": tuple(
                    (
                        f"/figures/{arxiv_id}/v{version}/"
                        f"fig{figure.number}-panel{panel}.png"
                    )
                    for panel, _ in enumerate(figure.image_urls, start=1)
                )
            }
        )
        for figure in gallery.figures
    )
    return gallery.model_copy(update={"figures": figures})


class RecordingStore:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def mirror_gallery(self, gallery: FigureGallery) -> FigureGallery:
        self.calls.append(str(gallery.html_url))
        return gallery_with_local_paths(gallery)


class PartialStore:
    def mirror_gallery(self, gallery: FigureGallery) -> FigureGallery:
        first, second = gallery.figures
        first = first.model_copy(
            update={
                "cached_image_paths": (
                    "/figures/2607.12345/v1/fig1-panel1.png",
                )
            }
        )
        second = second.model_copy(update={"cached_image_paths": (None,)})
        return gallery.model_copy(update={"figures": (first, second)})


class UnexpectedStore:
    def mirror_gallery(self, _gallery: FigureGallery) -> FigureGallery:
        raise AssertionError("unavailable galleries must not reach the store")


class PassthroughRecovery:
    def recover_gallery(
        self,
        gallery: FigureGallery,
        *,
        checked_at: datetime,
    ) -> FigureGallery:
        return gallery


NOW = datetime(2026, 7, 30, tzinfo=UTC)


def synchronize(
    *,
    data_dir: Path,
    store: object,
    recovery: object | None = None,
):
    return synchronize_figure_assets(
        data_dir=data_dir,
        store=store,  # type: ignore[arg-type]
        recovery=recovery or PassthroughRecovery(),  # type: ignore[arg-type]
        now=NOW,
    )


def seed_current_and_historical_records(data_dir: Path) -> None:
    january_time = datetime(2026, 1, 15, tzinfo=UTC)
    july_time = datetime(2026, 7, 30, tzinfo=UTC)
    historical = make_record(arxiv_id="2601.10001").model_copy(
        update={
            "published_at": january_time,
            "updated_at": january_time,
            "figure_gallery": make_gallery(arxiv_id="2601.10001"),
        }
    )
    current = make_record(arxiv_id="2607.20001").model_copy(
        update={
            "published_at": july_time,
            "updated_at": july_time,
            "figure_gallery": make_gallery(arxiv_id="2607.20001"),
        }
    )
    historical_key = figure_cache_key("2601.10001", 1)
    current_key = figure_cache_key("2607.20001", 1)
    save_successful_run(
        data_dir,
        [historical],
        {},
        RunStats(published=1, figure_available=1),
        january_time,
        figure_cache={
            historical_key: FigureCacheEntry(
                key=historical_key,
                gallery=historical.figure_gallery,
            )
        },
    )
    save_successful_run(
        data_dir,
        [current],
        {},
        RunStats(published=1, figure_available=1),
        july_time,
        figure_cache={
            historical_key: FigureCacheEntry(
                key=historical_key,
                gallery=historical.figure_gallery,
            ),
            current_key: FigureCacheEntry(
                key=current_key,
                gallery=current.figure_gallery,
            ),
        },
    )


def test_sync_mirrors_each_identity_once_and_updates_every_persisted_copy(
    tmp_path: Path,
) -> None:
    seed_current_and_historical_records(tmp_path)
    store = RecordingStore()

    report = synchronize(data_dir=tmp_path, store=store)

    assert report.model_dump() == {
        "papers_scanned": 2,
        "panels_reused": 0,
        "panels_mirrored": 4,
        "panels_failed": 0,
        "html_recovered": 0,
        "source_recovered": 0,
        "pdf_recovered": 0,
        "recovery_not_found": 0,
        "recovery_failed": 0,
    }
    assert sorted(store.calls) == [
        "https://arxiv.org/html/2601.10001v1",
        "https://arxiv.org/html/2607.20001v1",
    ]
    latest = load_data_file(tmp_path / "latest.json")
    assert latest is not None
    assert all(
        path is not None
        for figure in latest.papers[0].figure_gallery.figures
        for path in figure.cached_image_paths
    )
    archives = load_archives(tmp_path)
    assert all(
        path is not None
        for archive in archives.values()
        for paper in archive.papers
        for figure in paper.figure_gallery.figures
        for path in figure.cached_image_paths
    )
    cache = load_figure_cache(tmp_path)
    assert all(
        path is not None
        for entry in cache.values()
        for figure in entry.gallery.figures
        for path in figure.cached_image_paths
    )


def test_sync_is_idempotent_and_counts_existing_panels_as_reused(
    tmp_path: Path,
) -> None:
    seed_current_and_historical_records(tmp_path)
    synchronize(data_dir=tmp_path, store=RecordingStore())
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*.json")
    }

    report = synchronize(data_dir=tmp_path, store=RecordingStore())

    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*.json")
    }
    assert report.panels_reused == 4
    assert report.panels_mirrored == 0
    assert report.panels_failed == 0
    assert after == before


def test_sync_preserves_remote_fallback_for_a_failed_panel(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=UTC)
    key = figure_cache_key("2607.12345", 1)
    record = make_record()
    save_successful_run(
        tmp_path,
        [record],
        {},
        RunStats(published=1, figure_available=1),
        now,
        figure_cache={
            key: FigureCacheEntry(key=key, gallery=record.figure_gallery)
        },
    )

    report = synchronize(data_dir=tmp_path, store=PartialStore())

    latest = load_data_file(tmp_path / "latest.json")
    assert latest is not None
    assert report.panels_mirrored == 1
    assert report.panels_failed == 1
    assert latest.papers[0].figure_gallery.figures[1].cached_image_paths == (
        None,
    )
    assert str(
        latest.papers[0].figure_gallery.figures[1].image_urls[0]
    ) == "https://arxiv.org/html/2607.12345v1/x2.png"


def test_sync_skips_unavailable_galleries_without_store_calls(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=UTC)
    gallery = make_gallery(status=FigureStatus.NOT_FOUND)
    record = make_record().model_copy(update={"figure_gallery": gallery})
    key = figure_cache_key("2607.12345", 1)
    save_successful_run(
        tmp_path,
        [record],
        {},
        RunStats(published=1, figure_unavailable=1),
        now,
        figure_cache={key: FigureCacheEntry(key=key, gallery=gallery)},
    )

    report = synchronize(
        data_dir=tmp_path,
        store=UnexpectedStore(),
    )

    assert report.papers_scanned == 1
    assert report.panels_reused == 0
    assert report.panels_mirrored == 0
    assert report.panels_failed == 0


def test_sync_recovers_once_then_mirrors_and_counts_source_transition(
    tmp_path: Path,
) -> None:
    figure_two = make_gallery().figures[1]
    original = FigureGallery(
        status=FigureStatus.AVAILABLE,
        html_url="https://arxiv.org/html/2607.12345v1",
        figures=(figure_two,),
        checked_at=datetime(2026, 7, 27, 1, tzinfo=UTC),
    )
    record = make_record().model_copy(update={"figure_gallery": original})
    key = figure_cache_key("2607.12345", 1)
    save_successful_run(
        tmp_path,
        [record],
        {},
        RunStats(published=1, figure_available=1),
        NOW,
        figure_cache={key: FigureCacheEntry(key=key, gallery=original)},
    )
    recovered_figure = FigureAsset(
        number=1,
        label="Figure 1",
        caption="Recovered architecture.",
        image_urls=(None,),
        cached_image_paths=(
            "/figures/2607.12345/v1/fig1-panel1.svg",
        ),
        source_url="https://arxiv.org/e-print/2607.12345v1",
        source="arxiv_source",
    )
    recovered_gallery = original.model_copy(
        update={
            "figures": (recovered_figure, figure_two),
            "recovery_status": FigureRecoveryStatus.AVAILABLE,
            "recovery_checked_at": NOW,
        }
    )

    class RecordingRecovery:
        def __init__(self) -> None:
            self.calls: list[tuple[str, datetime]] = []

        def recover_gallery(
            self,
            gallery: FigureGallery,
            *,
            checked_at: datetime,
        ) -> FigureGallery:
            self.calls.append((str(gallery.html_url), checked_at))
            return recovered_gallery

    recovery = RecordingRecovery()
    report = synchronize(
        data_dir=tmp_path,
        store=RecordingStore(),
        recovery=recovery,
    )

    assert recovery.calls == [
        ("https://arxiv.org/html/2607.12345v1", NOW)
    ]
    assert report.source_recovered == 1
    assert report.html_recovered == 0
    assert report.pdf_recovered == 0
    assert report.panels_mirrored == 2
    persisted = load_data_file(tmp_path / "latest.json")
    assert persisted is not None
    assert persisted.papers[0].figure_gallery.figures[0].source == "arxiv_source"
    assert (
        load_figure_cache(tmp_path)[key].gallery
        == persisted.papers[0].figure_gallery
    )


def test_sync_counts_attempt_outcomes_but_not_cached_recovery_reuse(
    tmp_path: Path,
) -> None:
    seed_current_and_historical_records(tmp_path)

    class OutcomeRecovery:
        def recover_gallery(
            self,
            gallery: FigureGallery,
            *,
            checked_at: datetime,
        ) -> FigureGallery:
            if "2601.10001" in str(gallery.html_url):
                return gallery.model_copy(
                    update={
                        "status": FigureStatus.NOT_FOUND,
                        "figures": (),
                        "recovery_status": FigureRecoveryStatus.NOT_FOUND,
                        "recovery_checked_at": checked_at,
                    }
                )
            return gallery

    report = synchronize(
        data_dir=tmp_path,
        store=RecordingStore(),
        recovery=OutcomeRecovery(),
    )

    assert report.recovery_not_found == 1
    assert report.recovery_failed == 0
    assert report.html_recovered == 0


def test_sync_isolates_one_recovery_exception_and_continues(
    tmp_path: Path,
) -> None:
    seed_current_and_historical_records(tmp_path)
    calls: list[str] = []

    class PartialRecovery:
        def recover_gallery(
            self,
            gallery: FigureGallery,
            *,
            checked_at: datetime,
        ) -> FigureGallery:
            url = str(gallery.html_url)
            calls.append(url)
            if "2601.10001" in url:
                raise RuntimeError("one paper failed")
            return gallery

    report = synchronize(
        data_dir=tmp_path,
        store=RecordingStore(),
        recovery=PartialRecovery(),
    )

    assert len(calls) == 2
    assert report.papers_scanned == 2
    assert report.recovery_failed == 1


def test_sync_uses_more_complete_cache_gallery_and_replaces_record_copies(
    tmp_path: Path,
) -> None:
    complete = make_gallery()
    figure_two = make_gallery().figures[1]
    incomplete = FigureGallery(
        status=FigureStatus.AVAILABLE,
        html_url="https://arxiv.org/html/2607.12345v1",
        figures=(figure_two,),
        checked_at=NOW,
    )
    record = make_record().model_copy(update={"figure_gallery": incomplete})
    key = figure_cache_key("2607.12345", 1)
    save_successful_run(
        tmp_path,
        [record],
        {},
        RunStats(published=1, figure_available=1),
        NOW,
        figure_cache={key: FigureCacheEntry(key=key, gallery=complete)},
    )

    class RecordingRecovery:
        def __init__(self) -> None:
            self.calls: list[FigureGallery] = []

        def recover_gallery(
            self,
            gallery: FigureGallery,
            *,
            checked_at: datetime,
        ) -> FigureGallery:
            self.calls.append(gallery)
            return gallery

    recovery = RecordingRecovery()
    synchronize(
        data_dir=tmp_path,
        store=RecordingStore(),
        recovery=recovery,
    )

    assert recovery.calls == [complete]
    latest = load_data_file(tmp_path / "latest.json")
    assert latest is not None
    archives = load_archives(tmp_path)
    cached = load_figure_cache(tmp_path)[key].gallery
    assert latest.papers[0].figure_gallery == cached
    assert all(
        paper.figure_gallery == cached
        for archive in archives.values()
        for paper in archive.papers
    )


def test_sync_processes_identity_present_only_in_figure_cache_once(
    tmp_path: Path,
) -> None:
    seed_current_and_historical_records(tmp_path)
    latest = load_data_file(tmp_path / "latest.json")
    assert latest is not None
    archives = load_archives(tmp_path)
    cache = load_figure_cache(tmp_path)
    cache_only_gallery = make_gallery(arxiv_id="2512.99999")
    cache_only_key = figure_cache_key("2512.99999", 1)
    cache[cache_only_key] = FigureCacheEntry(
        key=cache_only_key,
        gallery=cache_only_gallery,
    )
    save_figure_sync(
        tmp_path,
        latest=latest,
        archives=archives,
        figure_cache=cache,
    )

    class RecordingRecovery:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def recover_gallery(
            self,
            gallery: FigureGallery,
            *,
            checked_at: datetime,
        ) -> FigureGallery:
            self.calls.append(str(gallery.html_url))
            return gallery

    recovery = RecordingRecovery()
    report = synchronize(
        data_dir=tmp_path,
        store=RecordingStore(),
        recovery=recovery,
    )

    assert report.papers_scanned == 3
    assert recovery.calls.count(str(cache_only_gallery.html_url)) == 1
    assert (
        load_figure_cache(tmp_path)[cache_only_key].gallery.figures[0]
        .cached_image_paths[0]
        is not None
    )
