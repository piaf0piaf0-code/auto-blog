# -*- coding: utf-8 -*-
"""
꿈해몽 글에 이미 발행한 꿈 글 링크를 넣는다.

왜 따로 파일인가
    blog_content_pipeline.py 를 직접 고치면 손보신 내용과 부딪힌다.
    이 파일은 혼자 돌고, 파이프라인에는 세 줄만 붙이면 된다.

어디서 목록을 가져오나
    1) 티스토리 RSS  (seaga4.tistory.com/rss)  - 손으로 올린 옛 글까지
    2) 구글시트 '6.완료꿈'                      - 자동화로 올린 글
    둘을 합치고 같은 주소는 하나로 친다.

무엇을 넣나
    이번 글 키워드와 낱말이 겹치는 글 2~3개를 골라, 본문에 이렇게 넣는다.

        더 자세히 보기 → (글 제목) 바로가기

붙이는 법은 이 파일 맨 아래 [파이프라인에 붙이는 법] 을 보세요.
"""

from __future__ import annotations

import html as html_lib
import logging
import os
import re
import xml.etree.ElementTree as ET
from typing import Any, Iterable

import requests

# ── 설정 ──────────────────────────────────────────────
꿈블로그 = os.getenv("DREAM_BLOG_URL", "https://seaga4.tistory.com").rstrip("/")
완료시트이름 = os.getenv("DREAM_DONE_SHEET", "6.완료꿈")
넣을링크수 = int(os.getenv("DREAM_LINK_COUNT", "3"))

# 어느 글에나 걸릴 만한 흔한 말. 이것만 겹치면 관련 글로 보지 않는다.
# '꿈' 은 모든 글에 있으니 반드시 빼야 한다.
흔한말 = {
    "꿈", "해몽", "꿈해몽", "의미", "해석", "정리", "총정리", "뜻", "무슨", "어떤",
    "경우", "상황", "상황별", "이야기", "관련", "대한", "하는", "보는", "나오는", "꾸는",
    "길몽", "흉몽", "심리", "예지", "전조", "이것", "그것", "여기", "거기",
    "이", "그", "저", "것", "수", "때", "등", "및", "더", "좀", "안", "못",
}

_글목록: list[dict[str, str]] | None = None


# ── 목록 모으기 ────────────────────────────────────────

def _rss에서(blog_url: str) -> list[dict[str, str]]:
    주소 = f"{blog_url}/rss"
    try:
        답 = requests.get(주소, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        })
        답.raise_for_status()
        뿌리 = ET.fromstring(답.content)
    except Exception as 오류:
        logging.warning("꿈해몽 RSS 를 읽지 못했습니다(%s): %s", 주소, 오류)
        return []

    나온것 = []
    for 항목 in 뿌리.iter("item"):
        제목 = (항목.findtext("title") or "").strip()
        링크 = (항목.findtext("link") or "").strip()
        if 제목 and 링크:
            나온것.append({"제목": 제목, "링크": 링크})
    logging.info("꿈해몽 RSS 에서 글 %s개를 읽었습니다.", len(나온것))
    return 나온것


def _시트에서(spreadsheet: Any) -> list[dict[str, str]]:
    if spreadsheet is None:
        return []
    try:
        시트 = spreadsheet.worksheet(완료시트이름)
        값들 = 시트.get_all_values()
    except Exception as 오류:
        logging.info("'%s' 시트를 읽지 못했습니다(없어도 괜찮습니다): %s", 완료시트이름, 오류)
        return []
    if len(값들) < 2:
        return []

    머리 = [str(칸).strip() for 칸 in 값들[0]]

    def 칸번호(후보: Iterable[str], 기본: int) -> int:
        for 이름 in 후보:
            if 이름 in 머리:
                return 머리.index(이름)
        return 기본

    제목칸 = 칸번호(["제목", "SEO 최적화 제목"], 2)
    링크칸 = 칸번호(["링크", "워드프레스 초안 URL", "URL", "결과 URL"], 3)

    나온것 = []
    for 줄 in 값들[1:]:
        제목 = str(줄[제목칸]).strip() if 제목칸 < len(줄) else ""
        링크 = str(줄[링크칸]).strip() if 링크칸 < len(줄) else ""
        if not 제목 or not 링크.startswith("http"):
            continue
        if "tistory.com" not in 링크:      # 워드프레스 주소는 건너뛴다
            continue
        나온것.append({"제목": 제목, "링크": 링크})
    logging.info("'%s' 시트에서 글 %s개를 읽었습니다.", 완료시트이름, len(나온것))
    return 나온것


