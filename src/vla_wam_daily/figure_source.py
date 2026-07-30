import gzip
import io
import logging
import math
import re
import tarfile
import unicodedata
import warnings
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlsplit

import httpx
from PIL import Image, UnidentifiedImageError

from vla_wam_daily.figure_pdf_render import render_single_page_pdf
from vla_wam_daily.figure_recovery_types import (
    DEFAULT_MAX_ASSET_BYTES,
    RecoveredExtension,
    RecoveredFigure,
    TransientRecoveryError,
)
from vla_wam_daily.figures import figure_cache_key
from vla_wam_daily.models import ARXIV_FIGURE_HOSTS

LOGGER = logging.getLogger(__name__)
_DOCUMENT_CLASS_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_SCHEME_PREFIX_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_UNSAFE_FIGURE_RE = re.compile(
    r"""
    \\begin\s*\{(?:tikzpicture|subfigure|subtable|minipage|tabular)\}
    |\\(?:tikz|write18|openin|openout|closein|closeout|read|readline|write)
      (?![A-Za-z@])
    |\\(?:special|directlua|pdfshellescape|newcommand|renewcommand|providecommand)
      (?![A-Za-z@])
    |\\(?:immediate|expandafter|csname|catcode|def)(?![A-Za-z@])
    |\\(?:subfloat|subcaption|subcaptionbox|parbox)(?![A-Za-z@])
    """,
    re.VERBOSE,
)
_SUPPORTED_ASSET_EXTENSIONS: dict[str, RecoveredExtension] = {
    ".png": "png",
    ".jpg": "jpg",
    ".jpeg": "jpg",
    ".webp": "webp",
    ".gif": "gif",
    ".svg": "svg",
}
_PIL_FORMATS = {
    "png": "PNG",
    "jpg": "JPEG",
    "webp": "WEBP",
    "gif": "GIF",
}
_SAFE_CAPTION_COMMANDS = frozenset(
    {
        "textbf",
        "textit",
        "textsl",
        "textsc",
        "textsf",
        "textrm",
        "texttt",
        "emph",
        "mathrm",
        "mathbf",
        "mathit",
        "mathsf",
        "mathtt",
        "mbox",
    }
)
_ESCAPED_CAPTION_CHARACTERS = frozenset("%&_#$\\{}")
_ALLOWED_FIGURE_CONTROLS = frozenset(
    {
        "caption",
        "centering",
        "columnwidth",
        "enspace",
        "hfill",
        "hspace",
        "includegraphics",
        "label",
        "linewidth",
        "noindent",
        "quad",
        "qquad",
        "raggedleft",
        "raggedright",
        "smallskip",
        "medskip",
        "bigskip",
        "textwidth",
        "vfill",
        "vspace",
        "tiny",
        "scriptsize",
        "footnotesize",
        "small",
        "normalsize",
        "large",
        "Large",
        "LARGE",
        "huge",
        "Huge",
        *_SAFE_CAPTION_COMMANDS,
    }
)
_ALLOWED_FIGURE_CONTROL_SYMBOLS = frozenset(
    {
        "\\",
        " ",
        "\t",
        "\r",
        "\n",
        ",",
        ";",
        ":",
        "!",
        "%",
        "&",
        "_",
        "#",
        "$",
        "{",
        "}",
    }
)
_MACRO_DEFINITION_CONTROLS = frozenset(
    {
        "DeclareDocumentCommand",
        "DeclareRobustCommand",
        "NewDocumentCommand",
        "ProvideDocumentCommand",
        "RenewDocumentCommand",
        "def",
        "edef",
        "gdef",
        "let",
        "futurelet",
        "newcommand",
        "newenvironment",
        "providecommand",
        "renewcommand",
        "renewenvironment",
        "xdef",
    }
)
_CONDITIONAL_CONTROLS = frozenset({"else", "fi", "or", "unless"})


class _RejectedArchive(RuntimeError):
    pass


def _safe_archive_path(name: str, *, directory: bool) -> PurePosixPath:
    if not name or "\x00" in name or "\\" in name:
        raise _RejectedArchive
    normalized = name.rstrip("/") if directory else name
    if not normalized:
        raise _RejectedArchive
    raw_parts = normalized.split("/")
    if any(not part for part in raw_parts):
        raise _RejectedArchive
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or _SCHEME_PREFIX_RE.match(path.parts[0])
    ):
        raise _RejectedArchive
    return path


