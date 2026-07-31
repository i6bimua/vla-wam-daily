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
FIGURE_RECOVERY_VERSION = 3
_FATAL_RECOVERY_ERRORS = (MemoryError, RecursionError)


class HtmlFigureFetcher(Protocol):
    def fetch(
        self,
        arxiv_id: str,
        version: int,
        checked_at: datetime,
    ) -> FigureGallery: ...


class RecoveredFigureExtractor(Protocol):
    def extract_all(
        self,
        arxiv_id: str,
        version: int,
    ) -> tuple[RecoveredFigure, ...]: ...


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


def _figure(
    gallery: FigureGallery,
    number: int,
) -> FigureAsset | None:
    return next(
        (figure for figure in gallery.figures if figure.number == number),
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
        return (
            gallery.recovery_status
            in {
                FigureRecoveryStatus.NOT_FOUND,
                FigureRecoveryStatus.FETCH_FAILED,
            }
            and gallery.recovery_version == FIGURE_RECOVERY_VERSION
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
        refreshed_numbers = {figure.number for figure in figures}
        figures = (
            *figures,
            *(
                figure
                for number, figure in originals.items()
                if number not in refreshed_numbers
            ),
        )
        figures = tuple(sorted(figures, key=lambda figure: figure.number))
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
            figure_number=recovered.number,
            panel=1,
            extension=recovered.extension,
            content=recovered.content,
        )
        return FigureAsset(
            number=recovered.number,
            label=f"Figure {recovered.number}",
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
        missing_numbers: set[int],
    ) -> tuple[tuple[FigureAsset, ...], bool]:
        try:
            recovered = extractor.extract_all(arxiv_id, version)
            installed: list[FigureAsset] = []
            had_failure = False
            seen: set[int] = set()
            for candidate in recovered:
                if (
                    candidate.number not in missing_numbers
                    or candidate.number in seen
                ):
                    continue
                seen.add(candidate.number)
                try:
                    installed.append(
                        self._install(
                            candidate,
                            arxiv_id=arxiv_id,
                            version=version,
                        )
                    )
                except _FATAL_RECOVERY_ERRORS:
                    raise
                except Exception:
                    had_failure = True
                    LOGGER.warning(
                        "Figure %s install failed for %sv%s",
                        candidate.number,
                        arxiv_id,
                        version,
                        exc_info=True,
                    )
            return tuple(installed), had_failure
        except _FATAL_RECOVERY_ERRORS:
            raise
        except Exception:
            LOGGER.warning(
                "Figure recovery stage failed for %sv%s",
                arxiv_id,
                version,
                exc_info=True,
            )
            return (), True

    def recover_gallery(
        self,
        gallery: FigureGallery,
        *,
        checked_at: datetime,
    ) -> FigureGallery:
        arxiv_id, version = parse_arxiv_html_identity(gallery.html_url)
        usable_figures: list[FigureAsset] = []
        for figure in gallery.figures:
            if figure.number not in {1, 2}:
                continue
            if figure.source == "arxiv_html":
                if (
                    figure.number == 1
                    and any(path is not None for path in figure.cached_image_paths)
                ):
                    self._has_usable_local_panel(figure)
                usable_figures.append(figure)
            elif self._has_usable_local_panel(figure):
                usable_figures.append(figure)
        if tuple(usable_figures) != gallery.figures:
            gallery = gallery.model_copy(
                update={
                    "figures": tuple(usable_figures),
                    "recovery_status": FigureRecoveryStatus.NOT_ATTEMPTED,
                    "recovery_checked_at": None,
                }
            )

        if {figure.number for figure in gallery.figures} == {1, 2}:
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
                "Figure HTML refresh failed for %sv%s",
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
        if {figure.number for figure in merged.figures} == {1, 2}:
            return merged.model_copy(
                update={
                    "recovery_status": FigureRecoveryStatus.AVAILABLE,
                    "recovery_checked_at": checked_at,
                    "recovery_version": FIGURE_RECOVERY_VERSION,
                }
            )

        for extractor in (self.source_extractor, self.pdf_extractor):
            missing_numbers = {1, 2} - {
                figure.number for figure in merged.figures
            }
            recovered_figures, failed = self._try_extractor(
                extractor,
                arxiv_id=arxiv_id,
                version=version,
                missing_numbers=missing_numbers,
            )
            had_failure = had_failure or failed
            existing_numbers = {figure.number for figure in merged.figures}
            accepted = tuple(
                figure
                for figure in recovered_figures
                if figure.number not in existing_numbers
            )
            if accepted:
                merged = merged.model_copy(
                    update={
                        "status": FigureStatus.AVAILABLE,
                        "figures": tuple(
                            sorted(
                                (*merged.figures, *accepted),
                                key=lambda figure: figure.number,
                            )
                        ),
                    }
                )
            if {figure.number for figure in merged.figures} == {1, 2}:
                break

        figure_one = _figure(merged, 1)
        if figure_one is not None:
            return merged.model_copy(
                update={
                    "status": FigureStatus.AVAILABLE,
                    "recovery_status": FigureRecoveryStatus.AVAILABLE,
                    "recovery_checked_at": checked_at,
                    "recovery_version": FIGURE_RECOVERY_VERSION,
                }
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
                    if merged.figures
                    else merged.status
                ),
                "recovery_status": status,
                "recovery_checked_at": checked_at,
                "recovery_version": FIGURE_RECOVERY_VERSION,
            }
        )
