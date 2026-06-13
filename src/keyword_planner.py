"""키워드 기회 발굴.

분야(seed)를 입력하면 Claude 가 고단가·저경쟁·검색의도가 명확한 롱테일
키워드 후보를 점수와 함께 제안한다.

⚠️ 점수는 모델의 학습 지식에 기반한 '추정치'다. 발행 우선순위를 빠르게
잡는 용도이며, 실제 검색량/경쟁은 Search Console·구글 자동완성·키워드
플래너로 검증해야 한다(docs/03 참고).
"""
from __future__ import annotations

from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from . import config

Intent = Literal["정보형", "상업조사형", "거래형", "탐색형"]


class KeywordOpportunity(BaseModel):
    keyword: str = Field(description="추천 롱테일 키워드(3~4단어 권장)")
    intent: Intent = Field(description="검색 의도 분류")
    value_score: int = Field(ge=1, le=10, description="추정 단가/수익 가치(높을수록 좋음)")
    competition_score: int = Field(ge=1, le=10, description="추정 경쟁 강도(낮을수록 좋음)")
    opportunity_score: int = Field(
        ge=1, le=10, description="종합 우선순위(가치 높고 경쟁 낮을수록 높음)"
    )
    monthly_search_estimate: str = Field(description="대략적 월 검색량 구간(예: '1천~5천')")
    content_angle: str = Field(description="1등 하기 위한 콘텐츠 각도 한 줄")
    cluster: str = Field(description="속하는 토픽 클러스터 이름")


class KeywordPlan(BaseModel):
    niche: str
    pillar_topic: str = Field(description="이 분야의 핵심 필러(허브) 주제")
    opportunities: list[KeywordOpportunity]


_SYSTEM = """당신은 한국 시장 SEO·애드센스 수익화 전문가다.
목표는 신생/저권위 블로그가 검색 1페이지에 진입할 수 있는, 고단가이면서
경쟁이 약한 롱테일 키워드를 찾는 것이다.

원칙:
- 거대 키워드(예: "대출")가 아니라 의도가 명확한 롱테일(3~4단어)을 제안한다.
- 대출/보험/세금/건강/법률처럼 광고 단가가 높은 분야를 가치 높게 평가한다.
- 대형 금융사·정부·대형 언론이 1페이지를 독점하는 키워드는 경쟁을 높게 본다.
- 하나의 토픽 클러스터로 묶일 수 있게 키워드를 설계한다(내부링크 전략).
- 한국 사용자가 실제로 검색하는 자연스러운 표현을 쓴다.
점수는 학습 지식 기반 추정치임을 전제로, 상대적 우선순위를 합리적으로 매긴다."""


def find_keywords(niche: str, count: int = 20) -> KeywordPlan:
    """분야에 대한 키워드 기회 목록을 생성한다."""
    client = anthropic.Anthropic(api_key=config.require_api_key())

    prompt = (
        f'분야: "{niche}"\n\n'
        f"위 분야에서 고단가·저경쟁·검색의도가 명확한 롱테일 키워드 "
        f"{count}개를 발굴하라. opportunity_score 내림차순으로 정렬하라. "
        f"하나의 토픽 클러스터로 묶어 내부링크가 가능하도록 설계하라."
    )

    response = client.messages.parse(
        model=config.MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_format=KeywordPlan,
    )
    plan = response.parsed_output
    if plan is None:
        raise RuntimeError("키워드 생성 실패: 모델이 스키마에 맞는 출력을 내지 못했습니다.")
    # 안전하게 우선순위 정렬
    plan.opportunities.sort(key=lambda o: o.opportunity_score, reverse=True)
    return plan


def format_plan(plan: KeywordPlan) -> str:
    """터미널 출력용 표 문자열."""
    lines = [
        f"분야: {plan.niche}",
        f"필러(허브) 주제: {plan.pillar_topic}",
        "",
        f"{'점수':>4} {'가치':>4} {'경쟁':>4}  {'의도':<6} 키워드",
        "-" * 72,
    ]
    for o in plan.opportunities:
        lines.append(
            f"{o.opportunity_score:>4} {o.value_score:>4} {o.competition_score:>4}  "
            f"{o.intent:<6} {o.keyword}"
        )
        lines.append(f"{'':>16}└ [{o.cluster}] {o.content_angle}")
    return "\n".join(lines)
