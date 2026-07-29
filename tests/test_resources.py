import pytest

from vla_wam_daily.resources import clean_url, extract_resources, validated_urls


def test_extracts_code_and_project_urls_from_metadata() -> None:
    resources = extract_resources(
        arxiv_id="2607.12345",
        abstract="Code: https://github.com/example/vla-policy.",
        comment="Project page https://example.github.io/vla-policy/",
    )

    assert str(resources.code_url) == "https://github.com/example/vla-policy"
    assert str(resources.project_url) == "https://example.github.io/vla-policy/"
    assert str(resources.arxiv_url) == "https://arxiv.org/abs/2607.12345"
    assert str(resources.pdf_url) == "https://arxiv.org/pdf/2607.12345"


def test_does_not_invent_missing_resources() -> None:
    resources = extract_resources(
        arxiv_id="2607.12345",
        abstract="No external links are provided.",
        comment=None,
    )

    assert resources.code_url is None
    assert resources.project_url is None


def test_selects_first_code_and_project_urls_in_stable_text_order() -> None:
    resources = extract_resources(
        arxiv_id="2607.12345",
        abstract=(
            "Project https://first.example/paper and https://second.example/paper. "
            "Code https://gitlab.com/group/first and https://github.com/group/second."
        ),
        comment=None,
    )

    assert str(resources.project_url) == "https://first.example/paper"
    assert str(resources.code_url) == "https://gitlab.com/group/first"


def test_validated_urls_deduplicates_stably() -> None:
    assert validated_urls(
        "https://example.com/paper https://example.com/paper https://other.example/project"
    ) == [
        "https://example.com/paper",
        "https://other.example/project",
    ]


def test_cleans_sentence_punctuation_without_damaging_balanced_parentheses() -> None:
    assert clean_url("https://example.com/paper.") == "https://example.com/paper"
    assert clean_url("https://example.com/model(v2)).") == "https://example.com/model(v2)"
    assert clean_url("https://example.com/model(v2)") == "https://example.com/model(v2)"
    assert clean_url("https://example.com/paper,;:!?]}") == "https://example.com/paper"


def test_code_host_confusion_is_not_classified_as_code() -> None:
    resources = extract_resources(
        arxiv_id="2607.12345",
        abstract="Repository https://github.com.evil.example/org/repo",
        comment=None,
    )

    assert resources.code_url is None
    assert str(resources.project_url) == "https://github.com.evil.example/org/repo"


def test_rejects_urls_with_credentials() -> None:
    resources = extract_resources(
        arxiv_id="2607.12345",
        abstract=(
            "Code https://trusted.example@github.com/org/repo "
            "project https://user:password@example.com/paper"
        ),
        comment=None,
    )

    assert validated_urls("https://user:password@example.com/paper") == []
    assert resources.code_url is None
    assert resources.project_url is None


@pytest.mark.parametrize(
    "arxiv_id",
    [
        "../2607.12345",
        "2607.12345/pdf",
        "2607.12345v2",
        "hep-th/9901001",
        "260.12345",
        "2607.123456",
        "2600.12345",
        "2613.12345",
        "0001.1234",
        "0703.1234",
    ],
)
def test_rejects_invalid_new_style_arxiv_ids(arxiv_id: str) -> None:
    with pytest.raises(ValueError, match="arXiv ID"):
        extract_resources(arxiv_id, "No links.", None)


def test_accepts_first_new_style_arxiv_month() -> None:
    resources = extract_resources("0704.1234", "No links.", None)

    assert str(resources.arxiv_url) == "https://arxiv.org/abs/0704.1234"
    assert str(resources.pdf_url) == "https://arxiv.org/pdf/0704.1234"


def test_excludes_scholarly_hosts_from_project_selection() -> None:
    resources = extract_resources(
        arxiv_id="2607.12345",
        abstract=(
            "Abstract https://arxiv.org/abs/2607.12345 "
            "DOI https://doi.org/10.1000/example "
            "legacy DOI https://dx.doi.org/10.1000/example "
            "review https://openreview.net/forum?id=example"
        ),
        comment=None,
    )

    assert resources.project_url is None
    assert resources.code_url is None


