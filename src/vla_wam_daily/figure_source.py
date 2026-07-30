import io
import math
import re
import tarfile
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import urljoin, urlsplit

import httpx

from vla_wam_daily.figures import figure_cache_key
from vla_wam_daily.models import ARXIV_FIGURE_HOSTS

RecoveredExtension = Literal["png", "jpg", "webp", "gif", "svg"]
RecoveredSource = Literal["arxiv_source", "arxiv_pdf"]

_DOCUMENT_CLASS_RE = re.compile(r"\\documentclass(?![A-Za-z@])")
_DOCUMENT_CLASS_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_INCLUDE_COMMAND_RE = re.compile(r"\\(?:input|include)(?![A-Za-z@])")
_LITERAL_INCLUDE_RE = re.compile(
    r"\\(?:input|include)(?![A-Za-z@])\s*\{([^{}]*)\}"
)
_FIGURE_TOKEN_RE = re.compile(
    r"\\(?P<action>begin|end)\s*\{(?P<environment>figure\*?)\}"
)
_INCLUDE_GRAPHICS_RE = re.compile(r"\\includegraphics(?![A-Za-z@])")
_CAPTION_RE = re.compile(r"\\caption(?![A-Za-z@])")
_CONTROL_SEQUENCE_RE = re.compile(
    r"\\(?:(?P<word>[A-Za-z@]+)|(?P<symbol>[^A-Za-z@]))",
    re.DOTALL,
)
_SCHEME_PREFIX_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_VERBATIM_ENVIRONMENT_RE = re.compile(
    r"\\begin\s*\{(?P<environment>"
    r"verbatim\*?|Verbatim\*?|lstlisting|minted"
    r")\}"
)
_VERB_COMMAND_RE = re.compile(r"\\verb\*?")
_UNSUPPORTED_INLINE_VERBATIM_RE = re.compile(
    r"\\(?:lstinline|mintinline|SaveVerb)(?![A-Za-z@])"
)
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


@dataclass(frozen=True)
class RecoveredFigure:
    caption: str
    extension: RecoveredExtension
    content: bytes
    source_url: str
    source: RecoveredSource


class TransientRecoveryError(RuntimeError):
    pass


class _RejectedArchive(RuntimeError):
    pass


def _strip_tex_comments(text: str) -> str:
    uncommented_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        cutoff = len(line)
        for index, character in enumerate(line):
            if character != "%":
                continue
            backslashes = 0
            previous = index - 1
            while previous >= 0 and line[previous] == "\\":
                backslashes += 1
                previous -= 1
            if backslashes % 2 == 0:
                cutoff = index
                break
        prefix = line[:cutoff]
        if line.endswith(("\n", "\r")) and not prefix.endswith(("\n", "\r")):
            prefix += "\n"
        uncommented_lines.append(prefix)
    return "".join(uncommented_lines)


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
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:*") as archive:
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
        _RejectedArchive,
    ):
        return None
    return files


def _decode_tex_files(
    files: dict[PurePosixPath, bytes],
) -> dict[PurePosixPath, str] | None:
    decoded: dict[PurePosixPath, str] = {}
    try:
        for path, content in files.items():
            if path.suffix.casefold() == ".tex":
                text = _mask_verbatim_like(content.decode("utf-8-sig"))
                if text is None:
                    return None
                decoded[path] = _strip_tex_comments(text)
    except UnicodeDecodeError:
        return None
    return decoded


def _mask_range(characters: list[str], start: int, end: int) -> None:
    for index in range(start, end):
        if characters[index] not in "\r\n":
            characters[index] = " "


