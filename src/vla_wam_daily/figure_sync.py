import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from pydantic import Field

from vla_wam_daily.figures import figure_cache_key
from vla_wam_daily.models import (
    DataFile,
    FigureCacheEntry,
    FigureGallery,
    FigureStatus,
    FrozenStrictModel,
    PaperRecord,
)
from vla_wam_daily.storage import (
    load_archives,
    load_data_file,
    load_figure_cache,
    save_figure_sync,
)

LOGGER = logging.getLogger(__name__)


class FigureStore(Protocol):
    def mirror_gallery(self, gallery: FigureGallery) -> FigureGallery: ...


class FigureSyncReport(FrozenStrictModel):
    papers_scanned: int = Field(ge=0)
    panels_reused: int = Field(ge=0)
    panels_mirrored: int = Field(ge=0)
    panels_failed: int = Field(ge=0)


def _cached_path_count(gallery: FigureGallery) -> int:
    return sum(
        path is not None
        for figure in gallery.figures
        for path in figure.cached_image_paths
    )


def _normalized_paths(
    gallery: FigureGallery,
) -> dict[tuple[int, int], str | None]:
    paths: dict[tuple[int, int], str | None] = {}
    for figure in gallery.figures:
        cached = (
            figure.cached_image_paths
            if figure.cached_image_paths
            else (None,) * len(figure.image_urls)
        )
        for panel, path in enumerate(cached, start=1):
            paths[(figure.number, panel)] = path
    return paths


def _select_galleries(
    data_files: Sequence[DataFile],
) -> dict[tuple[str, int], FigureGallery]:
    galleries: dict[tuple[str, int], FigureGallery] = {}
    for data_file in data_files:
        for paper in data_file.papers:
            identity = paper.arxiv_id, paper.version
            current = galleries.get(identity)
            if (
                current is None
                or _cached_path_count(paper.figure_gallery)
                > _cached_path_count(current)
            ):
                galleries[identity] = paper.figure_gallery
    return galleries


def _replace_galleries(
    data_file: DataFile,
    galleries: dict[tuple[str, int], FigureGallery],
) -> DataFile:
    papers: tuple[PaperRecord, ...] = tuple(
        paper.model_copy(
            update={
                "figure_gallery": galleries[
                    (paper.arxiv_id, paper.version)
                ]
            }
        )
        for paper in data_file.papers
    )
    return data_file.model_copy(update={"papers": papers})


def synchronize_figure_assets(
    *,
    data_dir: Path,
    store: FigureStore,
) -> FigureSyncReport:
    latest = load_data_file(data_dir / "latest.json")
    if latest is None:
        raise FileNotFoundError(f"latest data file is missing: {data_dir / 'latest.json'}")
    archives = load_archives(data_dir)
    source_galleries = _select_galleries([*archives.values(), latest])
    updated_galleries: dict[tuple[str, int], FigureGallery] = {}
    panels_reused = 0
    panels_mirrored = 0
    panels_failed = 0

    for identity, gallery in sorted(source_galleries.items()):
        before = _normalized_paths(gallery)
        if gallery.status is FigureStatus.AVAILABLE:
            try:
                mirrored = store.mirror_gallery(gallery)
            except Exception:
                LOGGER.exception(
                    "unexpected Figure synchronization failure for %sv%s",
                    identity[0],
                    identity[1],
                )
                mirrored = gallery
        else:
            mirrored = gallery
        after = _normalized_paths(mirrored)
        if gallery.status is FigureStatus.AVAILABLE:
            for panel_identity, path in after.items():
                previous = before.get(panel_identity)
                if path is None:
                    panels_failed += 1
                elif previous == path:
                    panels_reused += 1
                else:
                    panels_mirrored += 1
        updated_galleries[identity] = mirrored

    updated_latest = _replace_galleries(latest, updated_galleries)
    updated_archives = {
        filename: _replace_galleries(archive, updated_galleries)
        for filename, archive in archives.items()
    }
    updated_cache = load_figure_cache(data_dir)
    for (arxiv_id, version), gallery in updated_galleries.items():
        key = figure_cache_key(arxiv_id, version)
        updated_cache[key] = FigureCacheEntry(key=key, gallery=gallery)
    save_figure_sync(
        data_dir,
        latest=updated_latest,
        archives=updated_archives,
        figure_cache=updated_cache,
    )
    return FigureSyncReport(
        papers_scanned=len(updated_galleries),
        panels_reused=panels_reused,
        panels_mirrored=panels_mirrored,
        panels_failed=panels_failed,
    )
