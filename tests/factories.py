from datetime import UTC, datetime

from vla_wam_daily.models import Analysis, PaperRecord, Provenance, Resources, Topic


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
    )
