import io
import tarfile
from collections.abc import Mapping, Sequence

import httpx
import pytest

from vla_wam_daily.figure_source import (
    ArxivSourceFigureExtractor,
    TransientRecoveryError,
)

ARXIV_ID = "2607.12345"
VERSION = 1
SOURCE_URL = f"https://arxiv.org/e-print/{ARXIV_ID}v{VERSION}"
PNG_BYTES = b"\x89PNG\r\n\x1a\nfigure"


def make_tar(
    files: Mapping[str, bytes],
    *,
    extra_members: Sequence[tarfile.TarInfo] = (),
) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, content in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        for member in extra_members:
            archive.addfile(member)
    return output.getvalue()


def make_figure_tex(
    *,
    image_target: str = "figures/model.png",
    caption: str = r"The \textbf{model} architecture.",
    environment: str = "figure",
    body_prefix: str = r"\centering",
) -> bytes:
    return (
        rf"""
\documentclass{{article}}
\begin{{document}}
% \begin{{figure}}\includegraphics{{ignored.png}}\caption{{Ignored}}\end{{figure}}
\begin{{{environment}}}
{body_prefix}
\includegraphics[width=\textwidth]{{{image_target}}}
\caption{{{caption}}}
\label{{fig:model}}
\end{{{environment}}}
\begin{{figure}}
\includegraphics{{figures/later.png}}
\caption{{A later Figure.}}
\end{{figure}}
\end{{document}}
""".encode()
    )


def make_extractor(
    body: bytes | None = None,
    *,
    handler: httpx.BaseTransport | None = None,
    **kwargs: object,
) -> tuple[ArxivSourceFigureExtractor, httpx.Client]:
    if handler is None:
        assert body is not None
        handler = httpx.MockTransport(
            lambda _request: httpx.Response(200, content=body)
        )
    client = httpx.Client(transport=handler)
    options: dict[str, object] = {
        "user_agent": "VLA-WAM-Daily-Test/0.1",
        "client": client,
    }
    options.update(kwargs)
    return ArxivSourceFigureExtractor(**options), client


def extract_from_tar(
    files: Mapping[str, bytes],
    *,
    extra_members: Sequence[tarfile.TarInfo] = (),
    **kwargs: object,
):
    extractor, client = make_extractor(
        make_tar(files, extra_members=extra_members),
        **kwargs,
    )
    try:
        return extractor.extract(ARXIV_ID, VERSION)
    finally:
        client.close()


def test_extracts_first_direct_literal_figure_asset_and_plain_caption() -> None:
    candidate = extract_from_tar(
        {
            "main.tex": make_figure_tex(environment="figure*"),
            "figures/model.png": PNG_BYTES,
            "figures/later.png": b"later",
        }
    )

    assert candidate is not None
    assert candidate.caption == "The model architecture."
    assert candidate.extension == "png"
    assert candidate.content == PNG_BYTES
    assert candidate.source_url == SOURCE_URL
    assert candidate.source == "arxiv_source"


@pytest.mark.parametrize("command", ["input", "include"])
def test_boundedly_inlines_one_literal_local_tex_file(command: str) -> None:
    main = rf"""
\documentclass{{article}}
\begin{{document}}
\{command}{{sections/intro}}
\end{{document}}
""".encode()
    included = rb"""
\begin{figure}
\includegraphics{assets/robot}
\caption{A \emph{robot} policy.}
\end{figure}
"""

    candidate = extract_from_tar(
        {
            "main.tex": main,
            "sections/intro.tex": included,
            "assets/robot.webp": b"RIFFwebp",
        }
    )

    assert candidate is not None
    assert candidate.caption == "A robot policy."
    assert candidate.extension == "webp"
    assert candidate.content == b"RIFFwebp"


@pytest.mark.parametrize(
    ("asset_name", "content", "expected_extension"),
    [
        ("figure.png", PNG_BYTES, "png"),
        ("figure.jpg", b"\xff\xd8\xffjpg", "jpg"),
        ("figure.jpeg", b"\xff\xd8\xffjpeg", "jpg"),
        ("figure.webp", b"RIFFwebp", "webp"),
        ("figure.gif", b"GIF89a", "gif"),
        ("figure.svg", b"<svg/>", "svg"),
    ],
)
def test_accepts_each_supported_source_asset_extension(
    asset_name: str,
    content: bytes,
    expected_extension: str,
) -> None:
    candidate = extract_from_tar(
        {
            "paper/main.tex": make_figure_tex(image_target=asset_name),
            f"paper/{asset_name}": content,
            "paper/figures/later.png": b"later",
        }
    )

    assert candidate is not None
    assert candidate.extension == expected_extension
    assert candidate.content == content


