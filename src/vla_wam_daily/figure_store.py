import logging
import os
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from vla_wam_daily.models import (
    ARXIV_FIGURE_HOSTS,
    FigureAsset,
    FigureGallery,
    FigureStatus,
    parse_arxiv_html_identity,
)

LOGGER = logging.getLogger(__name__)
MEDIA_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/svg+xml": "svg",
}


def _resolve_safe_redirect(
    current_url: str,
    location: str,
    expected_path_prefix: str,
) -> str:
    if not location.strip():
        raise ValueError("arXiv image redirect is missing a location")
    candidate = urljoin(current_url, location)
    parsed = urlsplit(candidate)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("arXiv image redirect has an invalid port") from error
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ARXIV_FIGURE_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.fragment
        or not parsed.path.startswith(expected_path_prefix)
    ):
        raise ValueError("arXiv image redirect changed the paper identity")
    return candidate


class ArxivFigureStore:
    def __init__(
        self,
        *,
        public_dir: Path,
        user_agent: str,
        timeout_seconds: float = 30,
        max_image_bytes: int = 15_000_000,
        max_redirects: int = 3,
        client: httpx.Client | None = None,
    ) -> None:
        if not user_agent.strip():
            raise ValueError("user_agent must not be blank")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_image_bytes < 1:
            raise ValueError("max_image_bytes must be positive")
        if type(max_redirects) is not int or max_redirects < 0:
            raise ValueError("max_redirects must be a nonnegative integer")
        self.public_dir = public_dir
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.max_image_bytes = max_image_bytes
        self.max_redirects = max_redirects
        self.client = client or httpx.Client()
        self._owns_client = client is None

    def __enter__(self) -> "ArxivFigureStore":
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

    def _existing_cached_target(self, cached_path: str) -> Path | None:
        if not self.public_dir.is_dir() or self.public_dir.is_symlink():
            return None
        root = self.public_dir.resolve(strict=True)
        target = self.public_dir / cached_path.removeprefix("/")
        try:
            parent = target.parent.resolve(strict=True)
        except OSError:
            return None
        if not parent.is_relative_to(root) or target.is_symlink():
            return None
        return target

    def _prepare_target_directory(
        self,
        *,
        arxiv_id: str,
        version: int,
    ) -> Path:
        self.public_dir.mkdir(parents=True, exist_ok=True)
        if self.public_dir.is_symlink():
            raise ValueError("public_dir must not be a symbolic link")
        root = self.public_dir.resolve(strict=True)
        current = self.public_dir
        for component in ("figures", arxiv_id, f"v{version}"):
            current = current / component
            current.mkdir(exist_ok=True)
            if current.is_symlink():
                raise ValueError("Figure asset directory must not be a symbolic link")
            if not current.resolve(strict=True).is_relative_to(root):
                raise ValueError("Figure asset directory escapes public_dir")
        return current

    def _cached_panel(self, figure: FigureAsset, index: int) -> str | None:
        if not figure.cached_image_paths:
            return None
        cached_path = figure.cached_image_paths[index]
        if cached_path is None:
            return None
        target = self._existing_cached_target(cached_path)
        if target is None:
            return None
        try:
            if target.is_file() and target.stat().st_size > 0:
                return cached_path
        except OSError:
            return None
        return None

    def _download_panel(
        self,
        *,
        image_url: str,
        arxiv_id: str,
        version: int,
        figure_number: int,
        panel: int,
    ) -> str:
        expected_path_prefix = f"/html/{arxiv_id}v{version}/"
        current_url = image_url
        redirects_followed = 0
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
                        raise ValueError("arXiv image exceeded the redirect limit")
                    current_url = _resolve_safe_redirect(
                        current_url,
                        response.headers.get("location", ""),
                        expected_path_prefix,
                    )
                    redirects_followed += 1
                    continue

                response.raise_for_status()
                media_type = (
                    response.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .strip()
                    .casefold()
                )
                extension = MEDIA_EXTENSIONS.get(media_type)
                if extension is None:
                    raise ValueError("unsupported Figure image content type")
                declared_size_text = response.headers.get("content-length")
                if declared_size_text is not None:
                    try:
                        declared_size = int(declared_size_text)
                    except ValueError as error:
                        raise ValueError(
                            "Figure image has an invalid content length"
                        ) from error
                    if declared_size < 0 or declared_size > self.max_image_bytes:
                        raise ValueError(
                            "Figure image exceeds configured size limit"
                        )

                relative_path = (
                    f"/figures/{arxiv_id}/v{version}/"
                    f"fig{figure_number}-panel{panel}.{extension}"
                )
                target_directory = self._prepare_target_directory(
                    arxiv_id=arxiv_id,
                    version=version,
                )
                target = target_directory / Path(relative_path).name
                descriptor, temporary_name = tempfile.mkstemp(
                    dir=target_directory,
                    prefix=f".{target.name}.",
                    suffix=".tmp",
                )
                temporary_path = Path(temporary_name)
                try:
                    total_bytes = 0
                    with os.fdopen(descriptor, "wb") as handle:
                        for chunk in response.iter_bytes():
                            total_bytes += len(chunk)
                            if total_bytes > self.max_image_bytes:
                                raise ValueError(
                                    "Figure image exceeds configured size limit"
                                )
                            handle.write(chunk)
                        if total_bytes == 0:
                            raise ValueError("Figure image response is empty")
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temporary_path, target)
                    directory_descriptor = os.open(
                        target_directory,
                        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                    )
                    try:
                        os.fsync(directory_descriptor)
                    finally:
                        os.close(directory_descriptor)
                finally:
                    temporary_path.unlink(missing_ok=True)
                return relative_path

    def _mirror_figure(
        self,
        figure: FigureAsset,
        *,
        arxiv_id: str,
        version: int,
    ) -> FigureAsset:
        cached_paths: list[str | None] = []
        for index, image_url in enumerate(figure.image_urls):
            cached_path = self._cached_panel(figure, index)
            if cached_path is None:
                try:
                    cached_path = self._download_panel(
                        image_url=str(image_url),
                        arxiv_id=arxiv_id,
                        version=version,
                        figure_number=figure.number,
                        panel=index + 1,
                    )
                except Exception:
                    LOGGER.warning(
                        "failed to mirror %sv%s Figure %s panel %s",
                        arxiv_id,
                        version,
                        figure.number,
                        index + 1,
                        exc_info=True,
                    )
            cached_paths.append(cached_path)
        return figure.model_copy(
            update={"cached_image_paths": tuple(cached_paths)}
        )

    def mirror_gallery(self, gallery: FigureGallery) -> FigureGallery:
        if gallery.status is not FigureStatus.AVAILABLE:
            return gallery
        arxiv_id, version = parse_arxiv_html_identity(gallery.html_url)
        figures = tuple(
            self._mirror_figure(
                figure,
                arxiv_id=arxiv_id,
                version=version,
            )
            for figure in gallery.figures
        )
        return gallery.model_copy(update={"figures": figures})
