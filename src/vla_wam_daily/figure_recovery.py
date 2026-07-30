import logging
from datetime import datetime, timedelta
from typing import Protocol

from pydantic import HttpUrl

from vla_wam_daily.figure_recovery_types import RecoveredFigure
from vla_wam_daily.models import (
    FigureAsset,
    FigureGallery,
    FigureRecoveryStatus,
    FigureStatus,
    parse_arxiv_html_identity,
)

LOGGER = logging.getLogger(__name__)
RECOVERY_RETRY_INTERVAL = timedelta(hours=24)
FIGURE_RECOVERY_VERSION = 1
_FATAL_RECOVERY_ERRORS = (MemoryError, RecursionError)


class HtmlFigureFetcher(Protocol):
    def fetch(
        self,
        arxiv_id: str,
        version: int,
        checked_at: datetime,
    ) -> FigureGallery: ...


class RecoveredFigureExtractor(Protocol):
    def extract(self, arxiv_id: str, version: int) -> RecoveredFigure | None: ...


class RecoveryFigureStore(Protocol):
    def has_usable_cached_panel(self, cached_path: str) -> bool: ...

    def install_recovered_figure(
        self,
        *,
        arxiv_id: str,
        version: int,
        figure_number: int,
        panel: int,
        extension: str,
        content: bytes,
    ) -> str: ...


def _figure_one(gallery: FigureGallery) -> FigureAsset | None:
    return next(
        (figure for figure in gallery.figures if figure.number == 1),
        None,
    )


def _preserve_matching_cached_paths(
    refreshed: FigureAsset,
    original: FigureAsset | None,
) -> FigureAsset:
    if original is None or original.source != "arxiv_html":
        return refreshed
    cached_by_url = {
        str(image_url): cached_path
        for image_url, cached_path in zip(
            original.image_urls,
            original.cached_image_paths,
            strict=True,
        )
        if image_url is not None and cached_path is not None
    }
    return refreshed.model_copy(
        update={
            "cached_image_paths": tuple(
                cached_by_url.get(str(image_url))
                for image_url in refreshed.image_urls
            )
        }
    )