def test_resolves_optional_asset_extension_inside_main_archive_root() -> None:
    candidate = extract_from_tar(
        {
            "bundle/main.tex": make_figure_tex(image_target="images/architecture"),
            "bundle/images/architecture.svg": b"<svg>architecture</svg>",
            "bundle/figures/later.png": b"later",
        }
    )

    assert candidate is not None
    assert candidate.extension == "svg"
    assert candidate.content == b"<svg>architecture</svg>"


def unsafe_member(name: str, type_: bytes = tarfile.REGTYPE) -> tarfile.TarInfo:
    member = tarfile.TarInfo(name)
    member.type = type_
    member.size = 0
    return member


@pytest.mark.parametrize(
    "member",
    [
        unsafe_member("../escape"),
        unsafe_member("/absolute"),
        unsafe_member("C:/absolute"),
        unsafe_member(r"..\escape"),
        unsafe_member(""),
        unsafe_member("link", tarfile.SYMTYPE),
        unsafe_member("hardlink", tarfile.LNKTYPE),
        unsafe_member("character", tarfile.CHRTYPE),
        unsafe_member("block", tarfile.BLKTYPE),
        unsafe_member("fifo", tarfile.FIFOTYPE),
    ],
)
def test_rejects_unsafe_or_nonregular_archive_members(
    member: tarfile.TarInfo,
) -> None:
    candidate = extract_from_tar(
        {
            "main.tex": make_figure_tex(),
            "figures/model.png": PNG_BYTES,
            "figures/later.png": b"later",
        },
        extra_members=[member],
    )

    assert candidate is None


def test_rejects_nul_in_pax_member_name() -> None:
    member = unsafe_member("placeholder")
    member.pax_headers = {"path": "bad\x00name"}

    candidate = extract_from_tar(
        {
            "main.tex": make_figure_tex(),
            "figures/model.png": PNG_BYTES,
            "figures/later.png": b"later",
        },
        extra_members=[member],
    )

    assert candidate is None


def test_allows_safe_directory_archive_members() -> None:
    directory = unsafe_member("figures", tarfile.DIRTYPE)
    candidate = extract_from_tar(
        {
            "main.tex": make_figure_tex(),
            "figures/model.png": PNG_BYTES,
            "figures/later.png": b"later",
        },
        extra_members=[directory],
    )

    assert candidate is not None


def test_rejects_conflicting_file_and_directory_member_paths() -> None:
    conflicting_directory = unsafe_member("collision", tarfile.DIRTYPE)

    candidate = extract_from_tar(
        {
            "main.tex": make_figure_tex(),
            "figures/model.png": PNG_BYTES,
            "figures/later.png": b"later",
            "collision": b"regular file",
        },
        extra_members=[conflicting_directory],
    )

    assert candidate is None


def test_rejects_archives_with_too_many_members() -> None:
    candidate = extract_from_tar(
        {
            "main.tex": make_figure_tex(),
            "figures/model.png": PNG_BYTES,
            "figures/later.png": b"later",
        },
        max_members=2,
    )

    assert candidate is None


def test_rejects_one_oversized_archive_member() -> None:
    main = make_figure_tex()
    candidate = extract_from_tar(
        {
            "main.tex": main,
            "figures/model.png": PNG_BYTES,
            "figures/later.png": b"later",
            "oversized.bin": b"x" * (len(main) + 1),
        },
        max_member_bytes=len(main),
    )

    assert candidate is None


def test_rejects_oversized_total_declared_uncompressed_bytes() -> None:
    main = make_figure_tex()
    candidate = extract_from_tar(
        {
            "main.tex": main,
            "figures/model.png": PNG_BYTES,
            "figures/later.png": b"later",
        },
        max_member_bytes=len(main),
        max_total_uncompressed_bytes=len(main) + len(PNG_BYTES) - 1,
    )

    assert candidate is None


