from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vla_wam_daily.figure_recovery import FigureRecoveryService
from vla_wam_daily.figure_recovery_types import (
    RecoveredFigure,
    TransientRecoveryError,
)
from vla_wam_daily.figure_store import ArxivFigureStore
from vla_wam_daily.models import (
    FigureAsset,
    FigureGallery,
    FigureRecoveryStatus,
    FigureStatus,
)

ARXIV_ID = "2607.12345"
VERSION = 2
HTML_URL = f"https://arxiv.org/html/{ARXIV_ID}v{VERSION}"
CHECKED_AT = datetime(2026, 7, 30, 8, tzinfo=UTC)
ORIGINAL_CHECKED_AT = datetime(2026, 7, 27, 1, tzinfo=UTC)


def html_figure(
    number: int,
    *,
    image_url: str | None = None,
    cached_path: str | None = None,
    caption: str | None = None,
) -> FigureAsset:
    return FigureAsset(
        number=number,
        label=f"Figure {number}",
        caption=caption or f"HTML caption {number}.",
        image_urls=(
            image_url
            or f"https://arxiv.org/html/{ARXIV_ID}v{VERSION}/x{number}.png",
        ),
        cached_image_paths=(cached_path,),
        source_url=f"{HTML_URL}#S{number}.F{number}",
    )


def gallery(
    *figures: FigureAsset,
    status: FigureStatus | None = None,
    recovery_status: FigureRecoveryStatus = FigureRecoveryStatus.NOT_ATTEMPTED,
    recovery_checked_at: datetime | None = None,
) -> FigureGallery:
    resolved_status = status or (
        FigureStatus.AVAILABLE if figures else FigureStatus.NOT_FOUND
    )
    return FigureGallery(
        status=resolved_status,
        html_url=HTML_URL,
        figures=figures,
        checked_at=ORIGINAL_CHECKED_AT,
        recovery_status=recovery_status,
        recovery_checked_at=recovery_checked_at,
    )


class FakeHtmlFetcher:
    def __init__(
        self,
        result: FigureGallery | BaseException,
        calls: list[str],
    ) -> None:
        self.result = result
        self.calls = calls

    def fetch(
        self,
        arxiv_id: str,
        version: int,
        checked_at: datetime,
    ) -> FigureGallery:
        self.calls.append(f"html:{arxiv_id}:v{version}:{checked_at.isoformat()}")
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class FakeExtractor:
    def __init__(
        self,
        kind: str,
        result: RecoveredFigure | None | BaseException,
        calls: list[str],
    ) -> None:
        self.kind = kind
        self.result = result
        self.calls = calls

    def extract(self, arxiv_id: str, version: int) -> RecoveredFigure | None:
        self.calls.append(f"{self.kind}:{arxiv_id}:v{version}")
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class FakeStore:
    def __init__(
        self,
        calls: list[str],
        *,
        usable_paths: set[str] | None = None,
        install_error: BaseException | None = None,
    ) -> None:
        self.calls = calls
        self.usable_paths = usable_paths or set()
        self.install_error = install_error

    def has_usable_cached_panel(self, cached_path: str) -> bool:
        self.calls.append(f"usable:{cached_path}")
        return cached_path in self.usable_paths

    def install_recovered_figure(
        self,
        *,
        arxiv_id: str,
        version: int,
        figure_number: int,
        panel: int,
        extension: str,
        content: bytes,
    ) -> str:
        self.calls.append(
            f"install:{arxiv_id}:v{version}:fig{figure_number}:"
            f"panel{panel}:{extension}:{content.decode()}"
        )
        if self.install_error is not None:
            raise self.install_error
        return (
            f"/figures/{arxiv_id}/v{version}/"
            f"fig{figure_number}-panel{panel}.{extension}"
        )


