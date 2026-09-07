# -*- coding: utf-8 -*-
"""
워드프레스 카테고리를 글 주제에 맞게 고른다.

지금은 publish_targets.json 의 category_id 하나로 고정돼 있어서,
bodybalance-labo.com 에 올라가는 웰빙 글이 내용과 상관없이 전부
같은 카테고리로 들어간다.

이 파일은 **사이트에 실제로 있는 카테고리 목록을 읽어서** 고른다.
사장님이 워드프레스에서 카테고리를 새로 만들면 코드를 안 고쳐도
그쪽으로 붙는다. 티스토리 카테고리를 고친 것과 같은 방식이다.

고르는 순서
    1) 글 제목·키워드와 카테고리 이름이 겹치는지 (겹치는 글자 수로 점수)
    2) 아래 낱말표로 보정 (카테고리 이름이 짧아 안 겹칠 때가 있다)
    3) 아무것도 못 고르면 기존 category_id 를 그대로 쓴다

붙이는 법은 파일 맨 아래를 보세요.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import requests

# 카테고리 이름에 왼쪽 낱말 중 하나가 있으면, 글에 오른쪽 낱말이 있을 때 점수를 준다.
# 이름만으로 안 잡히는 경우를 메운다(예: '마음 건강' 은 '정신' 이라는 글자가 없다).
낱말표: list[tuple[tuple[str, ...], list[str]]] = [
    (("식단", "먹거리", "음식"),
     ["식단", "먹는", "음식", "식품", "레시피", "칼로리", "단백질", "탄수화물", "지방"]),
    (("영양", "보충제", "영양제"),
     ["영양", "비타민", "미네랄", "단백질", "영양제", "보충제", "결핍", "권장량"]),
    (("다이어트", "체중", "감량"),
     ["다이어트", "체중", "감량", "비만", "체지방", "칼로리"]),
    (("운동", "근력", "피트니스", "홈트"),
     ["운동", "근력", "유산소", "스트레칭", "홈트", "헬스", "근육", "걷기", "러닝", "루틴"]),
    (("수면", "잠", "휴식", "회복"),
     ["수면", "잠", "불면", "잠들", "숙면", "코골이", "낮잠", "밤"]),
    (("질환", "질병", "증상", "의학"),
     ["증상", "질환", "질병", "통증", "염증", "치료", "진단", "병원", "검사"]),
    (("정신", "마음", "심리", "멘탈", "스트레스"),
     ["스트레스", "우울", "불안", "공황", "마음", "정신", "번아웃", "자가진단", "신호"]),
    (("생활", "습관", "일상"),
     ["생활", "습관", "일상", "청소", "정리"]),
    (("장", "소화", "위"),
     ["장", "소화", "변비", "설사", "유산균", "위장"]),
    (("피부", "모발", "탈모", "뷰티"),
     ["피부", "여드름", "아토피", "보습", "탈모", "모발"]),
    (("건강",),
     ["건강", "관리", "예방", "체크"]),
]

# 낱말 끝에 붙는 조사. '수면과 회복' 의 '수면과' 를 '수면' 으로 되돌린다.
조사 = ("으로", "부터", "까지", "에서", "과", "와", "의", "에", "은", "는",
        "이", "가", "을", "를", "로", "도", "만", "및")


def _조사떼기(말: str) -> str:
    """'수면과' -> '수면'. 떼고 나서 두 글자 미만이면 그냥 둔다."""
    for 꼬리 in 조사:
        if len(말) > len(꼬리) + 1 and 말.endswith(꼬리):
            return 말[: -len(꼬리)]
    return 말


_카테고리보관: dict[str, list[dict[str, Any]]] = {}


def _낱말(글자: str) -> list[str]:
    말들 = re.sub(r"[^0-9A-Za-z가-힣]+", " ", str(글자 or "")).split()
    return [말 for 말 in 말들 if 말]


def fetch_categories(wp_url: str, username: str = "", app_password: str = "") -> list[dict[str, Any]]:
    """사이트의 카테고리 목록을 읽는다. 한 번 읽고 기억해 둔다."""
    열쇠 = str(wp_url or "").rstrip("/")
    if 열쇠 in _카테고리보관:
        return _카테고리보관[열쇠]

    주소 = f"{열쇠}/wp-json/wp/v2/categories"
    나온것: list[dict[str, Any]] = []
    try:
        인증 = (username, app_password) if username and app_password else None
        답 = requests.get(주소, params={"per_page": 100, "hide_empty": False},
                          auth=인증, timeout=20)
        답.raise_for_status()
        for 하나 in 답.json():
            이름 = str(하나.get("name", "")).strip()
            번호 = 하나.get("id")
            if not 이름 or 번호 is None:
                continue
            if 이름 in {"미분류", "Uncategorized"}:
                continue
            나온것.append({"id": int(번호), "name": 이름})
    except Exception as 오류:
        logging.warning("워드프레스 카테고리 목록을 읽지 못했습니다(%s): %s", 주소, 오류)

    _카테고리보관[열쇠] = 나온것
    if 나온것:
        logging.info("워드프레스 카테고리 %s개: %s",
                     len(나온것), ", ".join(하나["name"] for 하나 in 나온것[:12]))
    return 나온것


def 비우기() -> None:
    _카테고리보관.clear()


def _점수(카테고리이름: str, 글: str) -> int:
    """카테고리 이름이 이 글과 얼마나 맞나. 클수록 잘 맞는다."""
    점수 = 0

    # ① 카테고리 이름의 낱말이 글에 있으면 큰 점수.
    #    한국어는 조사가 붙어 '수면과' 처럼 되므로 떼고 견준다.
    #    (이걸 안 해서 '수면과 회복' 이 불면증 글을 못 잡았다)
    for 말 in _낱말(카테고리이름):
        핵 = _조사떼기(말)
        if len(핵) >= 2 and 핵 in 글:
            점수 += len(핵) * 3

    # ② 낱말표 보정. 이름에 왼쪽 낱말이 있으면 오른쪽 낱말로 점수를 매긴다.
    for 이름후보, 글후보 in 낱말표:
        if not any(하나 in 카테고리이름 for 하나 in 이름후보):
            continue
        점수 += sum(2 for 말 in 글후보 if 말 in 글)

    return 점수


def pick_category_id(
    wp_url: str,
    keyword: str,
    title: str,
    default_category_id: str | None = None,
    username: str = "",
    app_password: str = "",
) -> str | None:
    """글에 어울리는 카테고리 번호. 못 고르면 기존 값을 그대로 돌려준다."""
    if os.getenv("WP_CATEGORY_AUTO", "1").strip().lower() in {"0", "false", "no", "off"}:
        return default_category_id

    카테고리들 = fetch_categories(wp_url, username, app_password)
    if not 카테고리들:
        return default_category_id

    글 = f"{keyword} {title}"
    점수매김 = [(_점수(하나["name"], 글), 하나) for 하나 in 카테고리들]
    점수매김.sort(key=lambda 짝: 짝[0], reverse=True)
    최고점, 고른것 = 점수매김[0]

    if 최고점 <= 0:
        logging.info("어울리는 워드프레스 카테고리를 못 찾아 기본값을 씁니다: %s", default_category_id)
        return default_category_id

    logging.info("워드프레스 카테고리 선택: [%s] %s (점수 %s)",
                 고른것["id"], 고른것["name"], 최고점)
    return str(고른것["id"])


# ══════════════════════════════════════════════════════
#  [파이프라인에 붙이는 법]  blog_publish_pipeline.py
#
#  ① 파일 위쪽 import 줄 아래에
#         import wp_category
#
#  ② save_wordpress_draft 안, payload 를 만들기 직전에 한 줄
#         category_id = wp_category.pick_category_id(
#             wp_url, item.keyword, item.title, category_id, username, app_password)
#
#  끄고 싶으면 .env 에 WP_CATEGORY_AUTO=0
# ══════════════════════════════════════════════════════
