# -*- coding: utf-8 -*-
"""
글을 두 단계로 나눠 쓴다 — 두루뭉술한 글을 막기 위한 것.

왜 만들었나
    지금 프롬프트는 재료 459자로 7,000자를 쓰라고 한다(15.3배).
    거기에 '지어내지 마라' 까지 걸려 있으니, 모델이 할 수 있는 건
    같은 말을 표현만 바꿔 반복하는 것뿐이다. 두루뭉술은 결과가 아니라 산수다.

    더 결정적인 것은 이 줄이었다.
        "AI가 새로 만든 질문이 아니라, 제공된 롱테일 키워드를 글의 뼈대로 쓰세요"
    독자 질문에서 목차를 만드는 것을 대놓고 금지한다. 그래서 H2 가
    질문이 아니라 '키워드를 넣을 자리' 가 된다. 질문이 아니니 답도 없다.

어떻게 바꿨나
    1차 호출  재료를 주고 "이 재료로 답할 수 있는 질문" 만 뽑게 한다.
              재료에 없는 질문은 여기서 저절로 사라진다.
              동시에 글의 갈래를 정하게 한다.
                info  = 재료에 구체적 사실이 있다  → 정보 글
                howto = 재료가 얇다               → '확인하는 방법' 글
    2차 호출  그 질문들에 답하는 글을 쓰게 한다. 분량을 강요하지 않는다.

    재료가 얇을 때 두루뭉술해지지 않는 방법은 사실을 더 넣는 게 아니라
    **쓸 것을 바꾸는 것**이다. 금액·기간은 못 써도 절차는 지어내지 않고
    구체적으로 쓸 수 있다. 어디에 물을지, 뭐라고 물을지, 뭘 준비할지.

켜는 법
    .env 에서 카테고리별로 켠다. 비워 두면(기본) 지금 방식 그대로 돈다.
        ARTICLE_V2_CATEGORIES=정책지원,최신이슈
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

# ── 켜고 끄기 ─────────────────────────────────────────

def enabled_for(category: str) -> bool:
    값 = os.getenv("ARTICLE_V2_CATEGORIES", "").strip()
    if not 값:
        return False
    이름 = str(category or "").strip()
    return any(하나.strip() and 하나.strip() in 이름 for 하나 in 값.split(","))


# ── 1차: 답할 수 있는 질문만 뽑는다 ────────────────────

질문뽑기시스템 = """
당신은 한국어 검색 의도 분석가입니다.
사람들이 검색창에 실제로 치는 말과, 주어진 자료로 답할 수 있는 것을 가려냅니다.
출력은 JSON 하나만. 설명을 붙이지 마세요.
""".strip()


def build_question_prompt(keyword: str, category: str, longtails: str, material: str) -> str:
    return f"""
검색 키워드: {keyword}
분야: {category}
관련 검색어: {longtails or "(없음)"}

[가진 자료]
{material}

[할 일]

1) 이 키워드로 검색해 들어온 사람이 알고 싶어 할 질문을 10개 떠올리세요.
   검색창에 치는 말투로 쓰세요. "무엇인가" 같은 사전식 질문은 빼세요.
   좋은 예: 얼마까지 받을 수 있나 / 나도 대상인가 / 어디에 신청하나 / 얼마나 걸리나
   나쁜 예: 이 제도의 의의는 / 향후 전망은 / 왜 주목받는가

2) 그중 **위 자료로 구체적으로 답할 수 있는 것만** 남기세요.
   "상황에 따라 다릅니다", "공식 사이트에서 확인해야 합니다" 로밖에
   답할 수 없는 질문은 버리세요. 그런 질문은 글에 넣어도 소용이 없습니다.

3) 남은 질문이 3개 미만이면 mode 를 "howto" 로 하세요.

[mode 정하기]
"info"   자료에 금액·기간·조건·기관명 같은 구체적 사실이 있다.
         → 그 사실을 정리하는 정보 글을 씁니다.
