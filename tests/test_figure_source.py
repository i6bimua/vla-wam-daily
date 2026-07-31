import io
import tarfile
from collections.abc import Mapping, Sequence

import httpx
import pytest
from PIL import Image
from reportlab.pdfgen.canvas import Canvas

from vla_wam_daily.figure_recovery_types import DEFAULT_MAX_ASSET_BYTES
from vla_wam_daily.figure_source import (
    ArxivSourceFigureExtractor,
    TransientRecoveryError,
    _lex_tex,
)

ARXIV_ID = "2607.12345"
VERSION = 1
SOURCE_URL = f"https://arxiv.org/e-print/{ARXIV_ID}v{VERSION}"


def make_image(
    format_: str,
    *,
    size: tuple[int, int] = (2, 2),
    frames: int = 1,
) -> bytes:
    output = io.BytesIO()
    images = [
        Image.new("RGB", size, (index * 20, 80, 120))
        for index in range(frames)
    ]
    images[0].save(
        output,
        format=format_,
        save_all=frames > 1,
        append_images=images[1:],
    )
    return output.getvalue()


PNG_BYTES = make_image("PNG")
JPEG_BYTES = make_image("JPEG")
WEBP_BYTES = make_image("WEBP")
GIF_BYTES = make_image("GIF")


def make_pdf_asset(*, pages: int = 1, page_size: tuple[int, int] = (120, 80)) -> bytes:
    output = io.BytesIO()
    pdf = Canvas(output, pagesize=page_size, invariant=1, pageCompression=0)
    for page_number in range(pages):
        pdf.setFillColorRGB(0.1, 0.3, 0.8)
        pdf.rect(10, 10, 100, 60, stroke=0, fill=1)
        pdf.setStrokeColorRGB(1, 1, 1)
        pdf.line(15, 15 + page_number, 105, 65 - page_number)
        pdf.setFillColorRGB(1, 1, 1)
        pdf.setFont("Helvetica", 8)
        pdf.drawString(45, 36, "panel")
        pdf.showPage()
    pdf.save()
    return output.getvalue()


def make_tar(
    files: Mapping[str, bytes],
    *,
    extra_members: Sequence[tarfile.TarInfo] = (),
    mode: str = "w:gz",
    format_: int | None = None,
) -> bytes:
    output = io.BytesIO()
    options = {} if format_ is None else {"format": format_}
    with tarfile.open(fileobj=output, mode=mode, **options) as archive:
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


def extract_all_from_tar(
    files: Mapping[str, bytes],
    **kwargs: object,
):
    extractor, client = make_extractor(make_tar(files), **kwargs)
    try:
        return extractor.extract_all(ARXIV_ID, VERSION)
    finally:
        client.close()


def test_extracts_first_two_literal_overpic_pdf_assets() -> None:
    main = rb"""
\documentclass{article}
\newcommand{\method}{VAD}
\begin{document}
\begin{figure}
\centering
\begin{overpic}[width=\textwidth]{figures/introduction_v9.pdf}
\put(4,4){ignored overlay}
\end{overpic}
\Description{Accessible figure description.}
\caption{Overview of the proposed \method{} method.}
\end{figure}
\begin{figure*}
\begin{overpic}[width=345.0pt]{figures/overview_v11.pdf}
\end{overpic}
\caption{Detailed system architecture with $r_t^{\mathrm{vis}}$.}
\end{figure*}
\end{document}
"""

    candidates = extract_all_from_tar(
        {
            "main.tex": main,
            "figures/introduction_v9.pdf": make_pdf_asset(),
            "figures/overview_v11.pdf": make_pdf_asset(),
        }
    )

    assert [candidate.number for candidate in candidates] == [1, 2]
    assert [candidate.caption for candidate in candidates] == [
        "Overview of the proposed VAD method.",
        "Detailed system architecture with r_t^vis.",
    ]
    assert all(candidate.extension == "png" for candidate in candidates)
    assert all(
        candidate.content.startswith(b"\x89PNG\r\n\x1a\n")
        for candidate in candidates
    )