def test_rejects_oversized_total_tex_text_bytes() -> None:
    main = make_figure_tex()
    candidate = extract_from_tar(
        {
            "main.tex": main,
            "unused.tex": b"x" * 20,
            "figures/model.png": PNG_BYTES,
            "figures/later.png": b"later",
        },
        max_tex_bytes=len(main) + 19,
    )

    assert candidate is None


@pytest.mark.parametrize(
    "files",
    [
        {
            "one.tex": make_figure_tex(),
            "two.tex": make_figure_tex(),
            "figures/model.png": PNG_BYTES,
            "figures/later.png": b"later",
        },
        {
            "main.tex": rb"""
\documentclass{article}
\begin{figure}
\includegraphics{figure.png}
\end{figure}
""",
            "figure.png": PNG_BYTES,
        },
        {
            "main.tex": make_figure_tex(image_target="missing.png"),
            "figures/later.png": b"later",
        },
    ],
)
def test_returns_none_for_missing_or_ambiguous_required_source_parts(
    files: Mapping[str, bytes],
) -> None:
    assert extract_from_tar(files) is None


def test_returns_none_when_documentclass_is_only_commented_out() -> None:
    assert (
        extract_from_tar(
            {
                "main.tex": rb"""
% \documentclass{article}
\begin{figure}
\includegraphics{figure.png}
\caption{Not a main document.}
\end{figure}
""",
                "figure.png": PNG_BYTES,
            }
        )
        is None
    )


@pytest.mark.parametrize(
    "target",
    [
        "../escape",
        "/absolute",
        r"..\escape",
        "https://example.com/figure.png",
    ],
)
def test_rejects_escaping_or_nonlocal_source_asset_targets(target: str) -> None:
    assert (
        extract_from_tar(
            {
                "paper/main.tex": make_figure_tex(image_target=target),
                "escape.png": PNG_BYTES,
                "paper/figures/later.png": b"later",
            }
        )
        is None
    )


def test_returns_none_when_optional_extension_resolution_is_ambiguous() -> None:
    assert (
        extract_from_tar(
            {
                "main.tex": make_figure_tex(image_target="figure"),
                "figure.png": PNG_BYTES,
                "figure.jpg": b"\xff\xd8\xffjpg",
                "figures/later.png": b"later",
            }
        )
        is None
    )


def test_rejects_windows_drive_asset_target_even_if_archive_contains_it() -> None:
    assert (
        extract_from_tar(
            {
                "paper/main.tex": make_figure_tex(
                    image_target="C:/figure.png"
                ),
                "paper/C:/figure.png": PNG_BYTES,
                "paper/figures/later.png": b"later",
            }
        )
        is None
    )


@pytest.mark.parametrize(
    ("main", "extra_files", "max_depth"),
    [
        (
            rb"\documentclass{article}\input{../outside}",
            {
                "outside.tex": rb"""
\begin{figure}
\includegraphics{figures/model.png}
\caption{Outside.}
\end{figure}
"""
            },
            4,
        ),
        (
            rb"\documentclass{article}\input{one}",
            {"one.tex": rb"\input{two}", "two.tex": rb"\input{one}"},
            4,
        ),
        (
            rb"\documentclass{article}\input{one}",
            {
                "one.tex": rb"\input{two}",
                "two.tex": rb"\input{three}",
                "three.tex": rb"""
\begin{figure}
\includegraphics{figures/model.png}
\caption{Too deep.}
\end{figure}
""",
            },
            2,
        ),
    ],
)
def test_rejects_escaping_cycles_and_include_depth_breaches(
    main: bytes,
    extra_files: Mapping[str, bytes],
    max_depth: int,
) -> None:
    files = {
        "main.tex": main,
        "figures/model.png": PNG_BYTES,
        "figures/later.png": b"later",
        **extra_files,
    }

    assert extract_from_tar(files, max_include_depth=max_depth) is None


@pytest.mark.parametrize(
    "figure_body",
    [
        rb"""
\begin{tikzpicture}\draw (0,0) -- (1,1);\end{tikzpicture}
\caption{A generated Figure.}
""",
        rb"""
\immediate\write18{curl https://example.com}
\includegraphics{figure.png}
\caption{An external Figure.}
""",
        rb"""
\renderfigure{figure.png}
\caption{A macro Figure.}
""",
        rb"""
\includegraphics{\figurepath}
\caption{A macro path.}
""",
        rb"""
\includegraphics{figure.png}
\includegraphics{second.png}
\caption{Two panels.}
""",
        rb"""
\begin{subfigure}{.5\textwidth}
\includegraphics{figure.png}
\end{subfigure}
\caption{A panel layout.}
""",
    ],
)
def test_returns_none_for_generated_macro_or_panel_figure_layouts(
    figure_body: bytes,
) -> None:
    main = (
        rb"\documentclass{article}\begin{document}\begin{figure}"
        + figure_body
        + rb"\end{figure}\end{document}"
    )

    assert (
        extract_from_tar(
            {
                "main.tex": main,
                "figure.png": PNG_BYTES,
                "second.png": b"second",
            }
        )
        is None
    )