"howto"  자료가 얇다. 또는 지역·기관마다 달라서 하나로 정리할 수 없다.
         → 제도 설명 대신 '내가 해당되는지 확인하는 방법' 글을 씁니다.
         이쪽이 오히려 독자에게 쓸모 있습니다.

[출력 형식]
{{
  "mode": "info" 또는 "howto",
  "why": "그렇게 정한 이유 한 줄",
  "questions": ["질문1", "질문2", "..."],
  "facts": ["자료에서 확인한 구체적 사실 (금액·기간·기관명 등). 없으면 빈 배열"]
}}
""".strip()


def parse_questions(raw: str) -> dict[str, Any]:
    글 = str(raw or "").strip()
    m = re.search(r"\{[\s\S]*\}", 글)
    if m:
        글 = m.group(0)
    try:
        값 = json.loads(글)
    except Exception as 오류:
        raise ValueError(f"질문 뽑기 응답을 읽지 못했습니다: {오류}")

    mode = str(값.get("mode", "")).strip().lower()
    if mode not in {"info", "howto"}:
        mode = "info"
    질문들 = [str(하나).strip() for 하나 in (값.get("questions") or []) if str(하나).strip()]
    사실들 = [str(하나).strip() for 하나 in (값.get("facts") or []) if str(하나).strip()]
    if len(질문들) < 3:
        mode = "howto"
    return {"mode": mode, "questions": 질문들[:7], "facts": 사실들[:12],
            "why": str(값.get("why", "")).strip()}


# ── 2차: 그 질문에 답하는 글 ───────────────────────────

글쓰기시스템 = """
당신은 한국어 블로그 글을 쓰는 사람입니다.
독자는 답을 찾으러 검색해 들어왔습니다. 배경 설명이 아니라 답을 먼저 줍니다.

반드시 지킬 것
- 주어진 자료에 없는 날짜·금액·조건·수치를 지어내지 마세요.
- "확인해야 합니다" 로 문장을 끝내지 마세요.
  반드시 **어디서 어떻게** 확인하는지 같이 적으세요.
  나쁨: 신청기간은 공고문에서 확인해야 합니다.
  좋음: 신청기간은 구청 홈페이지 '고시·공고' 게시판에 올라옵니다. 없으면 복지정책과에 전화하세요.
- "~인 경우가 많습니다", "~에 따라 다릅니다", "다양합니다" 로 문단을 채우지 마세요.
  다르다면 **무엇에 따라 어떻게 다른지** 적으세요.
- 분량을 채우려고 같은 말을 표현만 바꿔 반복하지 마세요.
  쓸 것이 없으면 짧게 끝내는 편이 낫습니다.
- HTML 은 <p>, <h2>, <ul>, <li>, <strong>, <a>, <table> 만 쓰세요.
- 마지막 소제목은 반드시 '최종 정리하면,' 입니다.
- 출력은 JSON 하나만. 설명을 붙이지 마세요.
""".strip()

_정보뼈대 = """
[글의 뼈대]
- 도입 2~3문단. 이 글이 어떤 질문에 답하는지 바로 밝히세요.
- 위 질문 하나가 <h2> 하나입니다. 질문 순서대로 쓰세요.
- **각 <h2> 의 첫 문장이 그 질문의 답**이어야 합니다. 배경 설명으로 시작하지 마세요.
- 답한 뒤에 근거와 주의할 점을 이어 쓰세요.
- 조건이 둘 이상이거나 금액·기간을 견줄 때는 목록이나 표로 만드세요.
- [자료에서 확인한 사실] 에 있는 값은 그대로 쓰세요. 바꾸지 마세요.
"""

_방법뼈대 = """
[글의 뼈대]
이 주제는 지역·기관마다 달라서 하나로 정리할 수 없습니다.
그래서 '제도 설명' 이 아니라 **'내가 해당되는지 확인하는 방법'** 을 씁니다.
금액과 기간은 쓰지 않습니다. 대신 아래는 지어내지 않고도 구체적으로 쓸 수 있습니다.

