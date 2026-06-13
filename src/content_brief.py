"""콘텐츠 초안 생성.

키워드를 입력하면 검색 의도에 맞춘 마크다운 초안 + 메타데이터를 만든다.
이 결과는 '완성본'이 아니라 '검수용 초안'이다. 반드시 사람이 사실확인·보강한
뒤 발행한다(docs/04 품질 체크리스트).
"""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

import anthropic
from pydantic import BaseModel, Field

from . import config

DRAFTS_DIR = Path("drafts")


class ContentBrief(BaseModel):
    target_keyword: str
    title_tag: str = Field(description="검색결과 제목(50~60자, 키워드 포함, 클릭 유도)")
    meta_description: str = Field(description="메타설명(120~155자, 키워드 포함)")
    slug: str = Field(description="URL 슬러그(영문 소문자-하이픈)")
    secondary_keywords: list[str] = Field(description="함께 노릴 보조 키워드")
    body_markdown: str = Field(
        description="본문 초안(마크다운). 소제목/표/리스트로 스캔 가능하게. "
        "도입부에서 검색 의도에 즉시 답하고, 고유한 정보·예시를 포함."
    )
    internal_link_suggestions: list[str] = Field(
        description="같은 클러스터에서 연결하면 좋은 글 주제 2~4개"
    )
    fact_check_items: list[str] = Field(
        description="사람이 1차 출처로 반드시 확인해야 할 숫자·조건·날짜 목록"
    )
    is_ymyl: bool = Field(description="대출·건강·금융·법률 등 YMYL 주제 여부")


_SYSTEM = """당신은 한국어 SEO 콘텐츠 전문가이자 편집자다.
검색 사용자의 의도를 가장 잘 충족하는, 신뢰할 수 있는 초안을 작성한다.

규칙:
- 도입부 2~3문장 안에 핵심 답을 준다(서론 늘이기 금지).
- 소제목(##), 표, 리스트로 스캔 가능하게 구성한다.
- 다른 1페이지 글에 없는 고유한 가치(구체적 예시, 비교표, 단계별 방법)를 넣는다.
- 키워드를 자연스럽게 쓰되 억지 반복(스터핑)하지 않는다.
- 사실(숫자·조건·날짜·법규)은 단정하지 말고, 사람이 확인해야 할 항목을
  fact_check_items 에 따로 모은다.
- YMYL 주제면 본문 끝에 적절한 면책 문구와 공식 출처 확인 권고를 넣는다.
- 이것은 '검수용 초안'이다. 과장·허위 없이, 검증 가능한 형태로 쓴다."""


def _slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^\w가-힣\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text.strip("-") or "draft"


def generate_brief(keyword: str, site_niche: str | None = None) -> ContentBrief:
    client = anthropic.Anthropic(api_key=config.require_api_key())

    context = f"\n대상 사이트 분야: {site_niche}" if site_niche else ""
    prompt = (
        f'대상 키워드: "{keyword}"{context}\n\n'
        f"이 키워드로 검색한 사람의 의도를 충족하는 고품질 초안을 작성하라. "
        f"검색결과 제목과 메타설명도 만들고, 사람이 확인해야 할 사실 항목을 "
        f"따로 정리하라."
    )

    response = client.messages.parse(
        model=config.MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_format=ContentBrief,
    )
    brief = response.parsed_output
    if brief is None:
        raise RuntimeError("초안 생성 실패: 모델이 스키마에 맞는 출력을 내지 못했습니다.")
    return brief


def render_markdown(brief: ContentBrief) -> str:
    """검수용 마크다운(프런트매터 + 본문 + 검수 안내)."""
    today = _dt.date.today().isoformat()
    fm = [
        "---",
        f"target_keyword: {brief.target_keyword}",
        f"title: {brief.title_tag}",
        f"slug: {brief.slug}",
        f"meta_description: {brief.meta_description}",
        f"secondary_keywords: {', '.join(brief.secondary_keywords)}",
        f"is_ymyl: {str(brief.is_ymyl).lower()}",
        f"status: draft",
        f"generated: {today}",
        "---",
        "",
    ]
    body = [brief.body_markdown, "", "---", "", "## ⚠️ 발행 전 검수 항목 (이 부분은 발행 시 삭제)", ""]
    body.append("**사실 확인 필요:**")
    for item in brief.fact_check_items:
        body.append(f"- [ ] {item}")
    body.append("")
    body.append("**내부링크 제안:**")
    for s in brief.internal_link_suggestions:
        body.append(f"- {s}")
    if brief.is_ymyl:
        body.append("")
        body.append("> YMYL 주제: 작성자/감수 정보, 공식 출처 링크, 면책 문구를 반드시 포함.")
    body.append("")
    body.append("> docs/04-콘텐츠-품질-체크리스트.md 를 통과한 뒤에만 공개로 전환할 것.")
    return "\n".join(fm + body)


def save_draft(brief: ContentBrief) -> Path:
    DRAFTS_DIR.mkdir(exist_ok=True)
    filename = f"{_slugify(brief.target_keyword)}.md"
    path = DRAFTS_DIR / filename
    path.write_text(render_markdown(brief), encoding="utf-8")
    return path