def 불러오기(spreadsheet: Any = None, blog_url: str = "") -> list[dict[str, str]]:
    """발행한 꿈 글 목록을 만든다. 한 번만 읽고 그 뒤로는 기억해 둔다."""
    global _글목록
    if _글목록 is not None:
        return _글목록

    주소 = (blog_url or 꿈블로그).rstrip("/")
    모은것 = _rss에서(주소) + _시트에서(spreadsheet)

    본링크: set[str] = set()
    정리: list[dict[str, str]] = []
    for 하나 in 모은것:
        열쇠 = 하나["링크"].split("?")[0].rstrip("/")
        if 열쇠 in 본링크:
            continue
        본링크.add(열쇠)
        정리.append(하나)

    _글목록 = 정리
    logging.info("꿈해몽 내부 링크 후보: 글 %s개", len(정리))
    return 정리


def 비우기() -> None:
    """새로 읽고 싶을 때 부른다(시험용)."""
    global _글목록
    _글목록 = None


# ── 관련 글 고르기 ─────────────────────────────────────

def _낱말(글자: str) -> set[str]:
    """제목을 낱말로 쪼갠다.

    한국어는 조사가 붙어서 '고양이' 와 '고양이가' 가 다른 말로 잡힌다.
    그래서 여기서는 그대로 두고, 견줄 때 서로 품고 있는지를 본다(_겹치나).
    '뱀·물·불·집·돈' 처럼 한 글자짜리 꿈 주제가 많아 한 글자도 살린다.
    대신 '이·그·것' 같은 것은 흔한말 에 넣어 걸러 낸다.
    """
    말들 = re.sub(r"[^0-9A-Za-z가-힣]+", " ", str(글자 or "")).split()
    나온것 = set()
    for 말 in 말들:
        if 말 in 흔한말:
            continue
        if re.fullmatch(r"[가-힣]+", 말):      # 한글은 한 글자도 살린다
            나온것.add(말)
        elif len(말) >= 2:                      # 영문·숫자는 두 글자부터
            나온것.add(말)
    return 나온것


def _겹치나(내말: str, 남말: str) -> bool:
    """조사가 붙어도 같은 말로 본다. 두 글자 이상일 때만 품기를 허용한다."""
    if 내말 == 남말:
        return True
    if len(내말) >= 2 and 내말 in 남말:
        return True
    if len(남말) >= 2 and 남말 in 내말:
        return True
    return False


def 관련글고르기(
    keyword: str,
    title: str = "",
    limit: int = 0,
    spreadsheet: Any = None,
) -> list[dict[str, str]]:
    """이번 글과 낱말이 겹치는 지난 글을 겹치는 순으로 고른다."""
    limit = limit or 넣을링크수
    후보들 = 불러오기(spreadsheet)
    if not 후보들:
        return []

    내낱말 = _낱말(f"{keyword} {title}")
    if not 내낱말:
        return []
    내열쇠 = re.sub(r"\s+", "", f"{title or keyword}")

    점수매김 = []
    for 하나 in 후보들:
        # 지금 쓰는 글과 같은 글이면 건너뛴다
        if re.sub(r"\s+", "", 하나["제목"]) == 내열쇠:
            continue
        남낱말 = _낱말(하나["제목"])
        겹침 = {내말 for 내말 in 내낱말
                if any(_겹치나(내말, 남말) for 남말 in 남낱말)}
        if not 겹침:
            continue
        점수매김.append((len(겹침), max(len(말) for 말 in 겹침), 하나))

    점수매김.sort(key=lambda 짝: (짝[0], 짝[1]), reverse=True)
    return [하나 for _, _, 하나 in 점수매김[:limit]]