def _read_archive(
    body: bytes,
    *,
    max_archive_bytes: int,
    max_members: int,
    max_member_bytes: int,
    max_total_uncompressed_bytes: int,
    max_tex_bytes: int,
) -> dict[PurePosixPath, bytes] | None:
    files: dict[PurePosixPath, bytes] = {}
    seen_paths: set[PurePosixPath] = set()
    total_bytes = 0
    total_tex_bytes = 0
    try:
        if body.startswith(b"\x1f\x8b"):
            uncompressed = bytearray()
            with gzip.GzipFile(fileobj=io.BytesIO(body), mode="rb") as compressed:
                while True:
                    remaining = max_archive_bytes - len(uncompressed)
                    chunk = compressed.read(min(64 * 1024, remaining + 1))
                    if not chunk:
                        break
                    if len(chunk) > remaining:
                        raise _RejectedArchive
                    uncompressed.extend(chunk)
            archive_bytes = bytes(uncompressed)
        elif body.startswith((b"BZh", b"\xfd7zXZ\x00")):
            raise _RejectedArchive
        else:
            if len(body) > max_archive_bytes:
                raise _RejectedArchive
            archive_bytes = body
        if not archive_bytes:
            raise _RejectedArchive

        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
            for member_count, member in enumerate(archive, start=1):
                if member_count > max_members:
                    raise _RejectedArchive
                path = _safe_archive_path(
                    member.name,
                    directory=member.isdir(),
                )
                if path in seen_paths:
                    raise _RejectedArchive
                seen_paths.add(path)
                if member.isdir():
                    if member.size != 0:
                        raise _RejectedArchive
                    continue
                if not member.isreg():
                    raise _RejectedArchive
                if member.size < 0 or member.size > max_member_bytes:
                    raise _RejectedArchive
                total_bytes += member.size
                if total_bytes > max_total_uncompressed_bytes:
                    raise _RejectedArchive
                if path.suffix.casefold() == ".tex":
                    total_tex_bytes += member.size
                    if total_tex_bytes > max_tex_bytes:
                        raise _RejectedArchive
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise _RejectedArchive
                content = extracted.read(member.size + 1)
                if len(content) != member.size:
                    raise _RejectedArchive
                files[path] = content
    except (
        EOFError,
        OSError,
        tarfile.TarError,
        UnicodeError,
        ValueError,
        OverflowError,
        _RejectedArchive,
    ):
        return None
    return files


def _decode_tex_files(
    files: dict[PurePosixPath, bytes],
) -> dict[PurePosixPath, "_LexedTex"] | None:
    decoded: dict[PurePosixPath, _LexedTex] = {}
    try:
        for path, content in files.items():
            if path.suffix.casefold() == ".tex":
                lexed = _lex_tex(content.decode("utf-8-sig"))
                if lexed is None:
                    return None
                decoded[path] = lexed
    except UnicodeDecodeError:
        return None
    return decoded


@dataclass(frozen=True)
class _ControlToken:
    word: str | None
    symbol: str | None
    start: int
    end: int
    brace_depth: int
    bracket_depth: int
    environment_depth: int
    current_environment: str | None
    parent_environment: str | None
    document_active: bool
    environment_argument: str | None = None
    argument_end: int | None = None


@dataclass(frozen=True)
class _LexedTex:
    text: str
    controls: tuple[_ControlToken, ...]
    scan_steps: int


_VERBATIM_ENVIRONMENTS = frozenset(
    {"verbatim", "verbatim*", "Verbatim", "Verbatim*", "lstlisting", "minted"}
)
_UNSUPPORTED_INLINE_VERBATIM = frozenset(
    {"lstinline", "mintinline", "SaveVerb"}
)


def _mask_range(characters: list[str], start: int, end: int) -> None:
    for index in range(start, end):
        if characters[index] not in "\r\n":
            characters[index] = " "


def _literal_environment_argument(
    text: str,
    position: int,
) -> tuple[str, int] | None:
    position = _skip_whitespace(text, position)
    if position >= len(text) or text[position] != "{":
        return None
    end = text.find("}", position + 1)
    if end < 0:
        return None
    name = text[position + 1 : end]
    if (
        not name
        or any(character in name for character in "\\{}%\r\n")
        or not all(character.isalnum() or character in "*@_-" for character in name)
    ):
        return None
    return name, end + 1