def test_returns_none_when_the_figure_environment_is_macro_driven() -> None:
    main = rb"""
\documentclass{article}
\newcommand{\renderfigure}{
  \begin{figure}
  \includegraphics{figure.png}
  \caption{Hidden inside a macro.}
  \end{figure}
}
\begin{document}
\renderfigure
\end{document}
"""

    assert (
        extract_from_tar(
            {
                "main.tex": main,
                "figure.png": PNG_BYTES,
            }
        )
        is None
    )


@pytest.mark.parametrize(
    "figure_body",
    [
        rb"""
\customlayout{\includegraphics{figure.png}}
\caption{A macro-wrapped asset.}
""",
        rb"""
\includegraphics{figure.png}
\customcaption{\caption{A macro-wrapped caption.}}
""",
    ],
)
def test_returns_none_for_macro_wrapped_figure_commands(
    figure_body: bytes,
) -> None:
    main = (
        rb"\documentclass{article}\begin{document}\begin{figure}"
        + figure_body
        + rb"\end{figure}\end{document}"
    )

    assert (
        extract_from_tar(
            {
                "main.tex": main,
                "figure.png": PNG_BYTES,
            }
        )
        is None
    )


def test_source_embedded_pdf_asset_is_deferred_to_later_task() -> None:
    assert (
        extract_from_tar(
            {
                "main.tex": make_figure_tex(image_target="figure.pdf"),
                "figure.pdf": b"%PDF-1.7",
                "figures/later.png": b"later",
            }
        )
        is None
    )


def test_requests_exact_version_endpoint_with_explicit_user_agent() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(404)

    extractor, client = make_extractor(handler=httpx.MockTransport(handler))
    with extractor:
        assert extractor.extract(ARXIV_ID, VERSION) is None

    assert [str(request.url) for request in requests] == [SOURCE_URL]
    assert requests[0].headers["user-agent"] == "VLA-WAM-Daily-Test/0.1"
    assert client.is_closed is False
    client.close()


def test_follows_redirect_only_for_the_same_exact_source_identity() -> None:
    requests: list[httpx.Request] = []
    body = make_tar(
        {
            "main.tex": make_figure_tex(),
            "figures/model.png": PNG_BYTES,
            "figures/later.png": b"later",
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "arxiv.org":
            return httpx.Response(
                302,
                headers={
                    "location": (
                        f"https://www.arxiv.org/e-print/{ARXIV_ID}v{VERSION}"
                    )
                },
            )
        return httpx.Response(200, content=body)

    extractor, client = make_extractor(handler=httpx.MockTransport(handler))
    try:
        candidate = extractor.extract(ARXIV_ID, VERSION)
    finally:
        client.close()

    assert candidate is not None
    assert len(requests) == 2
    assert requests[1].url.host == "www.arxiv.org"


@pytest.mark.parametrize(
    "location",
    [
        f"http://arxiv.org/e-print/{ARXIV_ID}v{VERSION}",
        f"https://example.com/e-print/{ARXIV_ID}v{VERSION}",
        f"https://reader:secret@arxiv.org/e-print/{ARXIV_ID}v{VERSION}",
        f"https://arxiv.org:444/e-print/{ARXIV_ID}v{VERSION}",
        f"https://arxiv.org/e-print/{ARXIV_ID}v{VERSION}#fragment",
        f"https://arxiv.org/e-print/{ARXIV_ID}v{VERSION}?download=1",
        f"https://arxiv.org/e-print/{ARXIV_ID}v2",
        "https://arxiv.org/e-print/2607.99999v1",
        f"https://arxiv.org/src/{ARXIV_ID}v{VERSION}",
    ],
)
def test_rejects_redirects_that_change_exact_source_identity(
    location: str,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"location": location})

    extractor, client = make_extractor(handler=httpx.MockTransport(handler))
    try:
        assert extractor.extract(ARXIV_ID, VERSION) is None
    finally:
        client.close()

    assert len(requests) == 1


def test_rejects_redirects_over_configured_limit() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"location": SOURCE_URL})

    extractor, client = make_extractor(
        handler=httpx.MockTransport(handler),
        max_redirects=0,
    )
    try:
        assert extractor.extract(ARXIV_ID, VERSION) is None
    finally:
        client.close()

    assert len(requests) == 1


