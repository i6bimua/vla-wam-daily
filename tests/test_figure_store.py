import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from tests.factories import make_gallery
from vla_wam_daily.figure_store import ArxivFigureStore
from vla_wam_daily.models import FigureAsset, FigureGallery, FigureStatus


def make_store(
    public_dir: Path,
    handler: httpx.MockTransport,
    **kwargs: object,
) -> tuple[ArxivFigureStore, httpx.Client]:
    client = httpx.Client(transport=handler)
    options: dict[str, object] = {
        "public_dir": public_dir,
        "user_agent": "VLA-WAM-Daily-Test/0.1",
        "client": client,
    }
    options.update(kwargs)
    return ArxivFigureStore(**options), client


def test_store_mirrors_panels_to_deterministic_versioned_paths(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=b"\x89PNG\r\n\x1a\nfigure",
        )

    public_dir = tmp_path / "public"
    store, external_client = make_store(
        public_dir,
        httpx.MockTransport(handler),
    )

    with store:
        gallery = store.mirror_gallery(make_gallery())

    assert gallery.figures[0].cached_image_paths == (
        "/figures/2607.12345/v1/fig1-panel1.png",
    )
    assert gallery.figures[1].cached_image_paths == (
        "/figures/2607.12345/v1/fig2-panel1.png",
    )
    assert (
        public_dir / "figures/2607.12345/v1/fig1-panel1.png"
    ).read_bytes() == b"\x89PNG\r\n\x1a\nfigure"
    assert len(requests) == 2
    assert requests[0].headers["user-agent"] == "VLA-WAM-Daily-Test/0.1"
    assert external_client.is_closed is False
    external_client.close()


def test_store_reuses_nonempty_cached_panels_without_network(
    tmp_path: Path,
) -> None:
    public_dir = tmp_path / "public"

    def first_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=b"\x89PNG\r\n\x1a\ncached",
        )

    first_store, first_client = make_store(
        public_dir,
        httpx.MockTransport(first_handler),
    )
    with first_store:
        mirrored = first_store.mirror_gallery(make_gallery())
    first_client.close()

    def unexpected_request(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("cached panels must not be downloaded again")

    second_store, second_client = make_store(
        public_dir,
        httpx.MockTransport(unexpected_request),
    )
    with second_store:
        reused = second_store.mirror_gallery(mirrored)

    assert reused == mirrored
    second_client.close()


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404),
        httpx.Response(429),
        httpx.Response(500),
        httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"<html></html>",
        ),
        httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=b"",
        ),
        httpx.Response(
            200,
            headers={
                "content-type": "image/png",
                "content-length": "101",
            },
            content=b"short",
        ),
        httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=b"x" * 101,
        ),
    ],
)
def test_store_contains_invalid_or_failed_panel_responses(
    tmp_path: Path,
    response: httpx.Response,
) -> None:
    store, client = make_store(
        tmp_path / "public",
        httpx.MockTransport(lambda _request: response),
        max_image_bytes=100,
    )

    with store:
        gallery = store.mirror_gallery(make_gallery())

    assert all(
        figure.cached_image_paths == (None,) for figure in gallery.figures
    )
    assert [
        path
        for path in (tmp_path / "public").rglob("*")
        if path.is_file()
    ] == []
    client.close()


@pytest.mark.parametrize(
    "location",
    [
        "http://arxiv.org/html/2607.12345v1/redirect.png",
        "https://example.com/html/2607.12345v1/redirect.png",
        "https://user:pass@arxiv.org/html/2607.12345v1/redirect.png",
        "https://arxiv.org:444/html/2607.12345v1/redirect.png",
        "https://arxiv.org/html/2607.99999v1/redirect.png",
        "https://arxiv.org/html/2607.12345v2/redirect.png",
        "https://arxiv.org/html/2607.12345v1/redirect.png#fragment",
    ],
)
def test_store_rejects_unsafe_redirects(
    tmp_path: Path,
    location: str,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"location": location})

    store, client = make_store(
        tmp_path / "public",
        httpx.MockTransport(handler),
    )

    with store:
        gallery = store.mirror_gallery(make_gallery())

    assert all(
        figure.cached_image_paths == (None,) for figure in gallery.figures
    )
    assert len(requests) == 2
    client.close()