def recovered(source: str = "arxiv_source") -> RecoveredFigure:
    suffix = "e-print" if source == "arxiv_source" else "pdf"
    extension = "svg" if source == "arxiv_source" else "png"
    return RecoveredFigure(
        caption=f"{source} recovered caption.",
        extension=extension,
        content=source.encode(),
        source_url=f"https://arxiv.org/{suffix}/{ARXIV_ID}v{VERSION}",
        source=source,  # type: ignore[arg-type]
    )


def service(
    *,
    html: FigureGallery | BaseException,
    source: RecoveredFigure | None | BaseException,
    pdf: RecoveredFigure | None | BaseException,
    calls: list[str],
    store: FakeStore | None = None,
) -> FigureRecoveryService:
    return FigureRecoveryService(
        html_fetcher=FakeHtmlFetcher(html, calls),
        source_extractor=FakeExtractor("source", source, calls),
        pdf_extractor=FakeExtractor("pdf", pdf, calls),
        store=store or FakeStore(calls),
    )


def test_usable_cached_figure_one_skips_every_recovery_stage() -> None:
    calls: list[str] = []
    cached_path = f"/figures/{ARXIV_ID}/v{VERSION}/fig1-panel1.png"
    original = gallery(
        html_figure(1, cached_path=cached_path),
        html_figure(2),
        recovery_status=FigureRecoveryStatus.AVAILABLE,
    )
    recovery = service(
        html=gallery(),
        source=None,
        pdf=None,
        calls=calls,
        store=FakeStore(calls, usable_paths={cached_path}),
    )

    result = recovery.recover_gallery(original, checked_at=CHECKED_AT)

    assert result == original
    assert calls == [f"usable:{cached_path}"]


def test_html_figure_one_without_local_cache_is_left_for_mirroring() -> None:
    calls: list[str] = []
    original = gallery(
        html_figure(1),
        html_figure(2),
        recovery_status=FigureRecoveryStatus.AVAILABLE,
    )
    recovery = service(
        html=gallery(),
        source=None,
        pdf=None,
        calls=calls,
    )

    assert recovery.recover_gallery(original, checked_at=CHECKED_AT) == original
    assert calls == []


def test_missing_figure_one_refreshes_html_and_preserves_exact_cached_panels() -> None:
    calls: list[str] = []
    figure_two_path = f"/figures/{ARXIV_ID}/v{VERSION}/fig2-panel1.png"
    original = gallery(html_figure(2, cached_path=figure_two_path))
    refreshed = gallery(html_figure(2), html_figure(1))
    recovery = service(
        html=refreshed,
        source=AssertionError("source extraction must not run"),
        pdf=AssertionError("PDF extraction must not run"),
        calls=calls,
    )

    result = recovery.recover_gallery(original, checked_at=CHECKED_AT)

    assert [figure.number for figure in result.figures] == [1, 2]
    assert result.figures[1].cached_image_paths == (figure_two_path,)
    assert result.checked_at == ORIGINAL_CHECKED_AT
    assert result.recovery_checked_at == CHECKED_AT
    assert result.recovery_status is FigureRecoveryStatus.AVAILABLE
    assert calls == [f"html:{ARXIV_ID}:v{VERSION}:{CHECKED_AT.isoformat()}"]


def test_refreshed_html_drops_cached_path_when_remote_url_changed() -> None:
    calls: list[str] = []
    figure_two_path = f"/figures/{ARXIV_ID}/v{VERSION}/fig2-panel1.png"
    original = gallery(html_figure(2, cached_path=figure_two_path))
    refreshed = gallery(
        html_figure(1),
        html_figure(
            2,
            image_url=f"https://arxiv.org/html/{ARXIV_ID}v{VERSION}/new-x2.png",
        ),
    )
    recovery = service(html=refreshed, source=None, pdf=None, calls=calls)

    result = recovery.recover_gallery(original, checked_at=CHECKED_AT)

    assert result.figures[1].cached_image_paths == (None,)