def test_overpic_does_not_bypass_figure_counter_safety() -> None:
    main = rb"""
\documentclass{article}
\begin{document}
\setcounter{figure}{7}
\begin{figure}
\begin{overpic}{figure.pdf}
\end{overpic}
\caption{Actually Figure 8.}
\end{figure}
\end{document}
"""

    assert (
        extract_all_from_tar(
            {
                "main.tex": main,
                "figure.pdf": make_pdf_asset(),
            }
        )
        == ()
    )


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


def test_best_effort_accepts_harmless_class_and_unrelated_preamble_macro() -> None:
    main = rb"""
\documentclass{local}
\newcommand{\projectname}{See2Think}
\begin{document}
\begin{figure}
\centering
\includegraphics[width=\textwidth]{figures/model.png}
\caption{The literal first Figure.}
\end{figure}
\end{document}
"""

    candidate = extract_from_tar(
        {
            "main.tex": main,
            "local.cls": rb"""
\NeedsTeXFormat{LaTeX2e}
\let\projectalias\relax
\newcommand{\projectlabel}{Thinking with Video}
""",
            "figures/model.png": PNG_BYTES,
        }
    )

    assert candidate is not None
    assert candidate.caption == "The literal first Figure."
    assert candidate.extension == "png"
    assert candidate.content == PNG_BYTES


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
            "assets/robot.webp": WEBP_BYTES,
        }
    )

    assert candidate is not None
    assert candidate.caption == "A robot policy."
    assert candidate.extension == "webp"
    assert candidate.content == WEBP_BYTES


@pytest.mark.parametrize(
    ("asset_name", "content", "expected_extension"),
    [
        ("figure.png", PNG_BYTES, "png"),
        ("figure.jpg", JPEG_BYTES, "jpg"),
        ("figure.jpeg", JPEG_BYTES, "jpg"),
        ("figure.webp", WEBP_BYTES, "webp"),
        ("figure.gif", GIF_BYTES, "gif"),
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
            "bundle/images/architecture.webp": WEBP_BYTES,
            "bundle/figures/later.png": b"later",
        }
    )

    assert candidate is not None
    assert candidate.extension == "webp"
    assert candidate.content == WEBP_BYTES


def test_source_svg_asset_is_not_published_without_sanitization() -> None:
    assert (
        extract_from_tar(
            {
                "main.tex": make_figure_tex(image_target="figure.svg"),
                "figure.svg": b"<svg><script>alert(1)</script></svg>",
                "figures/later.png": PNG_BYTES,
            }
        )
        is None
    )


@pytest.mark.parametrize(
    ("asset_name", "content"),
    [
        ("figure.png", b"\x89PNG\r\n\x1a\ntruncated"),
        ("figure.png", JPEG_BYTES),
        ("figure.jpg", PNG_BYTES),
    ],
)
def test_rejects_malformed_or_extension_mismatched_raster_assets(
    asset_name: str,
    content: bytes,
) -> None:
    assert (
        extract_from_tar(
            {
                "main.tex": make_figure_tex(image_target=asset_name),
                asset_name: content,
                "figures/later.png": PNG_BYTES,
            }
        )
        is None
    )


@pytest.mark.parametrize(
    ("name", "value", "asset_name", "content"),
    [
        ("max_image_dimension", 1, "figure.png", PNG_BYTES),
        ("max_image_pixels", 3, "figure.png", PNG_BYTES),
        ("max_image_frames", 1, "figure.gif", make_image("GIF", frames=2)),
        (
            "max_asset_bytes",
            len(PNG_BYTES) - 1,
            "figure.png",
            PNG_BYTES,
        ),
    ],
)
def test_rejects_source_raster_assets_over_decode_bounds(
    name: str,
    value: int,
    asset_name: str,
    content: bytes,
) -> None:
    assert (
        extract_from_tar(
            {
                "main.tex": make_figure_tex(image_target=asset_name),
                asset_name: content,
                "figures/later.png": PNG_BYTES,
            },
            **{name: value},
        )
        is None
    )