def _verbatim_environment_end(
    text: str,
    position: int,
    environment: str,
) -> int | None:
    suffix = f"end{{{environment}}}"
    while position < len(text):
        if text[position] != "\\":
            position += 1
            continue
        run_start = position
        while position < len(text) and text[position] == "\\":
            position += 1
        run_length = position - run_start
        if run_length % 2 == 1 and text.startswith(suffix, position):
            return position + len(suffix)
    return None


def _lex_tex(text: str) -> _LexedTex | None:
    characters = list(text)
    controls: list[_ControlToken] = []
    environments: list[str] = []
    brace_depth = 0
    bracket_depth = 0
    index = 0
    scan_steps = 0

    while index < len(text):
        scan_steps += 1
        character = text[index]
        if character == "%":
            end = index
            while end < len(text) and text[end] not in "\r\n":
                end += 1
            scan_steps += end - index
            _mask_range(characters, index, end)
            index = end
            continue
        if character != "\\":
            if character == "{":
                brace_depth += 1
            elif character == "}":
                if brace_depth == 0:
                    return None
                brace_depth -= 1
            elif character == "[":
                bracket_depth += 1
            elif character == "]":
                if bracket_depth == 0:
                    return None
                bracket_depth -= 1
            index += 1
            continue

        start = index
        index += 1
        if index >= len(text):
            return None
        if text[index].isalpha() or text[index] == "@":
            word_start = index
            while index < len(text) and (
                text[index].isalpha() or text[index] == "@"
            ):
                index += 1
            word = text[word_start:index]
            symbol = None
        else:
            word = None
            symbol = text[index]
            index += 1

        current_environment = environments[-1] if environments else None
        parent_environment = (
            environments[-2] if len(environments) >= 2 else None
        )
        document_active = bool(
            environments and environments[0] == "document"
        )

        if word == "endinput":
            scan_steps += len(text) - start
            _mask_range(characters, start, len(text))
            break
        if word in _UNSUPPORTED_INLINE_VERBATIM:
            return None
        if word == "verb":
            if index < len(text) and text[index] == "*":
                index += 1
            if index >= len(text) or text[index].isspace():
                return None
            delimiter = text[index]
            closing = text.find(delimiter, index + 1)
            newline = text.find("\n", index + 1)
            if closing < 0 or (newline >= 0 and newline < closing):
                return None
            closing += 1
            scan_steps += closing - start
            _mask_range(characters, start, closing)
            index = closing
            continue

        environment_argument: str | None = None
        argument_end: int | None = None
        if word in {"begin", "end"}:
            parsed_environment = _literal_environment_argument(text, index)
            if parsed_environment is None:
                return None
            environment_argument, argument_end = parsed_environment
            if word == "begin" and environment_argument in _VERBATIM_ENVIRONMENTS:
                closing_end = _verbatim_environment_end(
                    text,
                    argument_end,
                    environment_argument,
                )
                if closing_end is None:
                    return None
                scan_steps += closing_end - start
                _mask_range(characters, start, closing_end)
                index = closing_end
                continue

        controls.append(
            _ControlToken(
                word=word,
                symbol=symbol,
                start=start,
                end=index,
                brace_depth=brace_depth,
                bracket_depth=bracket_depth,
                environment_depth=len(environments),
                current_environment=current_environment,
                parent_environment=parent_environment,
                document_active=document_active,
                environment_argument=environment_argument,
                argument_end=argument_end,
            )
        )

        if word == "begin":
            assert environment_argument is not None
            assert argument_end is not None
            environments.append(environment_argument)
            scan_steps += argument_end - index
            index = argument_end
        elif word == "end":
            assert environment_argument is not None
            assert argument_end is not None
            if not environments or environments[-1] != environment_argument:
                return None
            environments.pop()
            scan_steps += argument_end - index
            index = argument_end

    return _LexedTex(
        text="".join(characters),
        controls=tuple(controls),
        scan_steps=scan_steps,
    )


def _mask_verbatim_like(text: str) -> str | None:
    lexed = _lex_tex(text)
    return None if lexed is None else lexed.text