- 도입 2~3문단. **전국 공통 제도가 아니라는 사실을 먼저** 알리세요.
  그래야 독자가 헛다리를 안 짚습니다.
- <h2> 어디에 물어봐야 하는가
    기관을 우선순위대로 목록으로. 왜 그 순서인지 한 줄씩.
- <h2> 뭐라고 물어봐야 하는가
    **실제로 말할 문장을 인용문으로 적으세요.** 이게 이 글의 핵심입니다.
    같은 사업이 다른 이름으로 불릴 수 있으니 함께 말할 단어도 알려주세요.
- <h2> 미리 준비할 것
    표로. 각 항목이 왜 필요한지 함께.
- <h2> 함께 물어보면 좋은 것
    같은 창구에서 처리되는 인접 제도.
- <h2> 해당되지 않는다고 하면
    다음 수를 구체적으로. 광역 단위, 민간 지원, 대기 등록 등.
- <h2>최종 정리하면,
"""


def build_article_prompt(
    keyword: str,
    plan: dict[str, Any],
    material: str,
    extra_rules: str = "",
) -> str:
    질문줄 = "\n".join(f"{번호}. {질문}" for 번호, 질문 in enumerate(plan["questions"], 1)) \
        or "(답할 수 있는 질문이 없습니다)"
    사실줄 = "\n".join(f"- {하나}" for 하나 in plan["facts"]) or "(자료에서 확인된 구체적 사실 없음)"
    뼈대 = _방법뼈대 if plan["mode"] == "howto" else _정보뼈대

    return f"""
포커스 키워드: {keyword}

[이 글이 답할 질문]
{질문줄}

[자료에서 확인한 사실 — 이 값만 쓰세요]
{사실줄}

[원본 자료]
{material}
{뼈대}
[분량]
자료가 허락하는 만큼만 쓰세요. 2,500자로 끝나면 그게 맞습니다.
억지로 늘리지 마세요. 늘려야 한다면 사례나 경우를 더하세요.
{extra_rules}
[출력 형식]
{{
  "title": "50자 내외. 포커스 키워드 '{keyword}' 포함. 검색해서 클릭할 만한 제목",
  "meta": "120~160자. 첫 문장에 '{keyword}' 포함. 끝에 ' | tags: 태그1, 태그2, ...' 8~12개",
  "html": "<p>로 시작하는 본문"
}}
JSON 안의 큰따옴표는 이스케이프하고, 잘리지 않는 완전한 JSON으로 끝내세요.
""".strip()


# ── 모호함 재기 ────────────────────────────────────────

_모호표현 = [
    r"확인해야\s*합니다", r"확인이\s*필요합니다", r"다를\s*수\s*있습니다",
    r"경우가\s*많습니다", r"사례가\s*(있|보고)", r"다양합니다",
    r"상이합니다", r"달라집니다", r"고\s*있습니다\.", r"논의(가|되고)",
    r"중요합니다", r"바람직합니다", r"주목받", r"전망입니다",
]
_숫자 = re.compile(r"\d[\d,\.]*\s*(원|만원|억|%|퍼센트|일|개월|년|명|건|회|세)")


def measure_vagueness(html: str) -> dict[str, Any]:
    """1,000자당 모호표현과 구체 숫자를 센다. 낮을수록 좋다."""
    글 = re.sub(r"<[^>]+>", " ", str(html or ""))
    글 = re.sub(r"\s+", " ", 글).strip()
    길이 = max(len(글), 1)
    모호 = sum(len(re.findall(패턴, 글)) for 패턴 in _모호표현)
    숫자 = len(_숫자.findall(글))
    링크 = len(re.findall(r"<a\b", str(html or ""), re.I))
    목록 = len(re.findall(r"<li\b", str(html or ""), re.I))
    return {
        "글자수": 길이,
        "모호표현": 모호,
        "구체숫자": 숫자,
        "링크": 링크,
        "목록": 목록,
        "모호밀도": round(모호 * 1000 / 길이, 2),
        "숫자밀도": round(숫자 * 1000 / 길이, 2),
    }


def too_vague(잰것: dict[str, Any], mode: str) -> bool:
    """다시 쓰라고 할 만큼 두루뭉술한가."""
    # 너무 짧으면 재기 자체가 흔들린다. 다만 문턱을 높이 잡으면
    # 짧으면서 두루뭉술한 글이 그냥 통과한다(시험에서 실제로 그랬다).
    if 잰것["글자수"] < 300:
        return False
    # 1,000자당 모호표현 4개를 넘으면 문제로 본다.
    if 잰것["모호밀도"] > 4.0:
        return True
    # 정보 글인데 구체 숫자가 거의 없으면 알맹이가 없는 것이다.
    if mode == "info" and 잰것["숫자밀도"] < 0.5 and 잰것["글자수"] > 1500:
        return True
    return False


다시쓰기지시 = """