def test_extractor_and_store_share_the_same_default_asset_byte_limit() -> None:
    extractor, client = make_extractor(
        handler=httpx.MockTransport(lambda _request: httpx.Response(404))
    )
    try:
        assert extractor.max_asset_bytes == DEFAULT_MAX_ASSET_BYTES
    finally:
        client.close()


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
        unsafe_member("C:unsafe"),
        unsafe_member("https:archive"),
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


@pytest.mark.parametrize("format_", [tarfile.PAX_FORMAT, tarfile.GNU_FORMAT])
def test_rejects_archive_metadata_over_global_uncompressed_cap(
    format_: int,
) -> None:
    long_component = "m" * 4_000
    body = make_tar(
        {
            f"{long_component}/main.tex": make_figure_tex(),
            f"{long_component}/figures/model.png": PNG_BYTES,
            f"{long_component}/figures/later.png": PNG_BYTES,
        },
        format_=format_,
    )

    extractor, client = make_extractor(
        body,
        max_compressed_bytes=len(body),
        max_archive_bytes=2_000,
    )
    try:
        assert extractor.extract(ARXIV_ID, VERSION) is None
    finally:
        client.close()


@pytest.mark.parametrize("mode", ["w:bz2", "w:xz"])
def test_rejects_unsupported_compressed_tar_formats(mode: str) -> None:
    body = make_tar(
        {
            "main.tex": make_figure_tex(),
            "figures/model.png": PNG_BYTES,
            "figures/later.png": PNG_BYTES,
        },
        mode=mode,
    )

    extractor, client = make_extractor(body)
    try:
        assert extractor.extract(ARXIV_ID, VERSION) is None
    finally:
        client.close()


def test_accepts_raw_tar_within_global_archive_cap() -> None:
    body = make_tar(
        {
            "main.tex": make_figure_tex(),
            "figures/model.png": PNG_BYTES,
            "figures/later.png": PNG_BYTES,
        },
        mode="w",
    )
    extractor, client = make_extractor(body, max_archive_bytes=len(body))
    try:
        assert extractor.extract(ARXIV_ID, VERSION) is not None
    finally:
        client.close()


@pytest.mark.parametrize("error_type", [ValueError, OverflowError])
def test_tar_parser_value_and_overflow_errors_are_permanent_failures(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
) -> None:
    body = make_tar(
        {
            "main.tex": make_figure_tex(),
            "figures/model.png": PNG_BYTES,
        }
    )

    def fail_open(*_args: object, **_kwargs: object) -> None:
        raise error_type("malformed sparse or PAX metadata")

    monkeypatch.setattr(tarfile, "open", fail_open)
    extractor, client = make_extractor(body)
    try:
        assert extractor.extract(ARXIV_ID, VERSION) is None
    finally:
        client.close()


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


@pytest.mark.parametrize("target", ["C:section", "https:section"])
def test_rejects_drive_relative_or_scheme_like_include_target(
    target: str,
) -> None:
    main = rf"""
\documentclass{{article}}
\begin{{document}}
\input{{{target}}}
\end{{document}}
""".encode()
    included = rb"""
\begin{figure}
\includegraphics{figure.png}
\caption{Unsafe include target.}
\end{figure}
"""

    assert (
        extract_from_tar(
            {
                "main.tex": main,
                f"{target}.tex": included,
                "figure.png": PNG_BYTES,
            }
        )
        is None
    )


