import ipaddress
import re
from urllib.parse import urlsplit

from pydantic import HttpUrl, TypeAdapter, ValidationError

from vla_wam_daily.models import Resources

URL_PATTERN = re.compile(r"""https?://[^\s<>"'`{}\[\]]+""", re.IGNORECASE)
ARXIV_ID_PATTERN = re.compile(r"^\d{2}(?:0[1-9]|1[0-2])\.\d{4,5}$")
HTTP_URL: TypeAdapter[HttpUrl] = TypeAdapter(HttpUrl)
CODE_HOSTS = frozenset(
    {
        "github.com",
        "www.github.com",
        "gitlab.com",
        "www.gitlab.com",
    }
)
EXCLUDED_PROJECT_HOSTS = frozenset(
    {
        "arxiv.org",
        "www.arxiv.org",
        "doi.org",
        "dx.doi.org",
        "openreview.net",
    }
)
UNAMBIGUOUS_TRAILING_PUNCTUATION = ".,。，；：！？"
PATH_TRAILING_PUNCTUATION = ";:!?"
BRACKETS = (("(", ")"), ("[", "]"), ("{", "}"))


def clean_url(value: str) -> str:
    candidate = value
    while True:
        previous = candidate
        candidate = candidate.rstrip(UNAMBIGUOUS_TRAILING_PUNCTUATION)
        resource_part = candidate.partition("://")[2]
        query_index = resource_part.find("?")
        has_query_data = 0 <= query_index < len(resource_part) - 1
        if not has_query_data and "#" not in resource_part:
            candidate = candidate.rstrip(PATH_TRAILING_PUNCTUATION)
        for opening, closing in BRACKETS:
            while candidate.endswith(closing) and candidate.count(closing) > candidate.count(
                opening
            ):
                candidate = candidate[:-1]
        if candidate == previous:
            return candidate


def _validated_url(value: str) -> HttpUrl | None:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    try:
        url = HTTP_URL.validate_python(value)
    except ValidationError:
        return None
    if url.username is not None or url.password is not None:
        return None
    if url.host is not None and url.host.endswith(".."):
        return None
    return url


def _normalized_host(url: HttpUrl) -> str:
    host = (url.host or "").lower()
    return host[:-1] if host.endswith(".") else host


def _uses_default_port(url: HttpUrl) -> bool:
    default_ports = {"http": 80, "https": 443}
    return url.port == default_ports[url.scheme.lower()]


def _deduplication_key(url: HttpUrl) -> tuple[str, str, int | None, str, str, str]:
    return (
        url.scheme.lower(),
        _normalized_host(url),
        url.port,
        url.path or "",
        url.query or "",
        url.fragment or "",
    )


def validated_urls(text: str) -> list[str]:
    urls: list[str] = []
    seen: set[tuple[str, str, int | None, str, str, str]] = set()
    for match in URL_PATTERN.finditer(text):
        candidate = clean_url(match.group(0))
        url = _validated_url(candidate)
        if url is None:
            continue
        key = _deduplication_key(url)
        if key in seen:
            continue
        seen.add(key)
        urls.append(candidate)
    return urls


def _is_public_project_host(host: str) -> bool:
    normalized = host.strip("[]").rstrip(".").lower()
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return False
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return True
    return address.is_global


def extract_resources(
    arxiv_id: str,
    abstract: str,
    comment: str | None,
) -> Resources:
    if ARXIV_ID_PATTERN.fullmatch(arxiv_id) is None:
        raise ValueError(f"invalid new-style arXiv ID: {arxiv_id!r}")

    urls = validated_urls(f"{abstract}\n{comment or ''}")
    code_url: HttpUrl | None = None
    project_url: HttpUrl | None = None

    for value in urls:
        url = HTTP_URL.validate_python(value)
        host = _normalized_host(url)
        if host in CODE_HOSTS:
            if code_url is None and _uses_default_port(url):
                code_url = url
            continue
        if (
            project_url is None
            and host not in EXCLUDED_PROJECT_HOSTS
            and _is_public_project_host(host)
        ):
            project_url = url

    return Resources(
        arxiv_url=HTTP_URL.validate_python(f"https://arxiv.org/abs/{arxiv_id}"),
        pdf_url=HTTP_URL.validate_python(f"https://arxiv.org/pdf/{arxiv_id}"),
        project_url=project_url,
        code_url=code_url,
    )