def _is_within(path: PurePosixPath, root: PurePosixPath) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _literal_local_target(
    root: PurePosixPath,
    target: str,
) -> PurePosixPath | None:
    value = target.strip()
    if (
        not value
        or "\x00" in value
        or "\\" in value
        or "{" in value
        or "}" in value
    ):
        return None
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or _SCHEME_PREFIX_RE.match(path.parts[0])
    ):
        return None
    candidate = root / path
    if not _is_within(candidate, root):
        return None
    return candidate


def _resolve_tex_include(
    tex_files: dict[PurePosixPath, _LexedTex],
    root: PurePosixPath,
    target: str,
) -> PurePosixPath | None:
    candidate = _literal_local_target(root, target)
    if candidate is None:
        return None
    if candidate.suffix:
        candidates = [candidate] if candidate.suffix.casefold() == ".tex" else []
    else:
        candidates = [candidate.with_suffix(".tex")]
    matches = [path for path in candidates if path in tex_files]
    return matches[0] if len(matches) == 1 else None


def _inline_tex(
    path: PurePosixPath,
    *,
    tex_files: dict[PurePosixPath, _LexedTex],
    root: PurePosixPath,
    max_include_depth: int,
    max_tex_bytes: int,
    depth: int = 0,
    stack: tuple[PurePosixPath, ...] = (),
    consumed_bytes: list[int] | None = None,
) -> str | None:
    if path in stack:
        return None
    lexed = tex_files.get(path)
    if lexed is None:
        return None
    text = lexed.text
    if consumed_bytes is None:
        consumed_bytes = [0]
    consumed_bytes[0] += len(text.encode("utf-8"))
    if consumed_bytes[0] > max_tex_bytes:
        return None

    include_tokens = [
        token
        for token in lexed.controls
        if token.word in {"input", "include"}
    ]
    if include_tokens and depth >= max_include_depth:
        return None

    parts: list[str] = []
    cursor = 0
    for token in include_tokens:
        argument = _literal_command_argument_at(
            text,
            token.end,
            allow_options=False,
        )
        if argument is None:
            return None
        target, argument_end = argument
        include_path = _resolve_tex_include(tex_files, root, target)
        if include_path is None:
            return None
        included = _inline_tex(
            include_path,
            tex_files=tex_files,
            root=root,
            max_include_depth=max_include_depth,
            max_tex_bytes=max_tex_bytes,
            depth=depth + 1,
            stack=(*stack, path),
            consumed_bytes=consumed_bytes,
        )
        if included is None:
            return None
        parts.extend((text[cursor : token.start], included))
        cursor = argument_end
    parts.append(text[cursor:])
    return "".join(parts)


def _first_figure_block(
    lexed: _LexedTex,
) -> tuple[str, int, int, int, int] | None:
    figure_tokens = [
        token
        for token in lexed.controls
        if token.word in {"begin", "end"}
        and token.environment_argument in {"figure", "figure*"}
    ]
    if not figure_tokens:
        return None
    first = figure_tokens[0]
    if (
        first.word != "begin"
        or not first.document_active
        or first.environment_depth != 1
        or first.current_environment != "document"
        or first.brace_depth != 0
        or first.bracket_depth != 0
        or first.argument_end is None
    ):
        return None
    following = figure_tokens[1] if len(figure_tokens) >= 2 else None
    if (
        following is None
        or following.word != "end"
        or following.environment_argument != first.environment_argument
        or following.environment_depth != 2
        or following.current_environment != first.environment_argument
        or following.parent_environment != "document"
        or following.brace_depth != 0
        or following.bracket_depth != 0
        or following.argument_end is None
    ):
        return None
    return (
        lexed.text[first.argument_end : following.start],
        first.start,
        following.argument_end,
        first.argument_end,
        following.start,
    )


_COUNTER_MUTATION_CONTROLS = frozenset(
    {
        "addtocounter",
        "counterwithin",
        "counterwithout",
        "newcounter",
        "numberwithin",
        "numberwithout",
        "refstepcounter",
        "setcounter",
        "stepcounter",
    }
)
_AMBIGUOUS_FIGURE_SELECTION_CONTROLS = frozenset(
    {
        "captionof",
        "includeonly",
    }
)


def _has_ambiguous_semantic_control(
    controls: tuple[_ControlToken, ...],
    *,
    end: int,
) -> bool:
    for token in controls:
        if token.start >= end:
            break
        word = token.word
        if word is None:
            continue
        if (
            word in _MACRO_DEFINITION_CONTROLS
            or word in _CONDITIONAL_CONTROLS
            or word in _COUNTER_MUTATION_CONTROLS
            or word in _AMBIGUOUS_FIGURE_SELECTION_CONTROLS
            or word.startswith("if")
        ):
            return True
    return False