def test_store_follows_safe_arxiv_redirects(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "arxiv.org":
            return httpx.Response(
                302,
                headers={
                    "location": (
                        "https://www.arxiv.org"
                        f"{request.url.path.replace('x', 'redirected-x')}"
                    )
                },
            )
        return httpx.Response(
            200,
            headers={"content-type": "image/jpeg"},
            content=b"\xff\xd8\xfffigure",
        )

    store, client = make_store(
        tmp_path / "public",
        httpx.MockTransport(handler),
    )

    with store:
        gallery = store.mirror_gallery(make_gallery())

    assert gallery.figures[0].cached_image_paths == (
        "/figures/2607.12345/v1/fig1-panel1.jpg",
    )
    assert len(requests) == 4
    client.close()


def test_store_rejects_redirects_over_the_configured_limit(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"location": str(request.url)})

    store, client = make_store(
        tmp_path / "public",
        httpx.MockTransport(handler),
        max_redirects=0,
    )

    with store:
        gallery = store.mirror_gallery(make_gallery())

    assert all(
        figure.cached_image_paths == (None,) for figure in gallery.figures
    )
    assert len(requests) == 2
    client.close()


class BrokenStream(httpx.SyncByteStream):
    def __iter__(self):
        yield b"partial"
        raise httpx.ReadError("simulated interrupted stream")


def test_store_removes_partial_files_after_stream_failure(
    tmp_path: Path,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            stream=BrokenStream(),
        )

    public_dir = tmp_path / "public"
    store, client = make_store(
        public_dir,
        httpx.MockTransport(handler),
    )

    with store:
        gallery = store.mirror_gallery(make_gallery())

    assert all(
        figure.cached_image_paths == (None,) for figure in gallery.figures
    )
    assert list(public_dir.rglob("*.tmp")) == []
    client.close()


def test_store_preserves_successful_panels_when_another_panel_fails(
    tmp_path: Path,
) -> None:
    figure = FigureAsset(
        number=1,
        label="Figure 1",
        caption="Two panels.",
        image_urls=[
            "https://arxiv.org/html/2607.12345v1/good.png",
            "https://arxiv.org/html/2607.12345v1/missing.png",
        ],
        source_url="https://arxiv.org/html/2607.12345v1#S1.F1",
    )
    gallery = FigureGallery(
        status=FigureStatus.AVAILABLE,
        html_url="https://arxiv.org/html/2607.12345v1",
        figures=[figure],
        checked_at=make_gallery().checked_at,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/good.png"):
            return httpx.Response(
                200,
                headers={"content-type": "image/png"},
                content=b"\x89PNG\r\n\x1a\nfigure",
            )
        return httpx.Response(404)

    store, client = make_store(
        tmp_path / "public",
        httpx.MockTransport(handler),
    )

    with store:
        mirrored = store.mirror_gallery(gallery)

    assert mirrored.figures[0].cached_image_paths == (
        "/figures/2607.12345/v1/fig1-panel1.png",
        None,
    )
    client.close()


def test_store_does_not_follow_a_symlink_outside_public_dir(
    tmp_path: Path,
) -> None:
    public_dir = tmp_path / "public"
    outside = tmp_path / "outside"
    public_dir.mkdir()
    outside.mkdir()
    (public_dir / "figures").symlink_to(outside, target_is_directory=True)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=b"\x89PNG\r\n\x1a\nfigure",
        )

    store, client = make_store(
        public_dir,
        httpx.MockTransport(handler),
    )

    with store:
        gallery = store.mirror_gallery(make_gallery())

    assert all(
        figure.cached_image_paths == (None,) for figure in gallery.figures
    )
    assert list(outside.rglob("*")) == []
    client.close()


def test_store_skips_unavailable_galleries_without_network(
    tmp_path: Path,
) -> None:
    def unexpected_request(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("unavailable galleries must not request images")

    store, client = make_store(
        tmp_path / "public",
        httpx.MockTransport(unexpected_request),
    )
    gallery = make_gallery(status=FigureStatus.NOT_FOUND)

    with store:
        mirrored = store.mirror_gallery(gallery)

    assert mirrored == gallery
    client.close()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("timeout_seconds", 0),
        ("max_image_bytes", 0),
        ("max_redirects", -1),
    ],
)
def test_store_rejects_invalid_limits(
    tmp_path: Path,
    name: str,
    value: int,
) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(404))
    )
    with pytest.raises(ValueError):
        ArxivFigureStore(
            public_dir=tmp_path / "public",
            user_agent="test",
            client=client,
            **{name: value},
        )
    client.close()