def test_html_miss_uses_source_and_installs_local_only_figure_one() -> None:
    calls: list[str] = []
    original = gallery(html_figure(2))
    recovery = service(
        html=gallery(),
        source=recovered(),
        pdf=AssertionError("PDF extraction must not run"),
        calls=calls,
    )

    result = recovery.recover_gallery(original, checked_at=CHECKED_AT)

    figure_one, figure_two = result.figures
    assert figure_one.number == 1
    assert figure_one.image_urls == (None,)
    assert figure_one.cached_image_paths == (
        f"/figures/{ARXIV_ID}/v{VERSION}/fig1-panel1.svg",
    )
    assert figure_one.source == "arxiv_source"
    assert str(figure_one.source_url) == (
        f"https://arxiv.org/e-print/{ARXIV_ID}v{VERSION}"
    )
    assert figure_one.caption == "arxiv_source recovered caption."
    assert figure_two.number == 2
    assert result.status is FigureStatus.AVAILABLE
    assert result.checked_at == ORIGINAL_CHECKED_AT
    assert result.recovery_status is FigureRecoveryStatus.AVAILABLE
    assert result.recovery_checked_at == CHECKED_AT
    assert calls == [
        f"html:{ARXIV_ID}:v{VERSION}:{CHECKED_AT.isoformat()}",
        f"source:{ARXIV_ID}:v{VERSION}",
        f"install:{ARXIV_ID}:v{VERSION}:fig1:panel1:svg:arxiv_source",
    ]


@pytest.mark.parametrize(
    "source_result",
    [None, TransientRecoveryError("temporary source failure")],
)
def test_pdf_is_attempted_after_source_miss_or_transient_failure(
    source_result: None | BaseException,
) -> None:
    calls: list[str] = []
    recovery = service(
        html=gallery(),
        source=source_result,
        pdf=recovered("arxiv_pdf"),
        calls=calls,
    )

    result = recovery.recover_gallery(gallery(), checked_at=CHECKED_AT)

    assert result.figures[0].source == "arxiv_pdf"
    assert calls[-2:] == [
        f"pdf:{ARXIV_ID}:v{VERSION}",
        f"install:{ARXIV_ID}:v{VERSION}:fig1:panel1:png:arxiv_pdf",
    ]
    assert result.recovery_status is FigureRecoveryStatus.AVAILABLE


def test_all_definitive_misses_are_permanently_not_found() -> None:
    calls: list[str] = []
    recovery = service(html=gallery(), source=None, pdf=None, calls=calls)

    result = recovery.recover_gallery(gallery(), checked_at=CHECKED_AT)

    assert result.status is FigureStatus.NOT_FOUND
    assert result.recovery_status is FigureRecoveryStatus.NOT_FOUND
    assert result.recovery_checked_at == CHECKED_AT
    assert result.checked_at == ORIGINAL_CHECKED_AT


@pytest.mark.parametrize(
    ("html_result", "source_result", "pdf_result"),
    [
        (gallery(status=FigureStatus.FETCH_FAILED), None, None),
        (gallery(), ValueError("source parser failed"), None),
        (gallery(), None, TransientRecoveryError("PDF fetch failed")),
    ],
)
def test_exhausted_failure_is_recorded_as_fetch_failed(
    html_result: FigureGallery,
    source_result: RecoveredFigure | None | BaseException,
    pdf_result: RecoveredFigure | None | BaseException,
) -> None:
    calls: list[str] = []
    recovery = service(
        html=html_result,
        source=source_result,
        pdf=pdf_result,
        calls=calls,
    )

    result = recovery.recover_gallery(gallery(), checked_at=CHECKED_AT)

    assert result.recovery_status is FigureRecoveryStatus.FETCH_FAILED
    assert result.recovery_checked_at == CHECKED_AT