@pytest.mark.parametrize("target", ["C:figure.png", "https:figure.png"])
def test_rejects_drive_relative_or_scheme_like_asset_target(
    target: str,
) -> None:
    assert (
        extract_from_tar(
            {
                "main.tex": make_figure_tex(image_target=target),
                target: PNG_BYTES,
                "figures/later.png": b"later",
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


def test_returns_none_for_unknown_control_sequence_beside_direct_image() -> None:
    main = rb"""
\documentclass{article}
\begin{document}
\begin{figure}
\centering
\includegraphics{figure.png}
\renderpanel{other.png}
\caption{An ambiguous macro-driven layout.}
\end{figure}
\end{document}
"""

    assert (
        extract_from_tar(
            {
                "main.tex": main,
                "figure.png": PNG_BYTES,
                "other.png": b"other",
            }
        )
        is None
    )


@pytest.mark.parametrize(
    "main",
    [
        rb"""
\documentclass{article}
\begin{document}
\begin{figure}
\verb|\includegraphics{figure.png}|
\caption{A fake verb image command.}
\end{figure}
\end{document}
""",
        rb"""
\documentclass{article}
\begin{document}
\begin{figure}
\\includegraphics{figure.png}
\caption{An escaped image command.}
\end{figure}
\end{document}
""",
        rb"""
\documentclass{article}
\begin{document}
\verb|\begin{figure}\includegraphics{figure.png}\caption{Fake.}\end{figure}|
\end{document}
""",
        rb"""
\documentclass{article}
\begin{document}
\begin{verbatim}
\begin{figure}
\includegraphics{figure.png}
\caption{Fake environment.}
\end{figure}
\end{verbatim}
\end{document}
""",
        rb"""
\documentclass{article}
\begin{document}
\\begin{figure}
\includegraphics{figure.png}
\caption{Escaped environment.}
\\end{figure}
\end{document}
""",
    ],
)
def test_returns_none_for_commands_in_nonliteral_tex_contexts(
    main: bytes,
) -> None:
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
    "declarations",
    [
        rb"\documentclass\klass",
        rb"\documentclass{article}\documentclass{report}",
        rb"\documentclass{article}\documentclass\klass",
        rb"\\documentclass{article}",
    ],
)
def test_requires_exactly_one_literal_documentclass_declaration(
    declarations: bytes,
) -> None:
    main = (
        declarations
        + rb"""
\begin{document}
\begin{figure}
\includegraphics{figure.png}
\caption{Invalid main document.}
\end{figure}
\end{document}
"""
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


@pytest.mark.parametrize(
    "declaration",
    [
        rb"\newcommand{\bogus}{\documentclass{article}}",
        rb"{\documentclass{article}}",
    ],
)
def test_requires_documentclass_declaration_at_top_level(
    declaration: bytes,
) -> None:
    main = (
        declaration
        + rb"""
\begin{document}
\begin{figure}
\includegraphics{figure.png}
\caption{A non-top-level main declaration.}
\end{figure}
\end{document}
"""
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


def test_returns_none_when_preamble_redefines_includegraphics() -> None:
    main = rb"""
\documentclass{article}
\renewcommand{\includegraphics}[2][]{}
\begin{document}
\begin{figure}
\includegraphics{figure.png}
\caption{The command no longer renders this image.}
\end{figure}
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


def test_returns_none_when_preamble_redefines_implicit_figure_semantics() -> None:
    main = rb"""
\documentclass{article}
\renewcommand{\thefigure}{A}
\begin{document}
\begin{figure}
\includegraphics{figure.png}
\caption{This is not numbered Figure 1.}
\end{figure}
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


def test_returns_none_when_dynamic_control_advances_figure_before_candidate() -> None:
    main = rb"""
\documentclass{article}
\newcommand{\advancefigure}{\csname stepcounter\endcsname{figure}}
\begin{document}
\advancefigure
\begin{figure}
\includegraphics{figure.png}
\caption{This is rendered as Figure 2, never Figure 1.}
\end{figure}
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


def test_returns_none_when_conditional_hides_a_fake_first_figure() -> None:
    main = rb"""
\documentclass{article}
\begin{document}
\iffalse
\begin{figure}
\includegraphics{fake.png}
\caption{Inactive fake Figure.}
\end{figure}
\fi
\begin{figure}
\includegraphics{real.png}
\caption{The real Figure.}
\end{figure}
\end{document}
"""

    assert (
        extract_from_tar(
            {
                "main.tex": main,
                "fake.png": b"fake",
                "real.png": PNG_BYTES,
            }
        )
        is None
    )


def test_returns_none_when_control_symbol_is_redefined_to_add_an_image() -> None:
    main = rb"""
\documentclass{article}
\def\!{\includegraphics{other.png}}
\begin{document}
\begin{figure}
\includegraphics{figure.png}
\!
\caption{An ambiguous control-symbol layout.}
\end{figure}
\end{document}
"""

    assert (
        extract_from_tar(
            {
                "main.tex": main,
                "figure.png": PNG_BYTES,
                "other.png": b"other",
            }
        )
        is None
    )


def test_returns_none_for_unknown_control_symbol_in_figure() -> None:
    main = rb"""
\documentclass{article}
\begin{document}
\begin{figure}
\includegraphics{figure.png}
\?
\caption{An unknown control-symbol layout.}
\end{figure}
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


def test_macro_definition_after_first_figure_does_not_change_candidate() -> None:
    main = rb"""
\documentclass{article}
\begin{document}
\begin{figure}
\includegraphics{figure.png}
\caption{The direct first Figure.}
\end{figure}
\newcommand{\later}{not relevant to the first Figure}
\end{document}
"""

    candidate = extract_from_tar(
        {
            "main.tex": main,
            "figure.png": PNG_BYTES,
        }
    )

    assert candidate is not None
    assert candidate.caption == "The direct first Figure."


@pytest.mark.parametrize(
    "prefix",
    [
        r"\setcounter{figure}{5}",
        r"\addtocounter{figure}{2}",
        r"\counterwithin{figure}{section}",
        r"\counterwithout{figure}{section}",
        r"\numberwithin{figure}{section}",
        r"\numberwithout{figure}{section}",
        r"\stepcounter{figure}",
        r"\refstepcounter{figure}",
    ],
)
def test_returns_none_for_counter_mutation_before_candidate(
    prefix: str,
) -> None:
    main = (
        rf"""
\documentclass{{article}}
{prefix}
\begin{{document}}
\begin{{figure}}
\includegraphics{{figure.png}}
\caption{{Counter semantics are ambiguous.}}
\end{{figure}}
\end{{document}}
""".encode()
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


def test_captionof_figure_before_candidate_cannot_publish_figure_two() -> None:
    main = rb"""
\documentclass{article}
\usepackage{caption}
\begin{document}
\captionof{figure}{This consumes the Figure 1 counter.}
\begin{figure}
\includegraphics{figure-two.png}
\caption{This is rendered as Figure 2, never Figure 1.}
\end{figure}
\end{document}
"""

    assert (
        extract_from_tar(
            {
                "main.tex": main,
                "figure-two.png": PNG_BYTES,
            }
        )
        is None
    )


def test_includeonly_cannot_make_excluded_include_supply_candidate() -> None:
    main = rb"""
\documentclass{article}
\includeonly{sections/kept}
\begin{document}
\include{sections/excluded}
\end{document}
"""
    excluded = rb"""
\begin{figure}
\includegraphics{figure.png}
\caption{This file is excluded by includeonly.}
\end{figure}
"""

    assert (
        extract_from_tar(
            {
                "main.tex": main,
                "sections/excluded.tex": excluded,
                "figure.png": PNG_BYTES,
            }
        )
        is None
    )


@pytest.mark.parametrize(
    "trailing",
    [
        rb"""
\begin{figure}
\includegraphics{figure.png}
\caption{This Figure is after the hard input terminator.}
\end{figure}
""",
        rb"\input{sections/figure}",
    ],
)
def test_endinput_prevents_trailing_content_or_include_from_supplying_candidate(
    trailing: bytes,
) -> None:
    main = (
        rb"""
\documentclass{article}
\begin{document}
\endinput
"""
        + trailing
        + rb"""
\end{document}
"""
    )
    included = rb"""
\begin{figure}
\includegraphics{figure.png}
\caption{This included Figure is after the hard input terminator.}
\end{figure}
"""

    assert (
        extract_from_tar(
            {
                "main.tex": main,
                "sections/figure.tex": included,
                "figure.png": PNG_BYTES,
            }
        )
        is None
    )


def test_endinput_is_file_local_when_inlining_tex() -> None:
    main = rb"""
\documentclass{article}
\begin{document}
\input{sections/intro}
\begin{figure}
\includegraphics{figure.png}
\caption{The direct Figure from the main file.}
\end{figure}
\end{document}
"""
    included = rb"""
\endinput
\begin{figure}
\includegraphics{../fake.png}
\caption{Trailing included content must stay ignored.}
\end{figure}
"""

    candidate = extract_from_tar(
        {
            "main.tex": main,
            "sections/intro.tex": included,
            "figure.png": PNG_BYTES,
            "fake.png": PNG_BYTES,
        }
    )

    assert candidate is not None
    assert candidate.caption == "The direct Figure from the main file."


@pytest.mark.parametrize("dependency", ["local.sty", "local.cls", "LOCAL.STY"])
def test_returns_none_when_archive_contains_local_tex_semantic_dependency(
    dependency: str,
) -> None:
    assert (
        extract_from_tar(
            {
                "main.tex": make_figure_tex(),
                dependency: rb"\renewcommand{\includegraphics}[1]{}",
                "figures/model.png": PNG_BYTES,
                "figures/later.png": PNG_BYTES,
            }
        )
        is None
    )


@pytest.mark.parametrize(
    "semantic_control",
    [
        rb"\setcounter{figure}{5}",
        rb"\captionof{figure}{This consumes Figure 1.}",
        rb"\renewcommand{\thefigure}{A}",
        rb"\newcommand{\figure}{changed}",
        rb"\newenvironment{figure}{}{}",
    ],
)
def test_returns_none_when_local_class_changes_figure_selection(
    semantic_control: bytes,
) -> None:
    assert (
        extract_from_tar(
            {
                "main.tex": make_figure_tex(),
                "local.cls": semantic_control,
                "figures/model.png": PNG_BYTES,
                "figures/later.png": PNG_BYTES,
            }
        )
        is None
    )


@pytest.mark.parametrize(
    "alias_definition",
    [
        rb"\let\advancefigure\setcounter",
        rb"\let \advancefigure = \setcounter",
        rb"\futurelet\advancefigure\relax\setcounter",
    ],
)
def test_returns_none_when_local_class_aliases_figure_selection_control(
    alias_definition: bytes,
) -> None:
    semantic_dependency = (
        alias_definition + rb"\advancefigure{figure}{5}"
    )

    assert (
        extract_from_tar(
            {
                "main.tex": make_figure_tex(),
                "local.cls": semantic_dependency,
                "figures/model.png": PNG_BYTES,
                "figures/later.png": PNG_BYTES,
            }
        )
        is None
    )


def test_returns_none_when_local_class_dynamically_advances_figure() -> None:
    main = rb"""
\documentclass{local}
\begin{document}
\advancefigure
\begin{figure}
\includegraphics{figure.png}
\caption{This is rendered as Figure 2, never Figure 1.}
\end{figure}
\end{document}
"""
    local_class = (
        rb"\newcommand{\advancefigure}"
        rb"{\csname stepcounter\endcsname{figure}}"
    )

    assert (
        extract_from_tar(
            {
                "main.tex": main,
                "local.cls": local_class,
                "figure.png": PNG_BYTES,
            }
        )
        is None
    )


@pytest.mark.parametrize(
    "main",
    [
        rb"""
\documentclass{article}
\begin{figure}
\includegraphics{figure.png}
\caption{A preamble Figure.}
\end{figure}
\begin{document}
\end{document}
""",
        rb"""
\documentclass{article}
\begin{document}
\begin{minipage}{\textwidth}
\begin{figure}
\includegraphics{figure.png}
\caption{A nested Figure.}
\end{figure}
\end{minipage}
\end{document}
""",
        rb"""
\documentclass{article}
\begin{document}
\newcommand{\wrapped}{%
  \begin{figure}
  \includegraphics{figure.png}
  \caption{A command-nested Figure.}
  \end{figure}
}
\end{document}
""",
        rb"""
\documentclass{article}
\begin{document}
\begin{figure}
\resizebox{\textwidth}{!}{\includegraphics{figure.png}}
\caption{A nested image command.}
\end{figure}
\end{document}
""",
        rb"""
\documentclass{article}
\begin{document}
\begin{figure}
\includegraphics{figure.png}
\caption[Short]{\parbox{\textwidth}{A nested caption command.}}
\end{figure}
\end{document}
""",
    ],
)
def test_candidate_and_required_commands_must_be_in_direct_document_context(
    main: bytes,
) -> None:
    assert (
        extract_from_tar(
            {
                "main.tex": main,
                "figure.png": PNG_BYTES,
            }
        )
        is None
    )


def test_escaped_verbatim_end_does_not_expose_fake_figure_commands() -> None:
    main = rb"""
\documentclass{article}
\begin{document}
\begin{verbatim}
\\end{verbatim}
\begin{figure}
\includegraphics{figure.png}
\caption{Still verbatim, with no literal terminator.}
\end{figure}
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


def test_tex_lexer_has_a_stable_linear_scan_work_bound() -> None:
    repeated = (
        "\\\\\\% escaped percent \\\\begin{figure} "
        "\\verb|\\includegraphics{fake.png}| % comment\n"
    )
    text = repeated * 20_000 + make_figure_tex().decode()

    lexed = _lex_tex(text)

    assert lexed is not None
    assert lexed.scan_steps <= len(text) * 3


@pytest.mark.parametrize(
    "caption",
    [
        "NUL \x00 control.",
        "ESC \x1b control.",
        "BEL \x07 control.",
        "DEL \x7f control.",
        "C1 \u0085 control.",
        "Bidi override \u202e control.",
        "Bidi isolate \u2066 control.",
        "Zero width \u200b control.",
        "Byte order \ufeff mark.",
    ],
)
def test_returns_none_for_unsafe_caption_control_or_format_characters(
    caption: str,
) -> None:
    assert (
        extract_from_tar(
            {
                "main.tex": make_figure_tex(caption=caption),
                "figures/model.png": PNG_BYTES,
                "figures/later.png": b"later",
            }
        )
        is None
    )


def test_normalizes_safe_caption_whitespace() -> None:
    candidate = extract_from_tar(
        {
            "main.tex": make_figure_tex(
                caption="Line one.\n\tLine two."
            ),
            "figures/model.png": PNG_BYTES,
            "figures/later.png": b"later",
        }
    )

    assert candidate is not None
    assert candidate.caption == "Line one. Line two."


def test_renders_single_page_source_pdf_asset_to_png() -> None:
    candidate = extract_from_tar(
        {
            "main.tex": make_figure_tex(image_target="figure.pdf"),
            "figure.pdf": make_pdf_asset(),
            "figures/later.png": b"later",
        }
    )

    assert candidate is not None
    assert candidate.caption == "The model architecture."
    assert candidate.extension == "png"
    assert candidate.source == "arxiv_source"
    assert candidate.source_url == SOURCE_URL
    with Image.open(io.BytesIO(candidate.content)) as image:
        image.load()
        assert image.format == "PNG"
        assert image.width == pytest.approx(120 * 300 / 72, abs=1)
        assert image.height == pytest.approx(80 * 300 / 72, abs=1)


@pytest.mark.parametrize(
    "content",
    [
        b"%PDF-malformed",
        make_pdf_asset(pages=2),
    ],
)
def test_rejects_invalid_or_multi_page_source_pdf_asset(content: bytes) -> None:
    assert (
        extract_from_tar(
            {
                "main.tex": make_figure_tex(image_target="figure.pdf"),
                "figure.pdf": content,
                "figures/later.png": b"later",
            }
        )
        is None
    )


def test_rejects_source_pdf_asset_over_byte_limit() -> None:
    content = make_pdf_asset()
    assert (
        extract_from_tar(
            {
                "main.tex": make_figure_tex(image_target="figure.pdf"),
                "figure.pdf": content,
                "figures/later.png": b"later",
            },
            max_asset_bytes=len(content) - 1,
        )
        is None
    )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("max_image_dimension", 499),
        ("max_image_pixels", 166_999),
    ],
)
def test_rejects_source_pdf_render_over_pixel_bounds(name: str, value: int) -> None:
    assert (
        extract_from_tar(
            {
                "main.tex": make_figure_tex(image_target="figure.pdf"),
                "figure.pdf": make_pdf_asset(),
                "figures/later.png": b"later",
            },
            **{name: value},
        )
        is None
    )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("max_pdf_page_dimension_points", 100),
        ("max_pdf_objects", 1),
        ("max_pdf_text_chars", 2),
    ],
)
def test_rejects_source_pdf_over_page_or_object_preflight_cap(
    name: str,
    value: int,
) -> None:
    assert (
        extract_from_tar(
            {
                "main.tex": make_figure_tex(image_target="figure.pdf"),
                "figure.pdf": make_pdf_asset(),
                "figures/later.png": b"later",
            },
            **{name: value},
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


def test_follows_official_src_redirect_for_the_same_exact_source_identity() -> None:
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
        if request.url.path.startswith("/e-print/"):
            return httpx.Response(
                301,
                headers={
                    "location": f"/src/{ARXIV_ID}v{VERSION}",
                },
            )
        return httpx.Response(200, content=body)

    extractor, client = make_extractor(handler=httpx.MockTransport(handler))
    try:
        candidate = extractor.extract(ARXIV_ID, VERSION)
    finally:
        client.close()

    assert candidate is not None
    assert [request.url.path for request in requests] == [
        f"/e-print/{ARXIV_ID}v{VERSION}",
        f"/src/{ARXIV_ID}v{VERSION}",
    ]


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
        f"https://arxiv.org/src/{ARXIV_ID}v2",
        "https://arxiv.org/src/2607.99999v1",
        f"https://arxiv.org/src/{ARXIV_ID}v{VERSION}/other",
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


@pytest.mark.parametrize("status_code", [408, 425, 429, 500, 503])
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


class ReportedOversizedChunk(bytes):
    def __len__(self) -> int:
        return super().__len__() + 1


class ReportedOversizedStream(httpx.SyncByteStream):
    def __init__(self, content: bytes) -> None:
        self.content = content

    def __iter__(self):
        yield ReportedOversizedChunk(self.content)


def test_checks_stream_chunk_against_remaining_budget_before_extend() -> None:
    body = make_tar(
        {
            "main.tex": make_figure_tex(),
            "figures/model.png": PNG_BYTES,
            "figures/later.png": b"later",
        }
    )
    extractor, client = make_extractor(
        handler=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                stream=ReportedOversizedStream(body),
            )
        ),
        max_compressed_bytes=len(body),
    )
    try:
        assert extractor.extract(ARXIV_ID, VERSION) is None
    finally:
        client.close()


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
        ("max_archive_bytes", 0),
        ("max_redirects", -1),
        ("max_members", 0),
        ("max_member_bytes", 0),
        ("max_total_uncompressed_bytes", 0),
        ("max_include_depth", -1),
        ("max_tex_bytes", 0),
        ("max_asset_bytes", 0),
        ("max_image_dimension", 0),
        ("max_image_pixels", 0),
        ("max_image_frames", 0),
        ("max_pdf_page_dimension_points", 0),
        ("max_pdf_objects", 0),
        ("max_pdf_text_chars", 0),
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