def _mask_verbatim_like(text: str) -> str | None:
    characters = list(text)
    current = text
    cursor = 0
    while True:
        begin = next(
            (
                match
                for match in _VERBATIM_ENVIRONMENT_RE.finditer(
                    current,
                    cursor,
                )
                if not _is_escaped(current, match.start())
            ),
            None,
        )
        if begin is None:
            break
        environment = re.escape(begin.group("environment"))
        end_pattern = re.compile(rf"\\end\s*\{{{environment}\}}")
        end = next(
            (
                match
                for match in end_pattern.finditer(current, begin.end())
                if not _is_escaped(current, match.start())
            ),
            None,
        )
        if end is None:
            return None
        _mask_range(characters, begin.start(), end.end())
        current = "".join(characters)
        cursor = end.end()

    current = "".join(characters)
    cursor = 0
    while True:
        command = next(
            (
                match
                for match in _VERB_COMMAND_RE.finditer(current, cursor)
                if not _is_escaped(current, match.start())
            ),
            None,
        )
        if command is None:
            break
        if command.end() >= len(current):
            return None
        delimiter = current[command.end()]
        if delimiter.isspace():
            return None
        closing = current.find(delimiter, command.end() + 1)
        newline = current.find("\n", command.end() + 1)
        if closing < 0 or (newline >= 0 and newline < closing):
            return None
        _mask_range(characters, command.start(), closing + 1)
        current = "".join(characters)
        cursor = closing + 1

    if any(
        not _is_escaped(current, match.start())
        for match in _UNSUPPORTED_INLINE_VERBATIM_RE.finditer(current)
    ):
        return None
    return current


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
    tex_files: dict[PurePosixPath, str],
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
    tex_files: dict[PurePosixPath, str],
    root: PurePosixPath,
    max_include_depth: int,
    max_tex_bytes: int,
    depth: int = 0,
    stack: tuple[PurePosixPath, ...] = (),
    consumed_bytes: list[int] | None = None,
) -> str | None:
    if path in stack:
        return None
    text = tex_files.get(path)
    if text is None:
        return None
    if consumed_bytes is None:
        consumed_bytes = [0]
    consumed_bytes[0] += len(text.encode("utf-8"))
    if consumed_bytes[0] > max_tex_bytes:
        return None

    command_matches = _literal_matches(_INCLUDE_COMMAND_RE, text)
    literal_matches = _literal_matches(_LITERAL_INCLUDE_RE, text)
    if len(command_matches) != len(literal_matches):
        return None
    if literal_matches and depth >= max_include_depth:
        return None

    parts: list[str] = []
    cursor = 0
    for match in literal_matches:
        include_path = _resolve_tex_include(tex_files, root, match.group(1))
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
        parts.extend((text[cursor : match.start()], included))
        cursor = match.end()
    parts.append(text[cursor:])
    return "".join(parts)


def _first_figure_block(text: str) -> tuple[str, int, int] | None:
    first = next(iter(_literal_matches(_FIGURE_TOKEN_RE, text)), None)
    if (
        first is None
        or first.group("action") != "begin"
        or _brace_depth_at(text, first.start()) != 0
    ):
        return None
    environment = first.group("environment")
    following = next(
        iter(_literal_matches(_FIGURE_TOKEN_RE, text, first.end())),
        None,
    )
    if (
        following is None
        or following.group("action") != "end"
        or following.group("environment") != environment
        or _brace_depth_at(text, following.start()) != 0
    ):
        return None
    return (
        text[first.end() : following.start()],
        first.start(),
        following.end(),
    )


def _brace_depth_at(text: str, end: int) -> int | None:
    depth = 0
    for index, character in enumerate(text[:end]):
        if _is_escaped(text, index):
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            if depth == 0:
                return None
            depth -= 1
    return depth


def _is_escaped(text: str, index: int) -> bool:
    backslashes = 0
    index -= 1
    while index >= 0 and text[index] == "\\":
        backslashes += 1
        index -= 1
    return backslashes % 2 == 1


def _literal_matches(
    pattern: re.Pattern[str],
    text: str,
    position: int = 0,
) -> list[re.Match[str]]:
    return [
        match
        for match in pattern.finditer(text, position)
        if not _is_escaped(text, match.start())
    ]