def _parse_delimited(
    text: str,
    position: int,
    opening: str,
    closing: str,
) -> tuple[str, int] | None:
    if position >= len(text) or text[position] != opening:
        return None
    depth = 1
    index = position + 1
    while index < len(text):
        character = text[index]
        if character == "\\":
            index += 1
            if index >= len(text):
                return None
            if text[index].isalpha() or text[index] == "@":
                while index < len(text) and (
                    text[index].isalpha() or text[index] == "@"
                ):
                    index += 1
            else:
                index += 1
            continue
        if character == opening:
            depth += 1
        elif character == closing:
            depth -= 1
            if depth == 0:
                return text[position + 1 : index], index + 1
        index += 1
    return None


def _skip_whitespace(text: str, position: int) -> int:
    while position < len(text) and text[position].isspace():
        position += 1
    return position


def _literal_command_argument_at(
    text: str,
    command_end: int,
    *,
    allow_options: bool,
) -> tuple[str, int] | None:
    position = _skip_whitespace(text, command_end)
    if allow_options and position < len(text) and text[position] == "[":
        option = _parse_delimited(text, position, "[", "]")
        if option is None:
            return None
        position = _skip_whitespace(text, option[1])
    argument = _parse_delimited(text, position, "{", "}")
    return argument


def _literal_documentclass_declarations(
    lexed: _LexedTex,
) -> list[str] | None:
    declarations: list[str] = []
    for token in lexed.controls:
        if token.word != "documentclass":
            continue
        if (
            token.brace_depth != 0
            or token.bracket_depth != 0
            or token.environment_depth != 0
        ):
            return None
        argument = _literal_command_argument_at(
            lexed.text,
            token.end,
            allow_options=True,
        )
        if argument is None:
            return None
        name = argument[0].strip()
        if _DOCUMENT_CLASS_NAME_RE.fullmatch(name) is None:
            return None
        declarations.append(name)
    return declarations


def _plain_caption_group(text: str, position: int = 0) -> tuple[str, int] | None:
    output: list[str] = []
    while position < len(text):
        character = text[position]
        if character == "\\":
            if position + 1 >= len(text):
                return None
            escaped = text[position + 1]
            if escaped in _ESCAPED_CAPTION_CHARACTERS:
                output.append(escaped)
                position += 2
                continue
            if escaped in {",", " ", ";", ":"}:
                output.append(" ")
                position += 2
                continue
            if escaped == "\\":
                output.append(" ")
                position += 2
                continue
            command_match = re.match(r"[A-Za-z@]+", text[position + 1 :])
            if command_match is None:
                return None
            command = command_match.group(0)
            if command not in _SAFE_CAPTION_COMMANDS:
                return None
            position += 1 + len(command)
            position = _skip_whitespace(text, position)
            group = _parse_delimited(text, position, "{", "}")
            if group is None:
                return None
            nested = _plain_caption_group(group[0])
            if nested is None or nested[1] != len(group[0]):
                return None
            output.append(nested[0])
            position = group[1]
            continue
        if character == "{":
            group = _parse_delimited(text, position, "{", "}")
            if group is None:
                return None
            nested = _plain_caption_group(group[0])
            if nested is None or nested[1] != len(group[0]):
                return None
            output.append(nested[0])
            position = group[1]
            continue
        if character in "}$":
            return None
        output.append(" " if character == "~" else character)
        position += 1
    return "".join(output), position


def _normalize_caption(raw_caption: str) -> str | None:
    parsed = _plain_caption_group(raw_caption)
    if parsed is None or parsed[1] != len(raw_caption):
        return None
    plain = parsed[0]
    if any(
        character not in "\t\n\r"
        and unicodedata.category(character).startswith("C")
        for character in plain
    ):
        return None
    normalized = " ".join(plain.split())
    return normalized or None


