import fcntl
import logging
import os
import secrets
import stat
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from vla_wam_daily.figure_recovery_types import DEFAULT_MAX_ASSET_BYTES
from vla_wam_daily.figures import figure_cache_key
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
RECOVERED_EXTENSIONS = frozenset(MEDIA_EXTENSIONS.values())


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
        max_image_bytes: int = DEFAULT_MAX_ASSET_BYTES,
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

    def _atomic_install(
        self,
        *,
        target_directory: Path,
        target: Path,
        chunks: Iterable[bytes],
        no_clobber: bool = False,
    ) -> bool:
        if target.parent != target_directory:
            raise ValueError("Figure asset target is unsafe")
        directory_descriptor = self._open_target_directory_fd(
            target_directory
        )
        temporary_name: str | None = None
        try:
            fcntl.flock(directory_descriptor, fcntl.LOCK_EX)
            descriptor, temporary_name = self._create_temporary_file(
                directory_descriptor,
                target.name,
            )
            total_bytes = 0
            try:
                handle = os.fdopen(descriptor, "wb")
            except Exception:
                os.close(descriptor)
                raise
            with handle:
                for chunk in chunks:
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
            state = self._target_state(
                directory_descriptor,
                target.name,
            )
            if no_clobber:
                if state == "nonempty":
                    return False
                if state == "empty":
                    os.unlink(target.name, dir_fd=directory_descriptor)
                try:
                    os.link(
                        temporary_name,
                        target.name,
                        src_dir_fd=directory_descriptor,
                        dst_dir_fd=directory_descriptor,
                        follow_symlinks=False,
                    )
                except FileExistsError as error:
                    if (
                        self._target_state(
                            directory_descriptor,
                            target.name,
                        )
                        == "nonempty"
                    ):
                        return False
                    raise ValueError(
                        "Figure asset target changed during publication"
                    ) from error
            else:
                os.replace(
                    temporary_name,
                    target.name,
                    src_dir_fd=directory_descriptor,
                    dst_dir_fd=directory_descriptor,
                )
            os.fsync(directory_descriptor)
            return True
        finally:
            if temporary_name is not None:
                with suppress(FileNotFoundError):
                    os.unlink(temporary_name, dir_fd=directory_descriptor)
            os.close(directory_descriptor)

    def _open_target_directory_fd(self, target_directory: Path) -> int:
        try:
            components = target_directory.relative_to(self.public_dir).parts
        except ValueError as error:
            raise ValueError("Figure asset directory escapes public_dir") from error
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(self.public_dir, flags)
            for component in components:
                try:
                    next_descriptor = os.open(
                        component,
                        flags,
                        dir_fd=descriptor,
                    )
                finally:
                    os.close(descriptor)
                descriptor = next_descriptor
        except OSError as error:
            raise ValueError(
                "Figure asset directory cannot be opened safely"
            ) from error
        return descriptor

    @staticmethod
    def _create_temporary_file(
        directory_descriptor: int,
        target_name: str,
    ) -> tuple[int, str]:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        for _attempt in range(100):
            name = f".{target_name}.{secrets.token_hex(8)}.tmp"
            try:
                descriptor = os.open(
                    name,
                    flags,
                    0o600,
                    dir_fd=directory_descriptor,
                )
            except FileExistsError:
                continue
            return descriptor, name
        raise OSError("could not allocate a temporary Figure asset")

    @staticmethod
    def _target_state(
        directory_descriptor: int,
        target_name: str,
    ) -> str:
        try:
            details = os.stat(
                target_name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return "missing"
        if stat.S_ISLNK(details.st_mode):
            raise ValueError(
                "Figure asset target must not be a symbolic link"
            )
        if not stat.S_ISREG(details.st_mode):
            raise ValueError("Figure asset target must be a regular file")
        return "nonempty" if details.st_size > 0 else "empty"

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
        figure_cache_key(arxiv_id, version)
        if type(figure_number) is not int or figure_number not in (1, 2):
            raise ValueError("figure_number must be 1 or 2")
        if type(panel) is not int or panel < 1:
            raise ValueError("panel must be a positive integer")
        if extension not in RECOVERED_EXTENSIONS:
            raise ValueError("unsupported recovered Figure extension")
        if (
            type(content) is not bytes
            or not content
            or len(content) > self.max_image_bytes
        ):
            raise ValueError("recovered Figure content has an invalid size")

        relative_path = (
            f"/figures/{arxiv_id}/v{version}/"
            f"fig{figure_number}-panel{panel}.{extension}"
        )
        target_directory = self._prepare_target_directory(
            arxiv_id=arxiv_id,
            version=version,
        )
        target = target_directory / Path(relative_path).name
        if target.is_symlink():
            raise ValueError("Figure asset target must not be a symbolic link")
        try:
            if target.is_file() and target.stat().st_size > 0:
                return relative_path
        except OSError as error:
            raise ValueError("Figure asset target cannot be inspected") from error
        if target.exists() and not target.is_file():
            raise ValueError("Figure asset target must be a regular file")
        self._atomic_install(
            target_directory=target_directory,
            target=target,
            chunks=(content,),
            no_clobber=True,
        )
        return relative_path

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
                self._atomic_install(
                    target_directory=target_directory,
                    target=target,
                    chunks=response.iter_bytes(),
                )
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