def test_store_installs_recovered_figure_to_deterministic_versioned_path(
    tmp_path: Path,
) -> None:
    public_dir = tmp_path / "public"
    store, client = make_store(
        public_dir,
        httpx.MockTransport(lambda _request: httpx.Response(404)),
    )

    with store:
        cached_path = store.install_recovered_figure(
            arxiv_id="2607.12345",
            version=1,
            figure_number=1,
            panel=1,
            extension="png",
            content=b"\x89PNG\r\n\x1a\nrecovered",
        )

    assert cached_path == "/figures/2607.12345/v1/fig1-panel1.png"
    assert (
        public_dir / cached_path.removeprefix("/")
    ).read_bytes() == b"\x89PNG\r\n\x1a\nrecovered"
    client.close()


@pytest.mark.parametrize("extension", ["png", "jpg", "webp", "gif", "svg"])
def test_store_installs_each_exact_supported_recovered_extension(
    tmp_path: Path,
    extension: str,
) -> None:
    store, client = make_store(
        tmp_path / "public",
        httpx.MockTransport(lambda _request: httpx.Response(404)),
    )

    with store:
        cached_path = store.install_recovered_figure(
            arxiv_id="2607.12345",
            version=2,
            figure_number=2,
            panel=3,
            extension=extension,
            content=b"asset",
        )

    assert cached_path == (
        f"/figures/2607.12345/v2/fig2-panel3.{extension}"
    )
    client.close()


def test_store_reuses_valid_nonempty_recovered_target_without_overwrite(
    tmp_path: Path,
) -> None:
    public_dir = tmp_path / "public"
    store, client = make_store(
        public_dir,
        httpx.MockTransport(lambda _request: httpx.Response(404)),
    )

    with store:
        first_path = store.install_recovered_figure(
            arxiv_id="2607.12345",
            version=1,
            figure_number=1,
            panel=1,
            extension="png",
            content=b"original",
        )
        second_path = store.install_recovered_figure(
            arxiv_id="2607.12345",
            version=1,
            figure_number=1,
            panel=1,
            extension="png",
            content=b"replacement",
        )

    assert second_path == first_path
    assert (
        public_dir / first_path.removeprefix("/")
    ).read_bytes() == b"original"
    client.close()


def test_store_replaces_an_existing_empty_recovered_target(
    tmp_path: Path,
) -> None:
    public_dir = tmp_path / "public"
    target = (
        public_dir / "figures/2607.12345/v1/fig1-panel1.png"
    )
    target.parent.mkdir(parents=True)
    target.touch()
    store, client = make_store(
        public_dir,
        httpx.MockTransport(lambda _request: httpx.Response(404)),
    )

    with store:
        cached_path = store.install_recovered_figure(
            arxiv_id="2607.12345",
            version=1,
            figure_number=1,
            panel=1,
            extension="png",
            content=b"recovered",
        )

    assert cached_path == "/figures/2607.12345/v1/fig1-panel1.png"
    assert target.read_bytes() == b"recovered"
    client.close()


@pytest.mark.parametrize(
    "overrides",
    [
        {"arxiv_id": "2607.123"},
        {"arxiv_id": "../2607.12345"},
        {"version": 0},
        {"version": True},
        {"figure_number": 0},
        {"figure_number": 3},
        {"figure_number": True},
        {"panel": 0},
        {"panel": True},
        {"extension": "jpeg"},
        {"extension": "PNG"},
        {"extension": ".png"},
        {"extension": "../png"},
        {"content": b""},
        {"content": b"x" * 11},
        {"content": bytearray(b"image")},
    ],
)
def test_store_rejects_invalid_recovered_figure_parameters(
    tmp_path: Path,
    overrides: dict[str, object],
) -> None:
    store, client = make_store(
        tmp_path / "public",
        httpx.MockTransport(lambda _request: httpx.Response(404)),
        max_image_bytes=10,
    )
    arguments: dict[str, object] = {
        "arxiv_id": "2607.12345",
        "version": 1,
        "figure_number": 1,
        "panel": 1,
        "extension": "png",
        "content": b"image",
    }
    arguments.update(overrides)

    with store, pytest.raises(ValueError):
        store.install_recovered_figure(**arguments)

    assert not (tmp_path / "public").exists()
    client.close()