def _resolve_asset(
    files: dict[PurePosixPath, bytes],
    *,
    root: PurePosixPath,
    target: str,
    max_asset_bytes: int,
    max_image_dimension: int,
    max_image_pixels: int,
    max_image_frames: int,
    max_pdf_page_dimension_points: int,
    max_pdf_objects: int,
    max_pdf_text_chars: int,
) -> tuple[RecoveredExtension, bytes] | None:
    candidate = _literal_local_target(root, target)
    if candidate is None:
        return None
    if candidate.suffix:
        candidates = [candidate]
    else:
        candidates = [
            candidate.with_suffix(extension)
            for extension in _SUPPORTED_ASSET_EXTENSIONS
        ]
        candidates.append(candidate.with_suffix(".pdf"))
    matches = [path for path in candidates if path in files]
    if len(matches) != 1:
        return None
    asset_path = matches[0]
    extension = _SUPPORTED_ASSET_EXTENSIONS.get(asset_path.suffix.casefold())
    content = files[asset_path]
    if asset_path.suffix.casefold() == ".pdf":
        rendered = render_single_page_pdf(
            content,
            max_pdf_bytes=max_asset_bytes,
            max_page_dimension_points=max_pdf_page_dimension_points,
            max_page_objects=max_pdf_objects,
            max_text_chars=max_pdf_text_chars,
            resolution=300,
            max_output_dimension=max_image_dimension,
            max_output_pixels=max_image_pixels,
            max_output_bytes=max_asset_bytes,
        )
        return None if rendered is None else ("png", rendered)
    if (
        extension is None
        or extension == "svg"
        or not _valid_raster_asset(
            content,
            extension=extension,
            max_asset_bytes=max_asset_bytes,
            max_image_dimension=max_image_dimension,
            max_image_pixels=max_image_pixels,
            max_image_frames=max_image_frames,
        )
    ):
        return None
    return extension, content


def _valid_raster_asset(
    content: bytes,
    *,
    extension: RecoveredExtension,
    max_asset_bytes: int,
    max_image_dimension: int,
    max_image_pixels: int,
    max_image_frames: int,
) -> bool:
    expected_format = _PIL_FORMATS.get(extension)
    if (
        expected_format is None
        or not content
        or len(content) > max_asset_bytes
    ):
        return False

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if image.format != expected_format:
                    return False
                frames = getattr(image, "n_frames", 1)
                if frames < 1 or frames > max_image_frames:
                    return False
                for frame in range(frames):
                    image.seek(frame)
                    width, height = image.size
                    if (
                        width < 1
                        or height < 1
                        or width > max_image_dimension
                        or height > max_image_dimension
                        or width * height > max_image_pixels
                    ):
                        return False
                    image.load()
            with Image.open(io.BytesIO(content)) as verifier:
                verifier.verify()
    except (
        EOFError,
        OSError,
        SyntaxError,
        UnidentifiedImageError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        return False
    return True


def _extract_figure(
    files: dict[PurePosixPath, bytes],
    tex_files: dict[PurePosixPath, _LexedTex],
    *,
    main_path: PurePosixPath,
    max_include_depth: int,
    max_tex_bytes: int,
    max_asset_bytes: int,
    max_image_dimension: int,
    max_image_pixels: int,
    max_image_frames: int,
    max_pdf_page_dimension_points: int,
    max_pdf_objects: int,
    max_pdf_text_chars: int,
    source_url: str,
) -> RecoveredFigure | None:
    root = main_path.parent
    expanded = _inline_tex(
        main_path,
        tex_files=tex_files,
        root=root,
        max_include_depth=max_include_depth,
        max_tex_bytes=max_tex_bytes,
    )
    if expanded is None:
        return None
    expanded_lexed = _lex_tex(expanded)
    if expanded_lexed is None:
        return None
    block = _first_figure_block(expanded_lexed)
    if block is None:
        return None
    body, _block_start, block_end, body_start, body_end = block
    if (
        _has_ambiguous_semantic_control(
            expanded_lexed.controls,
            end=block_end,
        )
        or _UNSAFE_FIGURE_RE.search(body)
    ):
        return None

    body_controls = [
        token
        for token in expanded_lexed.controls
        if body_start <= token.start < body_end
    ]
    for token in body_controls:
        word = token.word
        symbol = token.symbol
        if word is not None:
            if word not in _ALLOWED_FIGURE_CONTROLS:
                return None
        elif symbol not in _ALLOWED_FIGURE_CONTROL_SYMBOLS:
            return None
    graphics_tokens = [
        token for token in body_controls if token.word == "includegraphics"
    ]
    caption_tokens = [
        token for token in body_controls if token.word == "caption"
    ]
    if len(graphics_tokens) != 1 or len(caption_tokens) != 1:
        return None
    required_tokens = (graphics_tokens[0], caption_tokens[0])
    if any(
        token.brace_depth != 0
        or token.bracket_depth != 0
        or token.environment_depth != 2
        or token.current_environment not in {"figure", "figure*"}
        or token.parent_environment != "document"
        or not token.document_active
        for token in required_tokens
    ):
        return None
    asset_argument = _literal_command_argument_at(
        expanded_lexed.text,
        graphics_tokens[0].end,
        allow_options=True,
    )
    caption_argument = _literal_command_argument_at(
        expanded_lexed.text,
        caption_tokens[0].end,
        allow_options=True,
    )
    if asset_argument is None or caption_argument is None:
        return None
    asset_target = asset_argument[0]
    raw_caption = caption_argument[0]
    caption = _normalize_caption(raw_caption)
    asset = _resolve_asset(
        files,
        root=root,
        target=asset_target,
        max_asset_bytes=max_asset_bytes,
        max_image_dimension=max_image_dimension,
        max_image_pixels=max_image_pixels,
        max_image_frames=max_image_frames,
        max_pdf_page_dimension_points=max_pdf_page_dimension_points,
        max_pdf_objects=max_pdf_objects,
        max_pdf_text_chars=max_pdf_text_chars,
    )
    if caption is None or asset is None:
        return None
    extension, content = asset
    return RecoveredFigure(
        caption=caption,
        extension=extension,
        content=content,
        source_url=source_url,
        source="arxiv_source",
    )


def _safe_source_redirect(
    current_url: str,
    location: str,
    *,
    expected_path: str,
) -> str | None:
    if not location.strip():
        return None
    candidate = urljoin(current_url, location)
    parsed = urlsplit(candidate)
    try:
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ARXIV_FIGURE_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or "?" in candidate
        or "#" in candidate
        or parsed.path != expected_path
    ):
        return None
    return candidate