# ── 프롬프트에 넣을 글 ─────────────────────────────────

def 요청문블록(관련글: list[dict[str, str]]) -> str:
    if not 관련글:
        return ""
    줄 = ["[본문에 반드시 넣을 지난 글 링크]",
          "아래 글을 본문 흐름에 맞는 자리에 자연스럽게 넣어 주세요.",
          "형식은 이대로 쓰고, 주소는 한 글자도 바꾸지 마세요.",
          ""]
    for 하나 in 관련글:
        줄.append(f"더 자세히 보기 → [{하나['제목']} 바로가기]({하나['링크']})")
    줄 += ["",
           "- 한 자리에 몰아넣지 말고, 그 내용이 나오는 대목 바로 아래에 하나씩 두세요.",
           "- 목록에 없는 주소를 새로 지어내지 마세요."]
    return "\n".join(줄)


# ── 봇이 빠뜨렸을 때 직접 넣기 ─────────────────────────

def 링크채우기(html: str, 관련글: list[dict[str, str]]) -> tuple[str, list[str]]:
    """본문에 없는 링크를 '최종 정리하면,' 앞에 붙인다."""
    본문 = str(html or "")
    if not 관련글:
        return 본문, []

    빠진것 = [하나 for 하나 in 관련글 if 하나["링크"] not in 본문]
    if not 빠진것:
        return 본문, []

    덩어리 = ["<h2>함께 보면 좋은 꿈해몽</h2>"]
    for 하나 in 빠진것:
        제목 = html_lib.escape(하나["제목"])
        주소 = html_lib.escape(하나["링크"], quote=True)
        덩어리.append(
            f'<p>더 자세히 보기 → <a href="{주소}" target="_blank" '
            f'rel="noopener">{제목} 바로가기</a></p>'
        )
    넣을것 = "\n".join(덩어리)

    자리 = re.search(r"<h2[^>]*>\s*최종\s*정리하면", 본문)
    if 자리:
        본문 = 본문[: 자리.start()] + 넣을것 + "\n" + 본문[자리.start() :]
    else:
        본문 = 본문 + "\n" + 넣을것
    return 본문, [하나["제목"] for 하나 in 빠진것]


# ══════════════════════════════════════════════════════
#  [파이프라인에 붙이는 법]  blog_content_pipeline.py
#
#  ① 파일 위쪽 import 줄 아래에
#         import dream_links
#
#  ② build_dream_user_prompt_v2 안, `참고 자료:` 를 만드는 곳 아래에
#         관련글 = dream_links.관련글고르기(keyword, "")
#         링크블록 = dream_links.요청문블록(관련글)
#     그리고 f""" ... """ 안 [이 글의 짜임] 바로 앞에
#         {링크블록}
#     를 한 줄 넣는다.
#
#  ③ 글이 만들어진 뒤(오늘작성에 쓰기 직전)
#         관련글 = dream_links.관련글고르기(item.keyword, article.title)
#         새본문, 채운것 = dream_links.링크채우기(article.html, 관련글)
#         if 채운것:
#             logging.info("꿈해몽 지난 글 링크 %s개를 직접 넣었습니다: %s",
#                          len(채운것), 채운것)
#             article = replace(article, html=새본문)
#
#  시트까지 보려면 ② ③ 의 관련글고르기에 spreadsheet=spreadsheet 를 넘긴다.
#  안 넘겨도 RSS 만으로 돌아간다.
# ══════════════════════════════════════════════════════