def _has_ambiguous_semantic_control(text: str) -> bool:
    for match in _literal_matches(_CONTROL_SEQUENCE_RE, text):
        word = match.group("word")
        if word is None:
            continue
        if (
            word in _MACRO_DEFINITION_CONTROLS
            or word in _CONDITIONAL_CONTROLS
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
        if not _is_escaped(text, index):
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


def _literal_command_argument(
    text: str,
    match: re.Match[str],
    *,
    allow_options: bool,
) -> str | None:
    position = _skip_whitespace(text, match.end())
    if allow_options and position < len(text) and text[position] == "[":
        option = _parse_delimited(text, position, "[", "]")
        if option is None:
            return None
        position = _skip_whitespace(text, option[1])
    argument = _parse_delimited(text, position, "{", "}")
    return None if argument is None else argument[0]


def _literal_documentclass_declarations(text: str) -> list[str] | None:
    declarations: list[str] = []
    for match in _literal_matches(_DOCUMENT_CLASS_RE, text):
        if _brace_depth_at(text, match.start()) != 0:
            return None
        argument = _literal_command_argument(
            text,
            match,
            allow_options=True,
        )
        if argument is None:
            return None
        name = argument.strip()
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
    if extension is None or not content:
        return None
    return extension, content


def _extract_figure(
    files: dict[PurePosixPath, bytes],
    tex_files: dict[PurePosixPath, str],
    *,
    main_path: PurePosixPath,
    max_include_depth: int,
    max_tex_bytes: int,
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
    block = _first_figure_block(expanded)
    if block is None:
        return None
    body, _block_start, block_end = block
    if (
        _has_ambiguous_semantic_control(expanded[:block_end])
        or _UNSAFE_FIGURE_RE.search(body)
    ):
        return None

    for match in _literal_matches(_CONTROL_SEQUENCE_RE, body):
        word = match.group("word")
        symbol = match.group("symbol")
        if word is not None:
            if word not in _ALLOWED_FIGURE_CONTROLS:
                return None
        elif symbol not in _ALLOWED_FIGURE_CONTROL_SYMBOLS:
            return None
    graphics_matches = _literal_matches(_INCLUDE_GRAPHICS_RE, body)
    caption_matches = _literal_matches(_CAPTION_RE, body)
    if len(graphics_matches) != 1 or len(caption_matches) != 1:
        return None
    if (
        _brace_depth_at(body, graphics_matches[0].start()) != 0
        or _brace_depth_at(body, caption_matches[0].start()) != 0
    ):
        return None
    asset_target = _literal_command_argument(
        body,
        graphics_matches[0],
        allow_options=True,
    )
    raw_caption = _literal_command_argument(
        body,
        caption_matches[0],
        allow_options=True,
    )
    if asset_target is None or raw_caption is None:
        return None
    caption = _normalize_caption(raw_caption)
    asset = _resolve_asset(files, root=root, target=asset_target)
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
        max_redirects: int = 3,
        max_members: int = 2_000,
        max_member_bytes: int = 25_000_000,
        max_total_uncompressed_bytes: int = 150_000_000,
        max_include_depth: int = 8,
        max_tex_bytes: int = 10_000_000,
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
            "max_members": max_members,
            "max_member_bytes": max_member_bytes,
            "max_total_uncompressed_bytes": max_total_uncompressed_bytes,
            "max_tex_bytes": max_tex_bytes,
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
        self.max_redirects = max_redirects
        self.max_members = max_members
        self.max_member_bytes = max_member_bytes
        self.max_total_uncompressed_bytes = max_total_uncompressed_bytes
        self.max_include_depth = max_include_depth
        self.max_tex_bytes = max_tex_bytes
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
                    if response.status_code == 429 or response.status_code >= 500:
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
        declarations: list[tuple[PurePosixPath, str]] = []
        for path, text in tex_files.items():
            names = _literal_documentclass_declarations(text)
            if names is None:
                return None
            declarations.extend((path, name) for name in names)
        if len(declarations) != 1:
            return None
        return _extract_figure(
            files,
            tex_files,
            main_path=declarations[0][0],
            max_include_depth=self.max_include_depth,
            max_tex_bytes=self.max_tex_bytes,
            source_url=source_url,
        )