class ArxivSourceFigureExtractor:
    def __init__(
        self,
        *,
        user_agent: str,
        timeout_seconds: float = 30,
        max_compressed_bytes: int = 50_000_000,
        max_archive_bytes: int = 160_000_000,
        max_redirects: int = 3,
        max_members: int = 2_000,
        max_member_bytes: int = 25_000_000,
        max_total_uncompressed_bytes: int = 150_000_000,
        max_include_depth: int = 8,
        max_tex_bytes: int = 10_000_000,
        max_asset_bytes: int = DEFAULT_MAX_ASSET_BYTES,
        max_image_dimension: int = 20_000,
        max_image_pixels: int = 100_000_000,
        max_image_frames: int = 16,
        max_pdf_page_dimension_points: int = 2_000,
        max_pdf_objects: int = 20_000,
        max_pdf_text_chars: int = 100_000,
        client: httpx.Client | None = None,
    ) -> None:
        if not isinstance(user_agent, str) or not user_agent.strip():
            raise ValueError("user_agent must not be blank")
        if (
            type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive")
        positive_integer_bounds = {
            "max_compressed_bytes": max_compressed_bytes,
            "max_archive_bytes": max_archive_bytes,
            "max_members": max_members,
            "max_member_bytes": max_member_bytes,
            "max_total_uncompressed_bytes": max_total_uncompressed_bytes,
            "max_tex_bytes": max_tex_bytes,
            "max_asset_bytes": max_asset_bytes,
            "max_image_dimension": max_image_dimension,
            "max_image_pixels": max_image_pixels,
            "max_image_frames": max_image_frames,
            "max_pdf_page_dimension_points": max_pdf_page_dimension_points,
            "max_pdf_objects": max_pdf_objects,
            "max_pdf_text_chars": max_pdf_text_chars,
        }
        if any(
            type(value) is not int or value < 1
            for value in positive_integer_bounds.values()
        ):
            raise ValueError("source archive byte and member limits must be positive")
        if type(max_redirects) is not int or max_redirects < 0:
            raise ValueError("max_redirects must be a nonnegative integer")
        if type(max_include_depth) is not int or max_include_depth < 0:
            raise ValueError("max_include_depth must be a nonnegative integer")

        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.max_compressed_bytes = max_compressed_bytes
        self.max_archive_bytes = max_archive_bytes
        self.max_redirects = max_redirects
        self.max_members = max_members
        self.max_member_bytes = max_member_bytes
        self.max_total_uncompressed_bytes = max_total_uncompressed_bytes
        self.max_include_depth = max_include_depth
        self.max_tex_bytes = max_tex_bytes
        self.max_asset_bytes = max_asset_bytes
        self.max_image_dimension = max_image_dimension
        self.max_image_pixels = max_image_pixels
        self.max_image_frames = max_image_frames
        self.max_pdf_page_dimension_points = max_pdf_page_dimension_points
        self.max_pdf_objects = max_pdf_objects
        self.max_pdf_text_chars = max_pdf_text_chars
        self.client = client or httpx.Client()
        self._owns_client = client is None

    def __enter__(self) -> "ArxivSourceFigureExtractor":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _download(self, source_url: str) -> bytes | None:
        expected_path = urlsplit(source_url).path
        current_url = source_url
        redirects_followed = 0
        try:
            while True:
                with self.client.stream(
                    "GET",
                    current_url,
                    headers={"User-Agent": self.user_agent},
                    timeout=self.timeout_seconds,
                    follow_redirects=False,
                ) as response:
                    if 300 <= response.status_code < 400:
                        if redirects_followed >= self.max_redirects:
                            return None
                        redirect = _safe_source_redirect(
                            current_url,
                            response.headers.get("location", ""),
                            expected_path=expected_path,
                        )
                        if redirect is None:
                            return None
                        current_url = redirect
                        redirects_followed += 1
                        continue
                    if response.status_code == 404:
                        return None
                    if response.status_code in (408, 425, 429) or (
                        response.status_code >= 500
                    ):
                        raise TransientRecoveryError(
                            f"arXiv source returned {response.status_code}"
                        )
                    if response.status_code >= 400:
                        return None

                    declared_size_text = response.headers.get("content-length")
                    if declared_size_text is not None:
                        try:
                            declared_size = int(declared_size_text)
                        except ValueError:
                            return None
                        if (
                            declared_size < 0
                            or declared_size > self.max_compressed_bytes
                        ):
                            return None

                    body = bytearray()
                    for chunk in response.iter_bytes():
                        if len(chunk) > self.max_compressed_bytes - len(body):
                            return None
                        body.extend(chunk)
                    return bytes(body) or None
        except httpx.RequestError as error:
            raise TransientRecoveryError("arXiv source request failed") from error

    def extract(
        self,
        arxiv_id: str,
        version: int,
    ) -> RecoveredFigure | None:
        figure_cache_key(arxiv_id, version)
        source_url = f"https://arxiv.org/e-print/{arxiv_id}v{version}"
        body = self._download(source_url)
        if body is None:
            return None
        files = _read_archive(
            body,
            max_archive_bytes=self.max_archive_bytes,
            max_members=self.max_members,
            max_member_bytes=self.max_member_bytes,
            max_total_uncompressed_bytes=self.max_total_uncompressed_bytes,
            max_tex_bytes=self.max_tex_bytes,
        )
        if files is None:
            return None
        tex_files = _decode_tex_files(files)
        if tex_files is None:
            return None
        if any(
            path.suffix.casefold() in {".sty", ".cls"}
            for path in files
        ):
            return None
        declarations: list[tuple[PurePosixPath, str]] = []
        for path, lexed in tex_files.items():
            names = _literal_documentclass_declarations(lexed)
            if names is None:
                return None
            declarations.extend((path, name) for name in names)
        if len(declarations) != 1:
            return None
        try:
            return _extract_figure(
                files,
                tex_files,
                main_path=declarations[0][0],
                max_include_depth=self.max_include_depth,
                max_tex_bytes=self.max_tex_bytes,
                max_asset_bytes=self.max_asset_bytes,
                max_image_dimension=self.max_image_dimension,
                max_image_pixels=self.max_image_pixels,
                max_image_frames=self.max_image_frames,
                max_pdf_page_dimension_points=self.max_pdf_page_dimension_points,
                max_pdf_objects=self.max_pdf_objects,
                max_pdf_text_chars=self.max_pdf_text_chars,
                source_url=source_url,
            )
        except (
            AssertionError,
            AttributeError,
            IndexError,
            KeyError,
            MemoryError,
            NameError,
            NotImplementedError,
            RecursionError,
            TypeError,
        ):
            raise
        except Exception:
            LOGGER.exception("Unexpected arXiv source figure extraction failure")
            return None
