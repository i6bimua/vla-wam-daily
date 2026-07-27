import re
import unicodedata

from vla_wam_daily.config import PrefilterConfig
from vla_wam_daily.models import RawPaper

SEPARATOR_RE = re.compile(r"[\s\-_–—/]+")
PUNCTUATION_RE = re.compile(r"[^\w\s]")


def normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = SEPARATOR_RE.sub(" ", normalized)
    normalized = PUNCTUATION_RE.sub(" ", normalized)
    return " ".join(normalized.split())


def contains_phrase(haystack: str, phrase: str) -> bool:
    normalized_phrase = normalize(phrase)
    if not normalized_phrase:
        return False
    return re.search(rf"\b{re.escape(normalized_phrase)}\b", haystack) is not None


def match_paper(paper: RawPaper, config: PrefilterConfig) -> list[str]:
    fields = [normalize(paper.title), normalize(paper.abstract)]
    matches: list[str] = []

    for phrase in config.exact_phrases:
        if any(contains_phrase(field, phrase) for field in fields):
            matches.append(f"exact:{normalize(phrase).replace(' ', '-')}")

    for rule in config.composite_rules:
        if all(
            any(contains_phrase(field, phrase) for field in fields for phrase in group)
            for group in rule.groups
        ):
            matches.append(f"composite:{rule.name}")

    return list(dict.fromkeys(matches))
