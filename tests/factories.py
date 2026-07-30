from datetime import UTC, datetime

from vla_wam_daily.models import (
    Analysis,
    FigureAsset,
    FigureGallery,
    FigureRecoveryStatus,
    FigureStatus,
    PaperRecord,
    Provenance,
    Resources,
    Topic,
)


def make_gallery(
    *,
    arxiv_id: str = "2607.12345",
    version: int = 1,
    status: FigureStatus = FigureStatus.AVAILABLE,
) -> FigureGallery:
    timestamp = datetime(2026, 7, 27, 1, 0, tzinfo=UTC)
    html_url = f"https://arxiv.org/html/{arxiv_id}v{version}"
    figures = []
    if status is FigureStatus.AVAILABLE:
        figures = [
            FigureAsset(
                number=1,
                label="Figure 1",
                caption="The model architecture.",
                image_urls=[f"https://arxiv.org/html/{arxiv_id}v{version}/x1.png"],
                source_url=f"{html_url}#S1.F1",
            ),
            FigureAsset(
                number=2,
                label="Figure 2",
                caption="Robot evaluation environments.",
                image_urls=[f"https://arxiv.org/html/{arxiv_id}v{version}/x2.png"],
                source_url=f"{html_url}#S2.F2",
            ),
        ]
    return FigureGallery(
        status=status,
        html_url=html_url,
        figures=figures,
        checked_at=timestamp,
        recovery_status=(
            FigureRecoveryStatus.AVAILABLE
            if any(figure.number == 1 for figure in figures)
            else FigureRecoveryStatus.NOT_ATTEMPTED
        ),
    )


def make_record(
    *,
    arxiv_id: str = "2607.12345",
    version: int = 1,
    score: int = 8,
    topic: Topic = Topic.VLA,
) -> PaperRecord:
    timestamp = datetime(2026, 7, 27, 1, 0, tzinfo=UTC)
    return PaperRecord(
        arxiv_id=arxiv_id,
        version=version,
        published_at=timestamp,
        updated_at=timestamp,
        title="A Vision-Language-Action Policy for Robot Manipulation",
        title_zh="用于机器人操作的视觉语言动作策略",
        authors=["Ada Robot", "Wei Model"],
        arxiv_categories=["cs.RO", "cs.CV"],
        abstract="We introduce a vision-language-action policy for robot manipulation.",
        matched_rules=["vision language action"],
        analysis=Analysis(
            relevance_score=score,
            primary_topic=topic,
            tags=["Vision-Language", "Robot Manipulation"],
            one_sentence_summary="提出一种用于机器人操作的视觉语言动作策略。",
            main_contribution="统一视觉、语言与动作建模。",
            method="使用多模态策略学习。",
            key_results="摘要未说明",
            limitations="摘要未说明",
            relation_to_vla_wam="该方法直接属于 VLA。",
        ),
        resources=Resources(
            arxiv_url=f"https://arxiv.org/abs/{arxiv_id}",
            pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
        ),
        provenance=Provenance(
            analysis_scope="title_and_abstract",
            model="deepseek-v4-pro",
            prompt_version="1",
            analyzed_at=timestamp,
        ),
        figure_gallery=make_gallery(arxiv_id=arxiv_id, version=version),
    )


def make_figure_fixture_records() -> list[PaperRecord]:
    records = [make_record()]
    statuses = [
        FigureStatus.HTML_UNAVAILABLE,
        FigureStatus.NOT_FOUND,
        FigureStatus.FETCH_FAILED,
    ]
    for index, status in enumerate(statuses, start=1):
        arxiv_id = f"2607.2000{index}"
        record_data = make_record(arxiv_id=arxiv_id, score=6).model_dump()
        record_data.update(
            title=f"Figure fallback fixture {index}",
            title_zh=f"图片降级状态测试 {index}",
            abstract="A fixture paper without the primary search keyword.",
            figure_gallery=make_gallery(arxiv_id=arxiv_id, status=status),
        )
        record_data["analysis"].update(
            one_sentence_summary=f"图片降级测试摘要 {index}",
            main_contribution=f"图片降级测试贡献 {index}",
            method=f"图片降级测试方法 {index}",
            key_results=f"图片降级测试结果 {index}",
            limitations=f"图片降级测试局限 {index}",
            relation_to_vla_wam=f"图片降级测试关联 {index}",
        )
        records.append(PaperRecord(**record_data))
    return records
