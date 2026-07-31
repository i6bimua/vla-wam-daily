from dataclasses import dataclass
from typing import Literal

DEFAULT_MAX_ASSET_BYTES = 15_000_000

RecoveredExtension = Literal["png", "jpg", "webp", "gif", "svg"]
RecoveredSource = Literal["arxiv_source", "arxiv_pdf"]


@dataclass(frozen=True)
class RecoveredFigure:
    caption: str
    extension: RecoveredExtension
    content: bytes
    source_url: str
    source: RecoveredSource
    number: Literal[1, 2] = 1


class TransientRecoveryError(RuntimeError):
    pass