[다시 쓰기]
방금 글이 두루뭉술합니다. 아래를 고쳐 다시 쓰세요.
- "확인해야 합니다", "다를 수 있습니다", "경우가 많습니다" 로 끝나는 문장을 모두 없애세요.
  대신 **어디서 어떻게** 확인하는지, **무엇에 따라 어떻게** 다른지 적으세요.
- 자료에서 확인되지 않는 대목은 늘리지 말고 **그 소제목을 통째로 빼세요.**
- 짧아져도 좋습니다. 알맹이 있는 2,000자가 두루뭉술한 8,000자보다 낫습니다.
"""


# ── 두 단계를 한 번에 ──────────────────────────────────

def generate(
    call_openai: Any,
    parse_article: Any,
    keyword: str,
    category: str,
    longtails: str,
    material: str,
    extra_rules: str = "",
) -> tuple[Any, dict[str, Any]]:
    """
    call_openai(system_prompt=..., user_prompt=...) -> str
    parse_article(raw) -> GeneratedArticle
    두 개를 넘겨받아 쓴다. 이 파일이 파이프라인에 매이지 않게 하려는 것.
    """
    # 1차 — 답할 수 있는 질문만
    raw = call_openai(
        system_prompt=질문뽑기시스템,
        user_prompt=build_question_prompt(keyword, category, longtails, material),
    )
    plan = parse_questions(raw)
    logging.info(
        "[v2] 갈래=%s 질문=%s개 확인된사실=%s개 · %s",
        plan["mode"], len(plan["questions"]), len(plan["facts"]), plan["why"])
    for 질문 in plan["questions"]:
        logging.info("[v2]   Q. %s", 질문)

    # 2차 — 그 질문에 답하는 글
    글프롬프트 = build_article_prompt(keyword, plan, material, extra_rules)
    raw2 = call_openai(system_prompt=글쓰기시스템, user_prompt=글프롬프트)
    article = parse_article(raw2)

    잰것 = measure_vagueness(article.html)
    logging.info("[v2] 1차 결과 %s", 잰것)

    if too_vague(잰것, plan["mode"]):
        logging.info("[v2] 두루뭉술해서 한 번 다시 씁니다.")
        raw3 = call_openai(
            system_prompt=글쓰기시스템,
            user_prompt=글프롬프트 + 다시쓰기지시,
        )
        다시 = parse_article(raw3)
        다시잰것 = measure_vagueness(다시.html)
        logging.info("[v2] 2차 결과 %s", 다시잰것)
        # 나아졌을 때만 바꾼다. 더 나빠지면 1차를 쓴다.
        if 다시잰것["모호밀도"] < 잰것["모호밀도"]:
            article, 잰것 = 다시, 다시잰것
        else:
            logging.info("[v2] 다시 쓴 글이 더 낫지 않아 처음 것을 씁니다.")

    잰것["mode"] = plan["mode"]
    잰것["questions"] = plan["questions"]
    return article, 잰것