def test_source_404_is_an_unambiguous_not_found() -> None:
    extractor, client = make_extractor(
        handler=httpx.MockTransport(
            lambda _request: httpx.Response(404)
        )
    )
    try:
        assert extractor.extract(ARXIV_ID, VERSION) is None
    finally:
        client.close()


@pytest.mark.parametrize("status_code", [429, 500, 503])
def test_source_retryable_http_failures_raise_typed_transient_error(
    status_code: int,
) -> None:
    extractor, client = make_extractor(
        handler=httpx.MockTransport(
            lambda _request: httpx.Response(status_code)
        )
    )
    try:
        with pytest.raises(TransientRecoveryError):
            extractor.extract(ARXIV_ID, VERSION)
    finally:
        client.close()


def test_source_network_failure_raises_typed_transient_error() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("simulated timeout", request=request)

    extractor, client = make_extractor(handler=httpx.MockTransport(fail))
    try:
        with pytest.raises(TransientRecoveryError):
            extractor.extract(ARXIV_ID, VERSION)
    finally:
        client.close()


class BrokenStream(httpx.SyncByteStream):
    def __iter__(self):
        yield b"partial"
        raise httpx.ReadError("simulated interrupted stream")


def test_source_interrupted_stream_raises_typed_transient_error() -> None:
    extractor, client = make_extractor(
        handler=httpx.MockTransport(
            lambda _request: httpx.Response(200, stream=BrokenStream())
        )
    )
    try:
        with pytest.raises(TransientRecoveryError):
            extractor.extract(ARXIV_ID, VERSION)
    finally:
        client.close()


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b""),
        httpx.Response(200, content=b"not a tar archive"),
        httpx.Response(400, content=b"bad request"),
        httpx.Response(
            200,
            headers={"content-length": "invalid"},
            content=b"small",
        ),
        httpx.Response(
            200,
            headers={"content-length": "-1"},
            content=b"small",
        ),
        httpx.Response(
            200,
            headers={"content-length": "101"},
            content=b"small",
        ),
        httpx.Response(200, content=b"x" * 101),
    ],
)
def test_invalid_empty_or_oversized_source_body_is_deterministic_failure(
    response: httpx.Response,
) -> None:
    extractor, client = make_extractor(
        handler=httpx.MockTransport(lambda _request: response),
        max_compressed_bytes=100,
    )
    try:
        assert extractor.extract(ARXIV_ID, VERSION) is None
    finally:
        client.close()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("timeout_seconds", 0),
        ("max_compressed_bytes", 0),
        ("max_redirects", -1),
        ("max_members", 0),
        ("max_member_bytes", 0),
        ("max_total_uncompressed_bytes", 0),
        ("max_include_depth", -1),
        ("max_tex_bytes", 0),
    ],
)
def test_extractor_rejects_invalid_constructor_bounds(
    name: str,
    value: int,
) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(404)
        )
    )
    with pytest.raises(ValueError):
        ArxivSourceFigureExtractor(
            user_agent="test",
            client=client,
            **{name: value},
        )
    client.close()


def test_extractor_rejects_blank_user_agent() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(404)
        )
    )
    with pytest.raises(ValueError):
        ArxivSourceFigureExtractor(user_agent=" \t", client=client)
    client.close()


@pytest.mark.parametrize(
    ("arxiv_id", "version"),
    [
        ("2607.123", 1),
        ("2607.123456", 1),
        ("../2607.12345", 1),
        ("2607.12345", 0),
        ("2607.12345", True),
    ],
)
def test_extractor_reuses_strict_arxiv_identity_validation(
    arxiv_id: str,
    version: int,
) -> None:
    extractor, client = make_extractor(
        handler=httpx.MockTransport(
            lambda _request: httpx.Response(404)
        )
    )
    try:
        with pytest.raises(ValueError):
            extractor.extract(arxiv_id, version)
    finally:
        client.close()