def test_install_failure_falls_through_to_pdf_success() -> None:
    calls: list[str] = []

    class SourceFailingStore(FakeStore):
        def install_recovered_figure(self, **kwargs: object) -> str:
            if kwargs["extension"] == "svg":
                self.calls.append("install-source-failed")
                raise OSError("cannot install source asset")
            return super().install_recovered_figure(**kwargs)  # type: ignore[arg-type]

    recovery = service(
        html=gallery(),
        source=recovered(),
        pdf=recovered("arxiv_pdf"),
        calls=calls,
        store=SourceFailingStore(calls),
    )

    result = recovery.recover_gallery(gallery(), checked_at=CHECKED_AT)

    assert result.figures[0].source == "arxiv_pdf"
    assert "install-source-failed" in calls
    assert f"pdf:{ARXIV_ID}:v{VERSION}" in calls


def test_skip_permanent_results_and_throttle_recent_fetch_failure() -> None:
    terminal_time = CHECKED_AT - timedelta(hours=48)
    recent_failure_time = CHECKED_AT - timedelta(hours=23)
    for status, checked_at in (
        (FigureRecoveryStatus.NOT_FOUND, terminal_time),
        (FigureRecoveryStatus.FETCH_FAILED, recent_failure_time),
    ):
        calls: list[str] = []
        original = gallery(
            recovery_status=status,
            recovery_checked_at=checked_at,
        )
        recovery = service(html=gallery(), source=None, pdf=None, calls=calls)

        assert recovery.recover_gallery(original, checked_at=CHECKED_AT) == original
        assert calls == []


def test_fetch_failure_retries_after_twenty_four_hours() -> None:
    calls: list[str] = []
    original = gallery(
        recovery_status=FigureRecoveryStatus.FETCH_FAILED,
        recovery_checked_at=CHECKED_AT - timedelta(hours=24),
    )
    recovery = service(html=gallery(), source=None, pdf=None, calls=calls)

    result = recovery.recover_gallery(original, checked_at=CHECKED_AT)

    assert calls[0] == f"html:{ARXIV_ID}:v{VERSION}:{CHECKED_AT.isoformat()}"
    assert result.recovery_status is FigureRecoveryStatus.NOT_FOUND
    assert result.recovery_checked_at == CHECKED_AT


def test_missing_local_recovered_figure_is_retried() -> None:
    calls: list[str] = []
    missing_path = f"/figures/{ARXIV_ID}/v{VERSION}/fig1-panel1.svg"
    source_figure = FigureAsset(
        number=1,
        label="Figure 1",
        caption="Old recovered caption.",
        image_urls=(None,),
        cached_image_paths=(missing_path,),
        source_url=f"https://arxiv.org/e-print/{ARXIV_ID}v{VERSION}",
        source="arxiv_source",
    )
    original = gallery(
        source_figure,
        html_figure(2),
        recovery_status=FigureRecoveryStatus.AVAILABLE,
    )
    recovery = service(
        html=gallery(html_figure(2)),
        source=recovered("arxiv_pdf"),
        pdf=None,
        calls=calls,
    )

    result = recovery.recover_gallery(original, checked_at=CHECKED_AT)

    assert calls[:2] == [
        f"usable:{missing_path}",
        f"html:{ARXIV_ID}:v{VERSION}:{CHECKED_AT.isoformat()}",
    ]
    assert result.figures[0].source == "arxiv_pdf"


def test_fatal_resource_errors_are_not_swallowed() -> None:
    recovery = service(
        html=gallery(),
        source=MemoryError("out of memory"),
        pdf=None,
        calls=[],
    )

    with pytest.raises(MemoryError):
        recovery.recover_gallery(gallery(), checked_at=CHECKED_AT)


def test_store_validates_real_nonempty_cached_panel(tmp_path: Path) -> None:
    public_dir = tmp_path / "public"
    cached_path = f"/figures/{ARXIV_ID}/v{VERSION}/fig1-panel1.png"
    target = public_dir / cached_path.removeprefix("/")
    target.parent.mkdir(parents=True)
    target.write_bytes(b"image")
    store = ArxivFigureStore(public_dir=public_dir, user_agent="test/1")
    try:
        assert store.has_usable_cached_panel(cached_path) is True
        target.unlink()
        assert store.has_usable_cached_panel(cached_path) is False
    finally:
        store.close()