def test_store_cleans_recovered_temp_file_after_atomic_link_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    public_dir = tmp_path / "public"
    store, client = make_store(
        public_dir,
        httpx.MockTransport(lambda _request: httpx.Response(404)),
    )

    def fail_link(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated link failure")

    monkeypatch.setattr("vla_wam_daily.figure_store.os.link", fail_link)
    with store, pytest.raises(OSError, match="simulated link failure"):
        store.install_recovered_figure(
            arxiv_id="2607.12345",
            version=1,
            figure_number=1,
            panel=1,
            extension="png",
            content=b"recovered",
        )

    assert list(public_dir.rglob("*.tmp")) == []
    assert not (
        public_dir / "figures/2607.12345/v1/fig1-panel1.png"
    ).exists()
    client.close()


@pytest.mark.parametrize("existing_empty", [False, True])
def test_concurrent_recovered_writers_publish_once_without_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing_empty: bool,
) -> None:
    public_dir = tmp_path / "public"
    target = public_dir / "figures/2607.12345/v1/fig1-panel1.png"
    if existing_empty:
        target.parent.mkdir(parents=True)
        target.touch()
    stores = [
        make_store(
            public_dir,
            httpx.MockTransport(lambda _request: httpx.Response(404)),
        )
        for _ in range(2)
    ]
    start_barrier = threading.Barrier(2)
    real_link = os.link
    link_calls: list[tuple[object, object]] = []
    calls_lock = threading.Lock()

    def racing_link(
        source: object,
        target: object,
        **kwargs: object,
    ) -> None:
        with calls_lock:
            link_calls.append((source, target))
        real_link(source, target, **kwargs)

    monkeypatch.setattr("vla_wam_daily.figure_store.os.link", racing_link)

    def install(index: int) -> str:
        store, _client = stores[index]
        start_barrier.wait(timeout=5)
        return store.install_recovered_figure(
            arxiv_id="2607.12345",
            version=1,
            figure_number=1,
            panel=1,
            extension="png",
            content=f"writer-{index}".encode(),
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            paths = list(executor.map(install, range(2)))
    finally:
        for store, client in stores:
            store.close()
            client.close()

    assert paths[0] == paths[1]
    assert target.read_bytes() in {b"writer-0", b"writer-1"}
    assert len(link_calls) == 1
    assert all(not Path(str(source)).is_absolute() for source, _ in link_calls)


def test_recovered_publish_revalidates_a_racing_target_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    public_dir = tmp_path / "public"
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    store, client = make_store(
        public_dir,
        httpx.MockTransport(lambda _request: httpx.Response(404)),
    )
    real_link = os.link

    def inject_symlink(
        source: object,
        target: object,
        **kwargs: object,
    ) -> None:
        directory_fd = kwargs["dst_dir_fd"]
        os.symlink(outside, target, dir_fd=directory_fd)
        real_link(source, target, **kwargs)

    monkeypatch.setattr(
        "vla_wam_daily.figure_store.os.link",
        inject_symlink,
    )
    with store, pytest.raises(ValueError, match="symbolic link"):
        store.install_recovered_figure(
            arxiv_id="2607.12345",
            version=1,
            figure_number=1,
            panel=1,
            extension="png",
            content=b"recovered",
        )

    assert outside.read_bytes() == b"outside"
    client.close()


def test_store_does_not_install_recovered_bytes_through_directory_symlink(
    tmp_path: Path,
) -> None:
    public_dir = tmp_path / "public"
    outside = tmp_path / "outside"
    public_dir.mkdir()
    outside.mkdir()
    (public_dir / "figures").symlink_to(outside, target_is_directory=True)
    store, client = make_store(
        public_dir,
        httpx.MockTransport(lambda _request: httpx.Response(404)),
    )

    with store, pytest.raises(ValueError):
        store.install_recovered_figure(
            arxiv_id="2607.12345",
            version=1,
            figure_number=1,
            panel=1,
            extension="png",
            content=b"recovered",
        )

    assert list(outside.rglob("*")) == []
    client.close()


def test_store_does_not_install_recovered_bytes_through_target_symlink(
    tmp_path: Path,
) -> None:
    public_dir = tmp_path / "public"
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    target = (
        public_dir / "figures/2607.12345/v1/fig1-panel1.png"
    )
    target.parent.mkdir(parents=True)
    target.symlink_to(outside)
    store, client = make_store(
        public_dir,
        httpx.MockTransport(lambda _request: httpx.Response(404)),
    )

    with store, pytest.raises(ValueError):
        store.install_recovered_figure(
            arxiv_id="2607.12345",
            version=1,
            figure_number=1,
            panel=1,
            extension="png",
            content=b"recovered",
        )

    assert outside.read_bytes() == b"outside"
    assert target.is_symlink()
    client.close()