class FigureRecoveryService:
    def __init__(
        self,
        *,
        html_fetcher: HtmlFigureFetcher,
        source_extractor: RecoveredFigureExtractor,
        pdf_extractor: RecoveredFigureExtractor,
        store: RecoveryFigureStore,
    ) -> None:
        self.html_fetcher = html_fetcher
        self.source_extractor = source_extractor
        self.pdf_extractor = pdf_extractor
        self.store = store

    def _has_usable_local_panel(self, figure: FigureAsset) -> bool:
        return any(
            path is not None and self.store.has_usable_cached_panel(path)
            for path in figure.cached_image_paths
        )

    @staticmethod
    def _should_skip(gallery: FigureGallery, checked_at: datetime) -> bool:
        if (
            gallery.recovery_status is FigureRecoveryStatus.NOT_FOUND
            and gallery.recovery_version == FIGURE_RECOVERY_VERSION
        ):
            return True
        return (
            gallery.recovery_status is FigureRecoveryStatus.FETCH_FAILED
            and gallery.recovery_checked_at is not None
            and checked_at - gallery.recovery_checked_at
            < RECOVERY_RETRY_INTERVAL
        )

    @staticmethod
    def _merge_html_refresh(
        original: FigureGallery,
        refreshed: FigureGallery,
    ) -> FigureGallery:
        originals = {figure.number: figure for figure in original.figures}
        figures = tuple(
            _preserve_matching_cached_paths(
                figure,
                originals.get(figure.number),
            )
            for figure in refreshed.figures
        )
        if (
            2 in originals
            and not any(figure.number == 2 for figure in figures)
        ):
            figures = (*figures, originals[2])
        return original.model_copy(
            update={
                "status": (
                    FigureStatus.AVAILABLE
                    if figures
                    else refreshed.status
                ),
                "figures": figures,
                "recovery_status": FigureRecoveryStatus.NOT_ATTEMPTED,
                "recovery_checked_at": None,
            }
        )

    def _install(
        self,
        recovered: RecoveredFigure,
        *,
        arxiv_id: str,
        version: int,
    ) -> FigureAsset:
        cached_path = self.store.install_recovered_figure(
            arxiv_id=arxiv_id,
            version=version,
            figure_number=1,
            panel=1,
            extension=recovered.extension,
            content=recovered.content,
        )
        return FigureAsset(
            number=1,
            label="Figure 1",
            caption=recovered.caption,
            image_urls=(None,),
            cached_image_paths=(cached_path,),
            source_url=HttpUrl(recovered.source_url),
            source=recovered.source,
        )

    def _try_extractor(
        self,
        extractor: RecoveredFigureExtractor,
        *,
        arxiv_id: str,
        version: int,
    ) -> tuple[FigureAsset | None, bool]:
        try:
            recovered = extractor.extract(arxiv_id, version)
            if recovered is None:
                return None, False
            return (
                self._install(
                    recovered,
                    arxiv_id=arxiv_id,
                    version=version,
                ),
                False,
            )
        except _FATAL_RECOVERY_ERRORS:
            raise
        except Exception:
            LOGGER.warning(
                "Figure 1 recovery stage failed for %sv%s",
                arxiv_id,
                version,
                exc_info=True,
            )
            return None, True

    def recover_gallery(
        self,
        gallery: FigureGallery,
        *,
        checked_at: datetime,
    ) -> FigureGallery:
        arxiv_id, version = parse_arxiv_html_identity(gallery.html_url)
        existing_figure_one = _figure_one(gallery)
        if existing_figure_one is not None:
            if existing_figure_one.source == "arxiv_html":
                if any(
                    path is not None
                    for path in existing_figure_one.cached_image_paths
                ):
                    self._has_usable_local_panel(existing_figure_one)
                return gallery
            if self._has_usable_local_panel(existing_figure_one):
                return gallery

        if self._should_skip(gallery, checked_at):
            return gallery

        had_failure = False
        try:
            refreshed = self.html_fetcher.fetch(
                arxiv_id,
                version,
                checked_at,
            )
        except _FATAL_RECOVERY_ERRORS:
            raise
        except Exception:
            LOGGER.warning(
                "Figure 1 HTML refresh failed for %sv%s",
                arxiv_id,
                version,
                exc_info=True,
            )
            refreshed = gallery.model_copy(
                update={"status": FigureStatus.FETCH_FAILED, "figures": ()}
            )
            had_failure = True
        else:
            had_failure = refreshed.status is FigureStatus.FETCH_FAILED

        merged = self._merge_html_refresh(gallery, refreshed)
        if _figure_one(merged) is not None:
            return merged.model_copy(
                update={
                    "recovery_status": FigureRecoveryStatus.AVAILABLE,
                    "recovery_checked_at": checked_at,
                    "recovery_version": FIGURE_RECOVERY_VERSION,
                }
            )

        for extractor in (self.source_extractor, self.pdf_extractor):
            recovered_figure, failed = self._try_extractor(
                extractor,
                arxiv_id=arxiv_id,
                version=version,
            )
            had_failure = had_failure or failed
            if recovered_figure is None:
                continue
            figures = (
                recovered_figure,
                *(figure for figure in merged.figures if figure.number != 1),
            )
            return merged.model_copy(
                update={
                    "status": FigureStatus.AVAILABLE,
                    "figures": figures,
                    "recovery_status": FigureRecoveryStatus.AVAILABLE,
                    "recovery_checked_at": checked_at,
                    "recovery_version": FIGURE_RECOVERY_VERSION,
                }
            )

        figures = tuple(
            figure for figure in merged.figures if figure.number != 1
        )
        status = (
            FigureRecoveryStatus.FETCH_FAILED
            if had_failure
            else FigureRecoveryStatus.NOT_FOUND
        )
        return merged.model_copy(
            update={
                "status": (
                    FigureStatus.AVAILABLE
                    if figures
                    else merged.status
                ),
                "figures": figures,
                "recovery_status": status,
                "recovery_checked_at": checked_at,
                "recovery_version": FIGURE_RECOVERY_VERSION,
            }
        )