def test_ignores_malformed_and_non_http_urls() -> None:
    assert (
        validated_urls(
            'Bad https:// and https:///missing-host plus ftp://example.com/file and "https://".'
        )
        == []
    )


def test_normalizes_host_case_default_port_and_trailing_dot_for_classification() -> None:
    resources = extract_resources(
        arxiv_id="2607.12345",
        abstract="Code https://WWW.GITHUB.COM.:443/Example/Repo",
        comment=None,
    )

    assert str(resources.code_url) == "https://www.github.com./Example/Repo"
    assert resources.project_url is None


def test_rejects_hosts_with_multiple_dns_root_dots() -> None:
    value = "https://github.com.../Example/Repo"
    resources = extract_resources("2607.12345", f"Code {value}", None)

    assert validated_urls(value) == []
    assert resources.code_url is None
    assert resources.project_url is None


@pytest.mark.parametrize(
    "value",
    [
        "https://github.com:444/Example/Repo",
        "http://gitlab.com:443/Example/Repo",
    ],
)
def test_nondefault_code_host_ports_are_not_published(value: str) -> None:
    resources = extract_resources("2607.12345", f"Code {value}", None)

    assert resources.code_url is None
    assert resources.project_url is None


def test_preserves_legal_parentheses_query_and_fragment() -> None:
    urls = validated_urls("See (https://example.com/docs/model(v2)?mode=full&view=1#figure-2).")

    assert urls == ["https://example.com/docs/model(v2)?mode=full&view=1#figure-2"]


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/#why?",
        "https://example.com/?flags=fast;",
        "https://example.com/#important!",
        "https://example.com/#section:",
    ],
)
def test_preserves_semantic_query_and_fragment_punctuation(value: str) -> None:
    assert validated_urls(value) == [value]


def test_stops_at_latex_and_markdown_markup_delimiters() -> None:
    urls = validated_urls(
        r"\href{https://example.com/paper}{website} "
        "`https://github.com/example/repository`"
    )

    assert urls == [
        "https://example.com/paper",
        "https://github.com/example/repository",
    ]


@pytest.mark.parametrize("punctuation", ["。", "，", "；", "：", "！", "？"])
def test_cleans_common_unicode_sentence_punctuation(punctuation: str) -> None:
    assert validated_urls(f"论文 https://example.com/paper{punctuation}") == [
        "https://example.com/paper"
    ]


def test_skips_local_and_non_public_project_hosts() -> None:
    resources = extract_resources(
        arxiv_id="2607.12345",
        abstract=(
            "Local https://localhost/project "
            "loopback https://127.0.0.1/project "
            "integer loopback https://2130706433/project "
            "private https://10.0.0.7/project "
            "link-local https://169.254.1.1/project "
            "IPv6 https://[::1]/project "
            "public https://project.example/paper"
        ),
        comment=None,
    )

    assert str(resources.project_url) == "https://project.example/paper"


def test_extracts_public_ipv6_literal_as_project_url() -> None:
    value = "https://[2606:4700:4700::1111]/paper"
    resources = extract_resources("2607.12345", f"Project {value}", None)

    assert validated_urls(value) == [value]
    assert str(resources.project_url) == value


@pytest.mark.parametrize(
    "value",
    [
        "https://[::1]/paper",
        "https://[fc00::1]/paper",
        "https://[fe80::1]/paper",
    ],
)
def test_tokenizes_but_rejects_nonpublic_ipv6_project_urls(value: str) -> None:
    resources = extract_resources("2607.12345", f"Project {value}", None)

    assert validated_urls(value) == [value]
    assert resources.project_url is None


def test_code_host_is_never_reused_as_project_url() -> None:
    resources = extract_resources(
        arxiv_id="2607.12345",
        abstract=(
            "Paper https://www.arxiv.org/abs/2607.12345 "
            "code https://www.gitlab.com/group/repository"
        ),
        comment=None,
    )

    assert str(resources.code_url) == "https://www.gitlab.com/group/repository"
    assert resources.project_url is None
