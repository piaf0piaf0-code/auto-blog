"""
Blog automation stage 2-4: OpenAI SEO writing from the "오늘작성" queue.

Workflow:
    1. In source sheets, put 1 or TRUE in column A for rows to publish.
    2. In Google Sheets, run the "선택완료" Apps Script button/menu.
    3. Selected rows move to "오늘작성" with status "글쓰기대기".
    4. This script generates SEO title, meta/tags, and HTML body.
    5. The same "오늘작성" row is updated and status becomes "발행대기".

.env:
    OPENAI_API_KEY=your_openai_api_key
    GOOGLE_SERVICE_ACCOUNT_FILE=C:/Users/kypia/Downloads/auto-blog-499701-b865809a9521.json
    SPREADSHEET_ID=15bzAktttxB3aQNwzWnAvWhtIDiyL1NhIKlZpASUrnT8
    OPENAI_MODEL=gpt-5
    MAX_APPROVED_ROWS=3
"""

from __future__ import annotations

import argparse
from difflib import SequenceMatcher
import html as html_lib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse, urlunparse

import gspread
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from gspread.exceptions import WorksheetNotFound
from openai import OpenAI
from serpapi_budget import reserve_serpapi_slot

# 꿈해몽 글에 지난 글 링크를 넣는 도우미. 파일이 없어도 나머지는 그대로 돈다.
try:
    import article_v2
except Exception as _v2오류:  # pragma: no cover
    article_v2 = None
    logging.getLogger(__name__).info(
        "article_v2.py 가 없어 두 단계 글쓰기는 건너뜁니다: %s", _v2오류)

try:
    import dream_links
except Exception as _꿈링크오류:  # pragma: no cover
    dream_links = None
    logging.getLogger(__name__).info(
        "dream_links.py 가 없어 꿈해몽 지난 글 링크는 건너뜁니다: %s", _꿈링크오류)


SPREADSHEET_ID = "15bzAktttxB3aQNwzWnAvWhtIDiyL1NhIKlZpASUrnT8"
DEFAULT_MODEL = "gpt-5"

TODAY_SHEET = "오늘작성"
TODAY_HEADERS = [
    "원본 메인 키워드",
    "카테고리",
    "워드프레스 롱테일",
    "SEO 최적화 제목",
    "메타 디스크립션 및 태그",
    "HTML 본문 내용",
    "작업 상태",
    "대표 링크/결과 URL",
]

TISTORY_LONGTAIL_HEADER = "티스토리 롱테일"
TISTORY_TITLE_HEADER = "티스토리 SEO 제목"
TISTORY_META_HEADER = "티스토리 메타 디스크립션 및 태그"
TISTORY_HTML_HEADER = "티스토리 HTML 본문"
OPTIONAL_TODAY_HEADERS = [
    TISTORY_LONGTAIL_HEADER,
    TISTORY_TITLE_HEADER,
    TISTORY_META_HEADER,
    TISTORY_HTML_HEADER,
]

READY_VALUES = {"글쓰기대기", "승인", "대기", "TRUE", "True", "true", "1"}
WRITING_DONE_VALUE = "발행대기"


@dataclass(frozen=True)
class WritingQueueItem:
    worksheet: Any
    row_number: int
    keyword: str
    category: str
    status_col: int
    wordpress_longtails: str = ""
    source_link: str = ""
    tistory_longtails: str = ""


@dataclass(frozen=True)
class GeneratedArticle:
    title: str
    meta: str
    html: str


@dataclass(frozen=True)
class ResearchContext:
    link: str
    snippets: str
    source_notes: str


def normalize_cell(value: Any) -> str:
    return str(value or "").strip()


def header_index(headers: list[str], candidates: list[str], fallback: int) -> int:
    for candidate in candidates:
        if candidate in headers:
            return headers.index(candidate) + 1
    return fallback


def column_letter(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def ensure_optional_today_headers(worksheet: Any) -> None:
    headers = worksheet.row_values(1)
    missing = [header for header in OPTIONAL_TODAY_HEADERS if header not in headers]
    if not missing:
        return
    start_col = len(headers) + 1
    end_col = start_col + len(missing) - 1
    worksheet.update(
        range_name=f"{column_letter(start_col)}1:{column_letter(end_col)}1",
        values=[missing],
        value_input_option="USER_ENTERED",
    )


def ensure_today_sheet(spreadsheet):
    try:
        worksheet = spreadsheet.worksheet(TODAY_SHEET)
    except WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=TODAY_SHEET, rows=1000, cols=len(TODAY_HEADERS))

    headers = worksheet.row_values(1)
    if headers and "워드프레스 롱테일" not in headers and "SEO 최적화 제목" in headers:
        worksheet.insert_cols([["워드프레스 롱테일"]], col=3)
        headers = worksheet.row_values(1)
    if headers[: len(TODAY_HEADERS)] != TODAY_HEADERS:
        worksheet.update(range_name="A1:H1", values=[TODAY_HEADERS])
    ensure_optional_today_headers(worksheet)
    return worksheet


def read_today_values_with_retry(today_sheet, attempts: int = 5) -> list[list[str]]:
    """Read the queue once, tolerating short-lived Sheets connection failures."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return today_sheet.get_all_values()
        except Exception as exc:
            last_error = exc
            if attempt == attempts:
                break
            delay = min(20, 2 ** attempt)
            logging.warning("오늘작성 시트 읽기 실패, %s초 후 재시도합니다 (%s/%s): %s", delay, attempt, attempts, exc)
            time.sleep(delay)
    raise RuntimeError(f"오늘작성 시트를 읽지 못했습니다. 잠시 후 다시 실행하세요: {last_error}")


def collect_writing_queue(
    today_sheet,
    limit: int,
    include_categories: set[str] | None = None,
    exclude_categories: set[str] | None = None,
    values: list[list[str]] | None = None,
) -> list[WritingQueueItem]:
    values = values if values is not None else read_today_values_with_retry(today_sheet)
    if len(values) < 2:
        return []

    headers = values[0]
    keyword_col = header_index(headers, ["원본 메인 키워드", "메인 키워드", "기본키워드"], 1)
    category_col = header_index(headers, ["카테고리", "주제"], 2)
    longtail_col = header_index(headers, ["워드프레스 롱테일", "롱테일 키워드", "롱테일"], 3)
    tistory_longtail_col = header_index(headers, [TISTORY_LONGTAIL_HEADER, "티스토리용 롱테일"], 9)
    title_col = header_index(headers, ["SEO 최적화 제목", "SEO 제목"], 4)
    html_col = header_index(headers, ["HTML 본문 내용", "HTML 본문"], 6)
    status_col = header_index(headers, ["작업 상태", "상태"], 7)
    link_col = header_index(headers, ["대표 링크/결과 URL", "대표 링크", "링크"], 8)

    queue: list[WritingQueueItem] = []
    for row_number, row in enumerate(values[1:], start=2):
        keyword = normalize_cell(row[keyword_col - 1] if keyword_col <= len(row) else "")
        category = normalize_cell(row[category_col - 1] if category_col <= len(row) else "")
        title = normalize_cell(row[title_col - 1] if title_col <= len(row) else "")
        html = normalize_cell(row[html_col - 1] if html_col <= len(row) else "")
        status = normalize_cell(row[status_col - 1] if status_col <= len(row) else "")
        wordpress_longtails = normalize_cell(row[longtail_col - 1] if longtail_col <= len(row) else "")
        tistory_longtails = normalize_cell(row[tistory_longtail_col - 1] if tistory_longtail_col <= len(row) else "")
        source_link = normalize_cell(row[link_col - 1] if link_col <= len(row) else "")

        if not keyword or not category:
            continue
        if include_categories is not None and category not in include_categories:
            continue
        if exclude_categories is not None and category in exclude_categories:
            continue
        if title or html:
            continue
        if status and status not in READY_VALUES:
            continue

        queue.append(
            WritingQueueItem(
                today_sheet,
                row_number,
                keyword,
                category,
                status_col,
                wordpress_longtails,
                source_link,
                tistory_longtails,
            )
        )
        if len(queue) >= limit:
            break

    return queue


def system_prompt_for_category(category: str) -> str:
    traffic_prompt = """
당신은 한국어 SEO 블로그 작가입니다.
목표는 검색자가 실제로 찾는 답을 빠르게 주면서 체류 시간을 늘리는 것입니다.
문체는 호기심을 유발하되, 내용은 일정/기간/조건/신청 위치/주의사항이 구체적인 정보형 블로그 스타일입니다.
WordPress와 Tistory에 바로 붙여넣을 수 있는 깨끗한 HTML만 작성하세요.
사용 가능한 태그는 <h2>, <h3>, <p>, <ul>, <li>, <strong>, <blockquote>, <a>, <table>, <thead>, <tbody>, <tr>, <th>, <td>입니다.
본문 초반은 기계적인 질문 목록이 아니라, 사람이 직접 설명하듯 자연스러운 도입 문단으로 시작하세요.
일정, 접수 기간, 자격 조건, 신청 방법은 실제로 그런 절차가 있는 주제에서만 포함하세요.
논란/의혹/쟁점 글은 왜 논란이 됐는지와 문제가 된 구체 내용을 먼저 설명하세요.
정확한 사실이 확인되지 않는 경우에는 확인된 사실과 추측을 구분하고, 어떤 점을 추가 확인해야 하는지 구체적으로 쓰세요.
본문 중간과 끝부분에 대표 링크를 실제 URL로 넣으세요. 빈 링크나 ##LINK_HERE##는 절대 쓰지 마세요.
"""

    health_prompt = """
당신은 한국어 건강 콘텐츠 에디터입니다.
문체는 신뢰감 있고 읽기 쉬워야 합니다.
진단을 단정하지 말고, 증상이 심하거나 오래가면 전문가 상담을 권하세요.
HTML 본문에는 <h2>, <h3>, <p>, <ul>, <li>, <strong>, <blockquote>, <div class="summary">를 사용하세요.
중요 문단 뒤에는 짧은 요약 박스를 넣어 체류 시간을 높이세요.
증상, 원인, 병원 방문 기준, 검사/상담 시 확인할 항목, 공식 건강정보 링크를 반드시 포함하세요.
본문은 실제 독자가 바로 행동할 수 있게 구체적으로 쓰세요.
"""

    loan_prompt = """
당신은 한국어 금융 SEO 작가입니다.
문체는 신뢰감 있는 금융 전문가 톤입니다.
대출 조건, 한도, 금리, 상환 방식, 주의사항을 구조적으로 설명하세요.
본문에 반드시 HTML <table> 태그를 하나 이상 포함하세요.
승인 가능성을 보장하지 말고, 실제 금리와 조건은 금융기관 공식 안내를 확인하라고 쓰세요.
신청 조건, 필요 서류, 조회할 공식 기관, 비교 체크리스트를 반드시 포함하세요.
"""

    if category in {"최신이슈", "정책지원", "꿈해몽"}:
        if category == "정책지원":
            return traffic_prompt + "\n정책지원 글은 신청 대상, 신청 시기, 필요 서류, 공식 확인 방법을 중심으로 쓰세요."
        if category == "꿈해몽":
            return traffic_prompt + """
꿈해몽은 가벼운 키워드 풀이가 아니라, 실제 꿈 전문 해몽가가 상담하듯 상세하게 풀어주세요.
꿈속 장면, 등장인물, 장소, 색감, 감정, 반복 행동, 깨어난 뒤의 느낌을 하나씩 해석하세요.
같은 꿈이라도 불안한 감정이었는지, 편안했는지, 도망쳤는지, 바라만 봤는지에 따라 해석이 달라진다는 점을 설명하세요.
현실의 인간관계, 일, 돈, 건강, 가족, 연애, 심리 상태와 연결해서 여러 가능성을 제시하세요.
단정적인 예언, 공포 조장, 복권/투자 권유, 질병 진단처럼 위험한 해석은 하지 마세요.
본문은 '이 꿈이 말하는 핵심', '상황별 해석', '현실에서 점검할 부분', '좋은 꿈으로 보는 경우', '주의가 필요한 경우' 흐름으로 깊게 작성하세요.
"""
        return traffic_prompt

    if category == "신장정신":
        return health_prompt + "\n불안, 공황장애, 신장병 주제는 환자 입장에서 공감과 위로를 담아 쓰세요."
    if category == "웰빙":
        return health_prompt + "\n웰빙 주제는 객관적이고 깔끔한 건강 매거진 톤으로 쓰세요."
    if category == "대출관련":
        return loan_prompt

    return traffic_prompt


def normalize_keyword(value: str) -> str:
    return re.sub(r"\s+", "", value or "").lower()


CONTENT_INTENT_HINTS = {
    "sports_person": ["르브론", "제임스", "lebron", "lakers", "nba", "free agent", "농구", "축구", "감독", "축구감독", "대표팀", "국가대표", "선수", "레이커스", "계약", "트레이드", "FA", "이적", "기록", "커리", "메시", "손흥민", "제시 마치", "jesse marsch", "마치 감독"],
    "sports_match": ["경기", "중계", "스코어", "라인업", "전적", "결과", "하이라이트", "월드컵", "축구", "야구", "농구", "배구", "vs"],
    "flag_display": ["태극기", "국기", "제헌절", "광복절", "현충일", "게양", "달기"],
    "controversy": ["논란", "의혹", "쟁점", "해명", "반박", "사과", "일베", "표절", "폭로", "루머", "갑질", "비판", "논쟁", "구설"],
    "person": ["프로필", "나이", "학력", "인스타", "배우", "가수", "선수", "작가", "유튜버", "논란", "근황"],
    "recruit": ["채용", "공고", "지원", "입사", "자소서", "면접", "인턴", "경력", "신입", "전형"],
    "policy": ["지원금", "장려금", "급여", "보조금", "바우처", "복지", "정부지원", "정책지원", "지원대상", "지급대상", "신청기간"],
    "loan": ["대출", "금리", "한도", "상환", "신용", "담보", "DSR", "DTI", "은행"],
    "investment": ["코인", "비트코인", "주가", "관련주", "시세", "전망", "소각", "상장", "투자", "etf", "ETF", "레버리지", "인버스", "교육", "금융투자교육원", "파생상품", "위험등급"],
    "health": ["증상", "원인", "치료", "검사", "병원", "질환", "불안", "공황", "신장", "건강"],
    "dream": ["꿈", "해몽", "태몽", "길몽", "흉몽", "악몽"],
}


LONGTAIL_INTENT_RULES = {
    "investment": ["etf", "레버리지", "인버스", "주가", "시세", "코인", "상장", "투자", "금융투자", "파생상품", "교육 무료", "교육 이수"],
    "loan": ["대출", "금리", "한도", "상환", "DSR", "DTI", "신용", "담보"],
    "recruit": ["채용", "공고", "접수", "자소서", "면접", "인턴", "경력", "신입"],
    "policy": ["지원금", "장려금", "급여", "보조금", "바우처", "복지", "신청기간", "지원대상", "지급대상"],
    "sports_match": ["경기", "중계", "라인업", "상대 전적", "하이라이트", "스코어", "관전 포인트"],
    "flag_display": ["태극기", "국기", "게양", "달기", "게양일", "게양 방법", "게양 기간"],
    "controversy": ["논란", "의혹", "쟁점", "해명", "반박", "일베", "표절", "폭로", "갑질", "비판"],
    "health": ["증상", "원인", "치료", "검사", "병원", "질환", "불안", "공황", "신장", "건강"],
    "dream": ["꿈", "해몽", "태몽", "길몽", "흉몽", "악몽"],
}


def has_sports_match_context(keyword: str, wordpress_longtails: str = "") -> bool:
    """검색 결과의 단어 하나가 비스포츠 주제를 경기 글로 바꾸지 못하게 합니다."""
    text = f"{keyword} {wordpress_longtails}".casefold()
    sport_names = ["축구", "야구", "농구", "배구", "골프", "테니스", "복싱", "e스포츠", "nba", "k리그", "월드컵", "올림픽"]
    match_signals = ["경기", "중계", "라인업", "상대 전적", "하이라이트", "스코어", "관전", "vs"]
    return any(term in text for term in sport_names) and any(term in text for term in match_signals)


def infer_intent_from_longtails(wordpress_longtails: str) -> str:
    text = (wordpress_longtails or "").casefold()
    if not text.strip():
        return ""

    scores = {intent: 0 for intent in LONGTAIL_INTENT_RULES}
    for intent, terms in LONGTAIL_INTENT_RULES.items():
        for term in terms:
            if term.casefold() in text:
                scores[intent] += 3 if " " in term else 1

    if not has_sports_match_context("", wordpress_longtails):
        scores["sports_match"] = 0

    # Longtail should win over ambiguous words like "경기" when finance terms are explicit.
    if any(term in text for term in ["etf", "레버리지", "인버스", "금융투자", "파생상품"]):
        scores["investment"] += 8
        scores["sports_match"] = max(0, scores["sports_match"] - 5)
    if any(term in text for term in ["논란", "의혹", "일베", "해명", "반박"]):
        scores["controversy"] += 6

    intent, score = max(scores.items(), key=lambda item: item[1])
    return intent if score > 0 else ""


def classify_content_intent(keyword: str, category: str, wordpress_longtails: str = "", snippets: str = "") -> str:
    longtail_intent = infer_intent_from_longtails(wordpress_longtails)
    if longtail_intent:
        return longtail_intent

    text = f"{keyword} {category} {wordpress_longtails} {snippets}".casefold()
    scores = {intent: 0 for intent in CONTENT_INTENT_HINTS}

    category_boosts = {
        "정책지원": "policy",
        "대출관련": "loan",
        "신장정신": "health",
        "웰빙": "health",
        "꿈해몽": "dream",
    }
    if category in category_boosts:
        scores[category_boosts[category]] += 5

    if re.search(r"\s대\s|\svs\s|[A-Za-z가-힣]+\s*대\s*[A-Za-z가-힣]+", keyword, flags=re.IGNORECASE):
        scores["sports_match"] += 6

    if "감독" in text and any(term in text for term in ["축구", "대표팀", "국가대표", "월드컵", "캐나다", "한국", "전술"]):
        scores["sports_person"] += 8
    if any(term in text for term in ["논란", "의혹", "쟁점", "해명", "일베", "표절", "폭로", "갑질", "비판"]):
        scores["controversy"] += 8
    if any(term in text for term in ["etf", "레버리지", "인버스", "금융투자교육원", "파생상품", "투자자교육"]):
        scores["investment"] += 10
        scores["sports_match"] = max(0, scores["sports_match"] - 8)

    for intent, hints in CONTENT_INTENT_HINTS.items():
        for hint in hints:
            if hint.casefold() in text:
                scores[intent] += 2 if hint.casefold() in keyword.casefold() else 1

    # 검색 결과 요약에 우연히 섞인 '경기' 한 단어만으로 스포츠 글이 되지 않게 합니다.
    if not has_sports_match_context(keyword, wordpress_longtails):
        scores["sports_match"] = 0

    if category != "정책지원" and scores["sports_person"] >= 5:
        scores["policy"] = max(0, scores["policy"] - 4)

    intent, score = max(scores.items(), key=lambda item: item[1])
    return intent if score > 0 else "issue"


def intent_article_rules(intent: str, keyword: str) -> str:
    rules = {
        "sports_person": f"""
이 글의 의도 분류: 스포츠 인물/선수 이슈
반드시 다룰 내용: {keyword}의 최근 이슈, 소속팀/계약 상황, 이적 또는 잔류 가능성, 최근 성적과 기록, 팬들이 궁금해하는 다음 행보, 공식 NBA/구단/선수 SNS 확인 링크.
절대 넣지 말 것: 신청방법, 접수기간, 자격조건, 필요서류, 지원대상, 신청 버튼, 채용 전형 같은 행정/채용/지원금 항목.
소제목 예시: 최근 무슨 일이 있었나, 계약 상황은 어떻게 봐야 하나, 이적 가능성은 있는가, 레이커스와의 관계, 다음 시즌 전망, 공식 발표 확인 방법.
""",
        "sports_match": """
이 글의 의도 분류: 스포츠 경기
반드시 다룰 내용: 경기 일정, 중계 채널/시간, 상대 전적, 예상 라인업, 관전 포인트, 경기 결과 확인 위치, 하이라이트.
절대 넣지 말 것: 신청방법, 자격조건, 접수기간, 필요서류.
""",
        "flag_display": """
이 글의 의도 분류: 국기·태극기 게양 안내
반드시 다룰 내용: 게양하는 날, 게양 시간/기간, 게양 위치, 태극기 다는 법, 깃대가 없을 때 방법, 공식 안내 확인처.
절대 넣지 말 것: 경기 관람, 중계, 라인업, 상대 전적, 하이라이트, 스포츠 일정.
""",
        "person": f"""
이 글의 의도 분류: 인물/화제 인물
반드시 다룰 내용: {keyword}의 프로필, 최근 근황, 왜 화제가 됐는지, 주요 활동, 관련 논란이 있으면 확인된 범위, 공식 채널.
절대 넣지 말 것: 신청방법, 접수기간, 자격조건, 필요서류, 지원대상.
""",
        "controversy": f"""
이 글의 의도 분류: 논란/의혹/쟁점 정리
이 글의 핵심 목적: {keyword}를 검색한 사람이 "왜 이게 이슈가 됐는지"와 "무엇이 문제였는지"를 본문 초반에서 바로 확인하게 만드는 것.
반드시 다룰 내용: {keyword}가 왜 논란이 됐는지, 문제가 된 구체 표현·장면·발언·게시물·행동, 쟁점별 찬반/비판/반박, 당사자 해명이나 공식 입장이 있는지, 확인된 사실과 추측을 구분한 정리, 독자가 오해하기 쉬운 부분.
본문 초반 금지: 프로필, 작품 소개, 일반 배경 설명으로 오래 시작하지 마세요. 논란의 발단과 문제가 된 구체 내용을 먼저 쓰세요.
절대 넣지 말 것: 근거 없이 단정하기, 확인되지 않은 루머 확대, 인신공격, 신청방법, 접수기간, 자격조건, 필요서류, 지원대상.
소제목 예시: 왜 논란이 됐나, 문제가 된 부분은 무엇인가, 일베 의혹이 나온 이유, 반박과 해명은 있었나, 확인된 사실과 추측 구분, 최종 쟁점 정리.
""",
        "recruit": """
이 글의 의도 분류: 채용
반드시 다룰 내용: 공고명, 접수기간, 마감시간, 지원자격, 직무, 근무지, 전형절차, 공식 지원 링크, 현재 지원 가능 여부.
""",
        "policy": """
이 글의 의도 분류: 정책/지원금
반드시 다룰 내용: 신청기간, 지원대상, 제외대상, 지급금액, 신청방법, 필요서류, 공식 신청처.
""",
        "loan": """
이 글의 의도 분류: 대출/금융
반드시 다룰 내용: 조건, 한도, 금리, 상환방식, 필요서류, 승인/거절 요인, 공식 조회처.
""",
        "investment": """
이 글의 의도 분류: 투자/코인/주가
반드시 다룰 내용: 키워드가 ETF/레버리지/인버스라면 레버리지 ETF 뜻, 교육 이수 필요 여부, 무료 교육 위치, 거래 전 확인사항, 위험성, 일반 ETF와 차이, 투자 주의점을 중심으로 작성. 코인/주가라면 현재 가격 또는 시세, 상승/하락 이유, 전망, 비교 대상, 주요 이슈, 투자 주의점.
절대 넣지 말 것: 경기 일정, 중계, 라인업, 상대 전적, 하이라이트, 신청방법, 접수기간, 자격조건.
""",
        "health": """
이 글의 의도 분류: 건강
반드시 다룰 내용: 증상, 원인, 병원에 가야 하는 기준, 검사 항목, 생활관리, 공식 건강정보 링크.
절대 넣지 말 것: 신청방법, 접수기간, 자격조건.
""",
        "dream": """
이 글의 의도 분류: 꿈해몽
반드시 다룰 내용: 꿈 장면, 감정, 등장인물, 장소, 현실 고민과 연결한 전문 해몽, 좋은 의미와 주의할 의미.
절대 넣지 말 것: 신청방법, 접수기간, 자격조건, 공식 신청처.
""",
        "issue": """
이 글의 의도 분류: 일반 이슈
반드시 다룰 내용: 왜 화제인지, 현재 확인된 사실, 사람들이 궁금해하는 쟁점, 영향, 앞으로 확인할 점, 대표 링크.
신청/접수/자격 항목은 실제로 그런 절차가 있는 주제일 때만 넣으세요.
""",
    }
    return rules.get(intent, rules["issue"])


FORBIDDEN_SECTION_WORDS = {
    "sports_person": ["정책지원", "지원 정보", "지원대상", "지원 대상", "공식 신청", "신청방법", "신청 방법", "접수기간", "접수 기간", "자격조건", "자격 조건", "필요서류", "지원 버튼"],
    "sports_match": ["정책지원", "지원 정보", "지원대상", "지원 대상", "공식 신청", "신청방법", "신청 방법", "접수기간", "접수 기간", "자격조건", "자격 조건", "필요서류"],
    "flag_display": ["경기 관람", "경기관람", "중계", "라인업", "상대 전적", "하이라이트", "스코어", "관전 포인트"],
    "person": ["정책지원", "지원 정보", "지원대상", "지원 대상", "공식 신청", "신청방법", "신청 방법", "접수기간", "접수 기간", "자격조건", "자격 조건", "필요서류"],
    "investment": ["정책지원", "지원대상", "공식 신청", "신청방법", "신청 방법", "접수기간", "접수 기간", "자격조건", "자격 조건", "필요서류", "경기 일정", "중계", "라인업", "상대 전적", "하이라이트", "스코어", "관전 포인트"],
    "health": ["정책지원", "지원대상", "공식 신청", "신청방법", "신청 방법", "접수기간", "접수 기간", "자격조건", "자격 조건", "필요서류"],
    "dream": ["정책지원", "지원대상", "신청방법", "신청 방법", "접수기간", "접수 기간", "자격조건", "자격 조건", "필요서류", "공식 신청"],
}


INTENT_REQUIRED_WORDS = {
    "sports_person": ["감독", "축구", "대표팀", "국가대표", "캐나다", "한국", "후보", "전술", "소속", "계약"],
    "sports_match": ["경기", "중계", "일정", "전적", "결과", "하이라이트", "라인업"],
    "recruit": ["채용", "공고", "접수", "지원", "전형", "직무"],
    "policy": ["지원금", "정책", "신청", "대상", "지급", "복지", "보조금"],
    "investment": ["투자", "ETF", "etf", "레버리지", "시세", "위험", "교육", "금융"],
}


def validate_article_intent(article: GeneratedArticle, intent: str) -> None:
    forbidden_words = FORBIDDEN_SECTION_WORDS.get(intent, [])
    text = " ".join([article.title, article.meta, article.html])
    if forbidden_words:
        found = [word for word in forbidden_words if word in text]
        if found:
            raise ValueError(f"글 의도와 맞지 않는 항목이 포함되었습니다: {', '.join(found)}")

    required_words = INTENT_REQUIRED_WORDS.get(intent, [])
    if required_words and not any(word in text for word in required_words):
        raise ValueError(f"글 의도({intent})를 뒷받침하는 핵심 단어가 부족합니다.")


def longtail_core_terms(longtail: str) -> list[str]:
    words = re.findall(r"[가-힣A-Za-z0-9]{2,}", longtail)
    blocked = {
        "관련",
        "최신",
        "확인",
        "정리",
        "정보",
        "방법",
        "공식",
        "현재",
        "사람",
        "궁금",
        "보기",
    }
    return [word for word in words if word not in blocked]


def longtail_is_reflected(longtail: str, text: str) -> bool:
    compact_longtail = re.sub(r"\s+", "", longtail).lower()
    compact_text = re.sub(r"\s+", "", text).lower()
    if compact_longtail and compact_longtail in compact_text:
        return True
    terms = longtail_core_terms(longtail)
    if not terms:
        return False
    matched = sum(1 for term in terms if term.lower() in text.lower())
    return matched >= min(2, len(terms))


def validate_longtail_usage(article: GeneratedArticle, wordpress_longtails: str) -> None:
    longtails = split_longtail_keywords(wordpress_longtails, 10)
    if len(longtails) < 3:
        return

    title_and_headings = " ".join([article.title, *h2_texts(article.html)])
    full_text = " ".join([article.title, article.meta, article.html])
    first_area = " ".join([article.title, article.meta, first_html_text(article.html, 1200), *h2_texts(article.html)[:3]])
    heading_matches = [item for item in longtails if longtail_is_reflected(item, title_and_headings)]
    full_matches = [item for item in longtails if longtail_is_reflected(item, full_text)]
    top_longtails = longtails[: min(3, len(longtails))]
    missed_top = [item for item in top_longtails if not longtail_is_reflected(item, first_area)]

    required_heading_matches = min(4, max(2, len(longtails) // 2))
    if len(heading_matches) < required_heading_matches:
        raise ValueError(
            f"롱테일 키워드가 제목/H2에 충분히 반영되지 않았습니다: "
            f"{len(heading_matches)}/{required_heading_matches}"
        )
    if len(full_matches) < min(5, len(longtails)):
        raise ValueError("롱테일 키워드가 본문에 충분히 반영되지 않았습니다.")
    if missed_top:
        raise ValueError(f"상위 롱테일이 제목/초반/H2에 반영되지 않았습니다: {', '.join(missed_top)}")


SOURCE_ATTRIBUTION_PHRASES = [
    "공식 설명은",
    "공식 자료",
    "공식 안내문",
    "공식 사이트에 따르면",
    "자료에 따르면",
    "안내에 따르면",
    "에 따르면",
]

BAD_STRUCTURE_PHRASES = [
    "공식 문서에 없는 항목",
    "공식 문서에는",
    "공식 안내문에는",
    "공식 설명은",
]


READER_META_PHRASES = [
    "검색하는 사람들은",
    "검색한 사람들은",
    "검색하는 분들은",
    "검색한 분들은",
    "검색자가 궁금",
    "사람들이 궁금해",
    "궁금해합니다",
    "알고 싶어합니다",
    "찾고 싶어합니다",
    "키워드는 검색에서 등장",
    "검색에서 등장합니다",
    "이 글을 읽는",
    "이 글을 클릭",
]


def polish_readability_style(article: GeneratedArticle) -> GeneratedArticle:
    """Remove common AI commentary about the reader before the final checks."""
    replacements = {
        "검색하는 사람들은": "",
        "검색한 사람들은": "",
        "검색하는 분들은": "",
        "검색한 분들은": "",
        "검색자가 궁금해하는": "핵심",
        "사람들이 궁금해하는 이유는": "주목받는 이유는",
        "사람들이 궁금해하는 점은": "핵심 쟁점은",
        "사람들이 궁금해하는 내용은": "핵심 내용은",
        "사람들이 궁금해하는": "많이 확인하는",
        "궁금해합니다": "주목합니다",
        "알고 싶어합니다": "확인이 필요합니다",
        "찾고 싶어합니다": "확인이 필요합니다",
        "키워드는 검색에서 등장": "관련 정보는",
        "검색에서 등장합니다": "확인됩니다",
        "이 글을 읽는 분들은": "",
        "이 글을 읽는 사람은": "",
        "이 글을 클릭한": "",
    }

    def clean(value: str) -> str:
        for before, after in replacements.items():
            value = value.replace(before, after)
        return re.sub(r"\s{2,}", " ", value).strip()

    return GeneratedArticle(title=clean(article.title), meta=clean(article.meta), html=clean(article.html))


def validate_readability_style(article: GeneratedArticle) -> None:
    text = " ".join([article.title, article.meta, article.html])
    found = [phrase for phrase in BAD_STRUCTURE_PHRASES if phrase in text]
    if found:
        raise ValueError(f"본문 구조가 딱딱한 출처 설명형으로 작성되었습니다: {', '.join(found)}")

    plain_text = re.sub(r"<[^>]+>", " ", text)
    plain_text = re.sub(r"\s+", " ", plain_text)
    reader_meta_found = [phrase for phrase in READER_META_PHRASES if phrase in plain_text]
    if reader_meta_found:
        logging.warning("독자 행동을 설명하는 문구가 일부 남아 있습니다: %s", ", ".join(reader_meta_found))

    attribution_count = sum(text.count(phrase) for phrase in SOURCE_ATTRIBUTION_PHRASES)
    if attribution_count > 2:
        raise ValueError("본문 중간에 '~에 따르면' 같은 출처 설명 표현이 너무 많습니다.")


def validate_controversy_depth(article: GeneratedArticle, keyword: str, intent: str) -> None:
    controversy_terms = ["논란", "의혹", "쟁점", "해명", "반박", "일베", "표절", "폭로", "갑질", "비판"]
    if intent != "controversy" and not any(term in keyword for term in controversy_terms):
        return

    text = re.sub(r"<[^>]+>", " ", " ".join([article.title, article.meta, article.html]))
    text = re.sub(r"\s+", " ", text)
    required_groups = {
        "논란 원인": ["왜 논란", "논란 이유", "논란이 된 이유", "발단", "계기", "이슈가 된 이유"],
        "문제가 된 구체 내용": ["문제가 된", "문제 된", "무엇이 문제", "구체", "장면", "표현", "발언", "게시물", "행동"],
        "쟁점 설명": ["쟁점", "의혹", "비판", "반응", "논쟁", "근거"],
        "반박/해명": ["해명", "반박", "입장", "사과", "부인", "확인되지", "추측"],
        "사실/추측 구분": ["확인된 사실", "추측", "단정", "확인되지", "구분"],
    }
    missing = [
        group_name
        for group_name, phrases in required_groups.items()
        if not any(phrase in text for phrase in phrases)
    ]
    if missing:
        raise ValueError(f"논란형 글의 핵심 설명이 부족합니다: {', '.join(missing)}")

    headings = " ".join(h2_texts(article.html))
    required_heading_terms = ["왜 논란", "문제가 된", "쟁점", "해명", "확인된 사실"]
    heading_matches = sum(1 for term in required_heading_terms if term in headings)
    if heading_matches < 3:
        raise ValueError("논란형 글의 H2 제목에 논란 원인/구체 내용/쟁점/해명 구조가 부족합니다.")


def validate_recent_issue_focus(article: GeneratedArticle, category: str, intent: str) -> None:
    issue_intents = {"sports_person", "person", "issue", "controversy", "investment", "sports_match"}
    if category != "최신이슈" and intent not in issue_intents:
        return

    text = re.sub(r"<[^>]+>", " ", " ".join([article.title, article.meta, article.html]))
    text = re.sub(r"\s+", " ", text)

    current_terms = ["최근", "현재", "왜", "화제", "이슈", "주목", "검색", "관심", "행보", "전망"]
    if sum(1 for term in current_terms if term in text) < 3:
        raise ValueError("최근 이슈형 글인데 '왜 지금 검색되는지'와 현재 상황 설명이 부족합니다.")

    first_area = " ".join([article.title, article.meta, first_html_text(article.html, 900), *h2_texts(article.html)[:2]])
    first_terms = ["최근", "현재", "왜", "화제", "이슈", "주목", "검색", "관심"]
    if not any(term in first_area for term in first_terms):
        raise ValueError("본문 초반에 최근 이슈가 된 이유가 먼저 나오지 않았습니다.")

    if intent == "sports_person":
        sports_terms = ["감독", "선수", "대표팀", "국가대표", "전술", "소속", "팀", "리그", "성과", "행보", "경기력", "한국"]
        if sum(1 for term in sports_terms if term in text) < 3:
            raise ValueError("스포츠 인물/감독 이슈인데 현재 역할, 전술, 대표팀/팀 맥락 설명이 부족합니다.")


def validate_template_matches_longtails(article: GeneratedArticle, wordpress_longtails: str, intent: str) -> None:
    longtail_text = (wordpress_longtails or "").casefold()
    full_text = re.sub(r"<[^>]+>", " ", " ".join([article.title, article.meta, article.html]))
    sports_template_words = ["경기 일정", "중계", "라인업", "상대 전적", "하이라이트", "스코어", "관전 포인트"]
    finance_terms = ["etf", "레버리지", "인버스", "금융투자", "투자자교육", "파생상품"]
    policy_template_words = ["신청기간", "지원대상", "지급금액", "필요서류", "신청방법", "제외대상"]
    recruit_template_words = ["지원자격", "전형절차", "자소서", "면접", "근무지", "채용공고"]

    if any(term in longtail_text for term in finance_terms):
        found_sports = [word for word in sports_template_words if word in full_text]
        if found_sports:
            raise ValueError(f"금융/ETF 롱테일인데 스포츠 경기 템플릿이 섞였습니다: {', '.join(found_sports)}")
        if not any(term in full_text.casefold() for term in finance_terms):
            raise ValueError("금융/ETF 롱테일의 핵심 단어가 본문에 부족합니다.")

    if intent != "sports_match":
        found_sports = [word for word in sports_template_words if word in full_text]
        if len(found_sports) >= 2:
            raise ValueError(f"스포츠 경기가 아닌 글에 경기 템플릿이 섞였습니다: {', '.join(found_sports)}")

    if intent not in {"policy", "loan", "recruit"}:
        found_policy = [word for word in policy_template_words if word in full_text]
        if len(found_policy) >= 2:
            raise ValueError(f"정책/신청형이 아닌 글에 신청 템플릿이 섞였습니다: {', '.join(found_policy)}")

    if intent != "recruit":
        found_recruit = [word for word in recruit_template_words if word in full_text]
        if len(found_recruit) >= 2:
            raise ValueError(f"채용 글이 아닌데 채용 템플릿이 섞였습니다: {', '.join(found_recruit)}")


def dream_interpretation_system_rules() -> str:
    return """
꿈해몽 글쓰기 전용 지침:
당신은 20년 경력의 꿈해몽 전문가입니다.
꿈해몽은 단순한 키워드 설명이 아니라, 실제 꿈 상담을 하듯 장면과 감정, 상징, 현실 고민을 연결해 풀어주세요.

긴 블로그 글을 작성할 때:
- 글은 3000자 이상으로 작성하세요.
- 관련 꿈 예시를 최대한 풍부하게 작성하세요. 가능한 경우 50개 예시를 목표로 하되, JSON이 잘리지 않도록 안정적으로 작성하세요.
- 각 꿈 예시는 <h3><strong>꿈 예시 제목</strong></h3> 형태로 작성하세요.
- 각 예시 앞에는 한 줄 여백이 생기도록 문단을 분리하세요.
- 각 해몽은 번호를 붙이지 말고, 문장형으로 자연스럽게 작성하세요.
- 같은 주제라도 꿈속 감정, 장소, 등장인물, 행동, 반복 여부에 따라 해석이 달라질 수 있음을 설명하세요.
- 좋은 의미, 주의가 필요한 의미, 현실에서 점검할 부분을 함께 풀어주세요.
- 예언처럼 단정하지 말고, 심리와 현실 상황을 연결하는 방식으로 말하세요.

구조:
- 머리말은 입력 주제에 대한 일반적인 의미와 사람들이 왜 이 꿈을 궁금해하는지 친근하게 시작하세요.
- 큰 대분류는 <h2>로 작성하세요.
- 꿈 주제 예시는 <h3><strong>...</strong></h3>로 작성하세요.
- 마지막은 반드시 <h2>최종 정리하면,</h2>로 끝내고 핵심을 5~7개 bullet로 정리하세요.

SEO:
- 제목은 검색 상위 노출을 노리고, 포커스 키워드를 자연스럽게 포함하세요.
- 메타 디스크립션에도 포커스 키워드를 포함하세요.
- 태그는 # 없이 콤마로 구분하세요.

지식인 답변형 짧은 해몽이 필요한 경우:
- 500자 전후의 친근하고 상냥한 공감 말투로 작성하세요.
- 구조화된 제목 목록보다 문장형 단락으로 작성하세요.
- 말미에는 관련 꿈해몽 페이지 후보 링크를 넣으세요.
- 관련 글이 확실하지 않으면 https://seaga4.tistory.com 주소를 넣으세요.
- 예: 고양이 꿈이면 https://seaga4.tistory.com/entry/고양이-꿈-해몽 형태를 우선 고려하세요.
"""


def system_prompt_for_category(category: str) -> str:
    base_prompt = """
당신은 한국어 SEO 블로그 글쓰기 전문가입니다.
가장 중요한 원칙은 '키워드의 실제 의도에 맞는 글'을 쓰는 것입니다.
모든 글에 신청방법, 접수기간, 자격조건, 필요서류를 넣지 마세요.
그 항목들은 채용, 정책지원, 대출처럼 실제 신청/접수/자격이 있는 주제에서만 사용합니다.
인물, 스포츠 선수, 스포츠 경기, 코인, 건강, 꿈해몽, 일반 이슈는 각 주제에 맞는 소제목과 정보만 사용하세요.
논란/의혹/쟁점 글은 프로필이나 배경 설명보다 "왜 논란이 됐는지"와 "문제가 된 구체 내용"을 본문 초반에 먼저 설명하세요.
검색 참고자료와 신뢰 링크에서 확인되지 않은 날짜, 금액, 조건, 기록은 만들어내지 마세요.
본문 중간에서 '공식 자료에 따르면', '공식 설명은', '~에 따르면' 같은 출처 설명으로 문장을 시작하지 마세요.
출처 표기는 글 마지막의 짧은 출처 줄에서만 처리하고, 본문은 독자가 원하는 답을 바로 보여주는 방식으로 쓰세요.
예를 들어 '필요서류' 섹션이면 실제 필요한 서류만 목록으로 정리하고, 장황한 출처 설명이나 없는 항목 해설을 넣지 마세요.
본문은 기계적인 질문 목록이 아니라 사람이 설명하듯 자연스러운 존댓말 문단으로 시작하세요.
HTML은 <p>, <h2>, <h3>, <ul>, <li>, <strong>, <blockquote>, <a>, <table>만 사용하세요.
FAQ 또는 Q&A 섹션은 만들지 말고, 마지막 섹션 제목은 반드시 '최종 정리하면,'으로 작성하세요.
출력은 반드시 JSON 하나만 반환하세요.
"""
    if category == "꿈해몽":
        # 전용 프롬프트를 쓸 때는 공용 base_prompt 를 붙이지 않는다.
        # base_prompt 는 '신청방법·접수기간을 넣지 마라' 처럼 꿈해몽과
        # 무관한 금지 조항이 대부분이라, 지시만 길어지고 도움이 안 된다.
        if use_dream_prompt_v2():
            return dream_system_prompt_v2()
        return base_prompt + "\n" + dream_interpretation_system_rules()
    return base_prompt


def official_link_for_keyword(keyword: str, category: str) -> str:
    """Return the official representative site for known entities/services."""
    key = normalize_keyword(keyword)
    encoded = requests_quote(keyword)

    official_rules = [
        (("국립생태원", "nie"), "https://www.nie.re.kr/"),
        (("sk하이닉스", "하이닉스", "skhynix"), "https://recruit.skhynix.com/"),
        (("삼성채용", "삼성전자채용", "samsungcareers"), "https://www.samsungcareers.com/"),
        (("국립공원공단",), "https://www.knps.or.kr/"),
        (("국민건강보험", "건강보험공단"), "https://www.nhis.or.kr/"),
        (("질병관리청", "kdca"), "https://www.kdca.go.kr/"),
        (("복지로",), "https://www.bokjiro.go.kr/"),
        (("고용24",), "https://www.work24.go.kr/"),
        (("워크넷",), "https://www.work.go.kr/"),
        (("금융감독원",), "https://www.fss.or.kr/"),
        (("서민금융진흥원",), "https://www.kinfa.or.kr/"),
    ]

    for patterns, url in official_rules:
        if any(pattern in key for pattern in patterns):
            return url

    government_terms = [
        "근로장려금",
        "자녀장려금",
        "부모급여",
        "육아휴직",
        "육아기근로시간",
        "지원금",
        "장려금",
        "급여",
        "보조금",
    ]
    if category == "정책지원" or any(term in key for term in government_terms):
        return f"https://www.gov.kr/portal/service/search?srhQuery={encoded}"
    if category == "꿈해몽":
        return "https://seaga4.tistory.com/"
    if category == "대출관련" or "대출" in key:
        return "https://finlife.fss.or.kr/"
    if category in {"신장정신", "웰빙"}:
        return f"https://health.kdca.go.kr/healthinfo/search/srchResult.do?searchWord={encoded}"

    return ""


def is_generic_or_news_link(url: str) -> bool:
    url = (url or "").lower()
    blocked_parts = [
        "google.com/search",
        "search.naver.com",
        "news.naver.com",
        "/news/",
        "yna.co.kr",
        "newsis.com",
        "blog",
        "tistory",
        "cafe.naver",
        "youtube.com",
    ]
    return any(part in url for part in blocked_parts)


def is_usable_official_link(url: str, category: str) -> bool:
    if not url.startswith(("http://", "https://")) or is_generic_or_news_link(url):
        return False
    return trusted_domain_score(url, category) >= 100


def homepage_url(url: str) -> str:
    parsed = urlparse(url if url.startswith(("http://", "https://")) else "https://" + url)
    if not parsed.netloc:
        return url
    return urlunparse((parsed.scheme or "https", parsed.netloc, "/", "", "", ""))


def link_is_reachable(url: str, timeout: int = 8) -> bool:
    if not url.startswith(("http://", "https://")):
        return False
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.6,en;q=0.5",
    }
    try:
        response = requests.head(url, headers=headers, timeout=timeout, allow_redirects=True)
        if response.status_code in {403, 405}:
            response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True, stream=True)
        return 200 <= response.status_code < 400
    except Exception:
        return False


def safe_representative_link(url: str) -> str:
    if not url:
        return url
    if link_is_reachable(url):
        return url
    home = homepage_url(url)
    if home != url:
        logging.info("대표 링크가 연결되지 않아 메인페이지로 대체합니다: %s -> %s", url, home)
    return home


def representative_link(keyword: str, category: str, source_link: str = "") -> str:
    official_link = official_link_for_keyword(keyword, category)
    if official_link:
        return official_link

    if is_usable_official_link(source_link, category):
        return source_link

    encoded = requests_quote(keyword)
    if "하이닉스" in keyword or "SK하이닉스" in keyword or "채용" in keyword:
        return "https://recruit.skhynix.com/"
    if "육아휴직" in keyword or "고용" in keyword:
        return f"https://www.gov.kr/portal/service/search?srhQuery={encoded}"
    if "급여" in keyword or "장려금" in keyword or category == "정책지원":
        return f"https://www.gov.kr/portal/service/search?srhQuery={encoded}"
    if category == "꿈해몽":
        return "https://seaga4.tistory.com/"
    if category == "대출관련":
        return "https://finlife.fss.or.kr/"
    if category in {"신장정신", "웰빙"}:
        return f"https://health.kdca.go.kr/healthinfo/search/srchResult.do?searchWord={encoded}"
    return f"https://www.google.com/search?q={encoded}"


def build_research_context(keyword: str, category: str, source_link: str = "") -> ResearchContext:
    source_news_notes = ""
    source_news_snippet = ""
    if is_saega2_health_news_item(category, source_link):
        source_news_snippet = f"- 원문 뉴스: {keyword} | {source_link}"
        try:
            source_news_notes = fetch_source_notes(
                [{"title": keyword, "link": source_link, "snippet": "신장정신 카테고리의 원문 뉴스"}],
                limit=1,
            )
        except Exception as exc:
            logging.warning("원문 뉴스 본문 확인 실패, 원문 링크와 제목만 기준으로 사용합니다: %s", exc)
            source_news_notes = f"원문 뉴스 제목: {keyword}\n원문 URL: {source_link}"

    try:
        search_results = google_search_results(keyword, category)
    except Exception as exc:
        safe_error = re.sub(r"(api_key=)[^&\s]+", r"\1[redacted]", str(exc), flags=re.IGNORECASE)
        logging.warning("검색 참고자료 수집 실패, 대표 링크 기준으로 글 생성을 계속합니다: %s", safe_error)
        search_results = []
    link = official_link_for_keyword(keyword, category)
    if not link and is_usable_official_link(source_link, category):
        link = source_link
    if link:
        link = safe_representative_link(link)

    if search_results:
        trusted_results = rank_trusted_results(keyword, category, search_results)
        if not link:
            link = choose_representative_result(keyword, category, trusted_results or search_results)
        search_snippets = "\n".join(
            f"- {item['title']} | {item['link']} | {item['snippet']}" for item in (trusted_results or search_results)[:5]
        )
        snippets = "\n".join(part for part in [source_news_snippet, search_snippets] if part)
        try:
            search_notes = fetch_source_notes(trusted_results or search_results)
            source_notes = "\n\n".join(part for part in [source_news_notes, search_notes] if part)
        except Exception as exc:
            logging.warning("신뢰 링크 본문 확인 실패, 검색 결과 요약만 참고합니다: %s", exc)
            source_notes = source_news_notes or "신뢰 링크 본문 확인 실패. 검색 결과 요약과 대표 링크만 참고할 것."
    else:
        snippets = source_news_snippet or "검색 API 참고자료 없음. 공식/대표 링크에서 확인할 항목을 구체적으로 안내할 것."
        try:
            fallback_notes = fetch_source_notes([{"title": "대표 링크", "link": representative_link(keyword, category, source_link), "snippet": ""}])
            source_notes = "\n\n".join(part for part in [source_news_notes, fallback_notes] if part)
        except Exception as exc:
            logging.warning("대표 링크 본문 확인 실패, 대표 링크만 참고합니다: %s", exc)
            source_notes = source_news_notes or "대표 링크 본문 확인 실패. 공식 사이트에서 확인할 항목을 구체적으로 안내할 것."

    if not link:
        link = representative_link(keyword, category, source_link)
    link = safe_representative_link(link)

    return ResearchContext(link=link, snippets=snippets, source_notes=source_notes)


def google_search_results(keyword: str, category: str) -> list[dict[str, str]]:
    serpapi_key = os.getenv("SERPAPI_API_KEY")
    query = build_google_query(keyword, category)
    if serpapi_key and reserve_serpapi_slot("content"):
        try:
            return serpapi_search_results(query, serpapi_key)
        except Exception as exc:
            safe_error = re.sub(r"(api_key=)[^&\s]+", r"\1[redacted]", str(exc), flags=re.IGNORECASE)
            logging.warning("SerpApi 검색이 제한되어 Google Search API를 대신 시도합니다: %s", safe_error)
    elif serpapi_key:
        logging.info("SerpApi content daily budget reached; continuing without paid Google search: %s", keyword)

    api_key = os.getenv("GOOGLE_SEARCH_API_KEY")
    cx = os.getenv("GOOGLE_SEARCH_CX")
    if not api_key or not cx:
        return []

    response = requests.get(
        "https://www.googleapis.com/customsearch/v1",
        params={"key": api_key, "cx": cx, "q": query, "num": 5, "lr": "lang_ko"},
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    results = []
    for item in data.get("items", []):
        results.append(
            {
                "title": item.get("title", ""),
                "link": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            }
        )
    return results


def build_google_query(keyword: str, category: str, wordpress_longtails: str = "") -> str:
    intent = classify_content_intent(keyword, category, wordpress_longtails)
    query_suffixes = {
        "sports_person": "최근 이슈 계약 상황 팀 공식 NBA 기록 이적 가능성",
        "sports_match": "경기 일정 중계 시간 상대 전적 라인업 결과 하이라이트",
        "flag_display": "태극기 게양일 게양 시간 게양 방법 국기 다는 법 공식 안내",
        "controversy": "논란 이유 쟁점 해명 반박 원문 반응 무엇이 문제",
        "person": "프로필 최근 근황 공식 인스타 주요 활동 왜 화제",
        "recruit": "공식 채용 공고 접수기간 자격 전형절차 지원방법",
        "policy": "공식 신청 지원대상 신청기간 지급금액 필요서류",
        "loan": "조건 한도 금리 신청방법 상환방식 공식",
        "investment": "ETF 레버리지 뜻 교육 무료 금융투자교육원 위험 투자 주의점",
        "health": "증상 원인 치료 검사 병원 공식 건강정보",
        "dream": "꿈해몽 의미 상황별 심리 해석",
        "issue": "최신 이슈 현재 상황 핵심 정리 공식 확인",
    }
    return f"{keyword} {query_suffixes.get(intent, query_suffixes['issue'])}"


def serpapi_search_results(query: str, api_key: str) -> list[dict[str, str]]:
    response = requests.get(
        "https://serpapi.com/search.json",
        params={
            "engine": "google",
            "q": query,
            "google_domain": "google.co.kr",
            "hl": "ko",
            "gl": "kr",
            "api_key": api_key,
            "num": 10,
        },
        timeout=25,
    )
    response.raise_for_status()
    data = response.json()
    results = []

    ai_overview = data.get("ai_overview") or data.get("answer_box") or {}
    if isinstance(ai_overview, dict):
        overview_text = ai_overview.get("text") or ai_overview.get("answer") or ai_overview.get("snippet")
        if overview_text:
            results.append(
                {
                    "title": "Google AI/Answer 요약",
                    "link": ai_overview.get("link", ""),
                    "snippet": overview_text,
                }
            )

    for item in data.get("organic_results", []):
        results.append(
            {
                "title": item.get("title", ""),
                "link": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            }
        )
    return results


def trusted_domain_score(url: str, category: str) -> int:
    url = url.lower()
    score = 0
    high_trust = [
        ".go.kr",
        "gov.kr",
        "moel.go.kr",
        "mohw.go.kr",
        "bokjiro.go.kr",
        "nhis.or.kr",
        "kdca.go.kr",
        "fss.or.kr",
        "finlife.fss.or.kr",
        "nie.re.kr",
        "recruit.skhynix.com",
        "skhynix.com",
        "samsungcareers.com",
        "work24.go.kr",
        "work.go.kr",
        "knps.or.kr",
        "kinfa.or.kr",
        "kin.naver.com",
    ]
    medium_trust = [
        "news.naver.com",
        "yna.co.kr",
        "korea.kr",
        "jobkorea.co.kr",
        "saramin.co.kr",
        "catch.co.kr",
    ]
    if any(domain in url for domain in high_trust):
        score += 100
    if any(domain in url for domain in medium_trust):
        score += 50
    if category == "정책지원" and any(domain in url for domain in ["gov.kr", ".go.kr", "bokjiro.go.kr", "korea.kr"]):
        score += 50
    if category == "대출관련" and any(domain in url for domain in ["fss.or.kr", "finlife", "go.kr"]):
        score += 50
    if category in {"신장정신", "웰빙"} and any(domain in url for domain in ["kdca.go.kr", "nhis.or.kr", "mohw.go.kr"]):
        score += 50
    if "blog" in url or "tistory" in url or "cafe.naver" in url:
        score -= 80
    return score


def rank_trusted_results(keyword: str, category: str, results: list[dict[str, str]]) -> list[dict[str, str]]:
    ranked = sorted(results, key=lambda item: trusted_domain_score(item.get("link", ""), category), reverse=True)
    trusted = [item for item in ranked if trusted_domain_score(item.get("link", ""), category) > 0]
    return trusted or ranked


def choose_representative_result(keyword: str, category: str, results: list[dict[str, str]]) -> str:
    official_link = official_link_for_keyword(keyword, category)
    if official_link:
        return official_link

    preferred_domains = [
        "nie.re.kr",
        "recruit.skhynix.com",
        "skhynix.com",
        "samsungcareers.com",
        "gov.kr",
        "moel.go.kr",
        "work24.go.kr",
        "work.go.kr",
        "bokjiro.go.kr",
        "nhis.or.kr",
        "kdca.go.kr",
        "finlife.fss.or.kr",
        "fss.or.kr",
        "kin.naver.com",
    ]
    for domain in preferred_domains:
        for item in results:
            link = item.get("link", "")
            if domain in link and not is_generic_or_news_link(link):
                return item["link"]
    for item in results:
        link = item.get("link", "")
        if is_usable_official_link(link, category):
            return link
    return results[0].get("link", "") if results else ""


def fetch_source_notes(results: list[dict[str, str]], limit: int = 3) -> str:
    notes: list[str] = []
    for item in results[:limit]:
        url = item.get("link", "")
        if not url.startswith(("http://", "https://")):
            continue
        try:
            notes.append(fetch_single_source_note(url, item.get("title", "")))
        except Exception as exc:
            logging.warning("참고 링크 열기 실패, 이 링크는 건너뜁니다: %s / %s", url, exc)
    if not notes:
        return "신뢰 링크 본문 확인 실패. 검색 결과 요약과 대표 링크만 참고할 것."
    return "\n\n".join(notes)


def fetch_single_source_note(url: str, fallback_title: str) -> str:
    try:
        return fetch_single_source_note_once(url, fallback_title, verify=True)
    except requests.exceptions.SSLError as exc:
        if os.getenv("ALLOW_INSECURE_SOURCE_FETCH", "true").lower() not in {"1", "true", "yes", "y"}:
            raise
        logging.warning("SSL 인증서 확인 실패. 보조 방식으로 한 번만 다시 시도합니다: %s / %s", url, exc)
        return fetch_single_source_note_once(url, fallback_title, verify=False)


def fetch_single_source_note_once(url: str, fallback_title: str, verify: bool) -> str:
    response = requests.get(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.6,en;q=0.5",
        },
        timeout=8,
        verify=verify,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    title = normalize_cell(soup.title.get_text(" ", strip=True) if soup.title else fallback_title)
    meta = ""
    meta_tag = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
    if meta_tag and meta_tag.get("content"):
        meta = normalize_cell(meta_tag.get("content"))

    headings = [normalize_cell(h.get_text(" ", strip=True)) for h in soup.find_all(["h1", "h2"], limit=8)]
    paragraphs = [normalize_cell(p.get_text(" ", strip=True)) for p in soup.find_all("p", limit=12)]
    text_parts = [part for part in [meta, *headings, *paragraphs] if part]
    summary = " ".join(text_parts)
    summary = re.sub(r"\s+", " ", summary)[:1400]

    return f"출처: {title}\nURL: {url}\n확인한 내용: {summary}"


def requests_quote(value: str) -> str:
    from urllib.parse import quote_plus

    return quote_plus(value)


def split_longtail_keywords(value: str, limit: int = 10) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for raw in re.split(r"[,|\n]+", value or ""):
        item = re.sub(r"\s+", " ", raw).strip()
        if not item:
            continue
        key = re.sub(r"\s+", "", item).lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
        if len(items) >= limit:
            break
    return items


def uses_source_news_longtails(item: WritingQueueItem, peers: list[WritingQueueItem]) -> bool:
    """Detect the source-news fallback written by the keyword pipeline.

    A real autocomplete query almost never equals a collected full news title.
    Source-news fallback values intentionally do, so policy rows can be routed
    to the briefing writer even though their cells are no longer blank.
    """
    longtails = split_longtail_keywords(item.wordpress_longtails, 3)
    peer_titles = {
        re.sub(r"\s+", "", normalize_cell(peer.keyword)).casefold()
        for peer in peers
        if normalize_cell(peer.keyword)
    }
    return any(re.sub(r"\s+", "", longtail).casefold() in peer_titles for longtail in longtails)


def longtail_prompt_block(wordpress_longtails: str) -> str:
    longtails = split_longtail_keywords(wordpress_longtails, 10)
    if not longtails:
        return "롱테일 키워드 없음. 글쓰기를 진행하지 말고 먼저 롱테일 키워드를 생성해야 합니다."

    lines = [f"{index}. {item}" for index, item in enumerate(longtails, start=1)]
    return "\n".join(lines)


def readability_prompt_block() -> str:
    return """
본문 문체 추가 금지 규칙:
- 본문에서 "OO를 검색하는 사람들은 ... 궁금해합니다", "OO라는 키워드는 검색에서 등장합니다", "이 글을 읽는 독자는 ..." 같은 독자 행동 설명을 쓰지 마세요.
- 독자가 왜 찾는지 설명하지 말고, 바로 답을 쓰세요. 예: "제시 마치 캐나다 연봉은 공개된 공식 자료가 제한적입니다. 다만 대표팀 감독 보수는 협회 재정과 계약 기간에 따라 달라집니다."
- "궁금해합니다", "알고 싶어합니다", "찾고 싶어합니다", "등장합니다"처럼 형광펜으로 표시한 듯한 메타 문장은 문맥을 어색하게 만드니 금지합니다.
- 각 문단은 독자 심리 해설이 아니라 실제 사실, 확인 범위, 해석, 주의점으로 시작하세요.
본문 문체와 가독성 규칙:
- 본문 중간에서 "공식 자료에 따르면", "공식 설명은", "~에 따르면" 같은 표현을 반복하지 마세요.
- 검색 참고자료는 사실 확인용으로만 쓰고, 출처 표기는 글 맨 마지막에 짧게 모아 주세요.
- 각 섹션은 독자가 원하는 답을 바로 보여주는 구조로 작성하세요.
- "필요서류", "준비물", "신청방법", "대상", "기간" 같은 섹션은 해당 정보만 짧은 문단, bullet, 표로 정리하세요.
- 확인된 필요서류가 있으면 서류명만 목록으로 정리하세요. 확인되지 않으면 "현재 공개자료 기준 별도 필요서류는 확인되지 않았습니다."처럼 한 문장으로만 처리하세요.
- "공식 문서에 없는 항목은..."처럼 방어적인 문장으로 분량을 채우지 마세요.
- 한 문단은 2~4문장 안에서 끊고, 긴 설명은 <ul><li>...</li></ul> 또는 <table>로 정리하세요.
- 마지막에는 <h2>최종 정리하면,</h2> 섹션으로 핵심을 정리하고, 그 아래에 <p><strong>출처:</strong> ...</p> 형식으로 참고한 대표 출처를 짧게 표시하세요.
"""


def health_news_prompt_block(keyword: str, category: str, source_link: str) -> str:
    if not is_saega2_health_news_item(category, source_link):
        return ""
    return f"""
saega2.seaga.co.kr 신장정신 뉴스 글쓰기 추가 규칙:
- 원문 뉴스 제목은 "{keyword}" 입니다.
- 이 글은 건강 상식이나 증상 가이드가 아니라, 위 원문 뉴스 한 건을 쉽게 해설하는 뉴스 설명 글입니다.
- 블로그 글 제목은 원문 뉴스 제목을 그대로 쓰지 마세요.
- 제목은 원문 뉴스의 핵심 사건을 해설하는 검색형 제목으로 다시 만드세요.
- 원문 제목 전체를 포커스 키워드처럼 반복하지 말고, '신장병', '신약 개발', 기업명처럼 핵심 단어만 자연스럽게 사용하세요.
- 제목과 H2에는 원문 뉴스와 무관한 '증상', '원인', '검사', '치료 가이드', '생활관리 가이드'를 넣지 마세요.
- 첫 요약 문단 바로 아래에는 실제 원문 뉴스 제목과 원문 링크를 보여줘야 합니다.
- 형식은 <blockquote class="source-news"><strong>출처:</strong> <a href="{source_link}">{keyword} 바로가기</a></blockquote> 를 사용하세요.
- 원문 뉴스 제목 전체는 위 출처 블록에서만 한 번 표기하세요. 첫 문단이나 다른 본문 문장에 원문 제목을 따옴표로 다시 인용하거나 반복하지 말고, 발표·연구·협력의 실제 내용을 자기 문장으로 풀어 설명하세요.
- 참고 뉴스 링크는 반드시 "{source_link}" 원문 URL 그대로 사용하세요. 대표 사이트, 검색 링크, 홈페이지 링크로 바꾸지 마세요.
- 본문은 원문 뉴스의 핵심 내용만 해설하세요. 뉴스와 관계없는 일반 건강 정보로 분량을 채우지 마세요.
- 연구·신약·오가노이드·기업협력 뉴스라면 '무엇을 개발했는지', '어떤 기술인지', '왜 신장질환과 관련 있는지', '이번 발표에서 확인된 사실', '아직 임상·치료로 확정되지 않은 한계'를 중심으로 쓰세요.
- H2 제목은 뉴스 내용과 직접 연결되어야 합니다. 예: '실제 같은 신장 오가노이드는 무엇인가', '이번 개발에서 확인된 핵심', '치료에 바로 쓰이는 단계는 아닌 이유'.
- 환자 증상, 병원 방문, 검사, 치료법은 원문 뉴스가 직접 다룬 사실을 설명할 때만 짧게 언급할 수 있으며, 독립된 건강 가이드 섹션으로 만들지 마세요.
"""


def controversy_prompt_block(keyword: str, content_intent: str) -> str:
    controversy_terms = ["논란", "의혹", "쟁점", "해명", "반박", "일베", "표절", "폭로", "갑질", "비판"]
    if content_intent != "controversy" and not any(term in keyword for term in controversy_terms):
        return ""
    return f"""
논란형 이슈 글쓰기 고정 규칙:
- 이 글의 목적은 "{keyword}"가 왜 이슈가 됐는지 독자가 바로 이해하게 만드는 것입니다.
- 프로필, 배경, 일반 설명보다 논란의 발단과 문제가 된 구체 내용을 먼저 다루세요.
- 아래 H2 구조를 반드시 포함하세요. 제목 문구는 자연스럽게 바꿔도 되지만 의미는 빠지면 안 됩니다.
  1. 왜 논란이 됐나
  2. 문제가 된 구체 내용은 무엇인가
  3. 사람들이 쟁점으로 보는 부분
  4. 반박이나 해명은 있었나
  5. 확인된 사실과 추측을 구분하면
- "논란이 있다", "쟁점이 있다"처럼 말만 하지 말고, 어떤 장면·표현·게시물·발언·행동 때문에 문제가 됐는지 구체적으로 설명하세요.
- 검색자료에서 구체 내용이 확인되지 않으면, 확인된 범위와 확인되지 않은 범위를 분리해서 쓰고 "무엇이 아직 불명확한지"를 알려주세요.
- 근거 없는 단정, 인신공격, 혐오 표현 확대 재생산은 하지 마세요.
"""


def recent_issue_prompt_block(keyword: str, category: str, content_intent: str) -> str:
    issue_intents = {"sports_person", "person", "issue", "controversy", "investment", "sports_match"}
    if category != "최신이슈" and content_intent not in issue_intents:
        return ""

    return f"""
최근 이슈형 글쓰기 고정 규칙:
- 이 글은 "{keyword}"를 검색한 사람이 "최근 왜 이슈인지"를 바로 확인하기 위한 글입니다.
- 본문 초반 2~3문단 안에서 최근 검색/뉴스 기준으로 왜 화제가 됐는지 먼저 설명하세요.
- 프로필, 뜻, 배경 설명은 현재 이슈를 설명한 뒤 보조로 배치하세요.
- 검색 참고자료와 직접 확인한 내용에서 확인된 최근 행보, 현재 상황, 국내 독자가 관심 가질 이유, 앞으로의 전망을 우선합니다.
- 스포츠 인물/감독이면 현재 맡은 팀, 최근 성과/경기력, 전술 스타일, 한국 대표팀 또는 국내 팬과의 연결점을 포함하세요.
- 논란 이슈면 왜 논란이 됐는지, 문제가 된 구체 내용, 쟁점, 반박/해명, 확인된 사실과 추측의 경계를 먼저 정리하세요.
- 투자/ETF/코인 이슈면 현재 가격/변동 이유/관련 제도/위험성/앞으로 확인할 지점을 중심으로 쓰세요.
- H2에는 주제에 맞게 '왜 지금 주목받나', '최근 행보', '사람들이 궁금해하는 이유', '앞으로 전망' 같은 현재성 있는 항목을 포함하세요.
- FAQ는 만들지 마세요.
"""


# ══════════════════════════════════════════════════════════
#  꿈해몽 전용 프롬프트 (2026-09 추가)
#
#  왜 따로 두는가
#    공용 프롬프트(build_user_prompt)는 채용·ETF·정책지원·논란까지
#    한 번에 다루느라 지시가 81줄 3,179자다. 꿈해몽 글에는 그중
#    대부분이 필요 없다. 꿈해몽 글 한 편을 쓰면서 'ETF 레버리지 교육',
#    '스포츠 중계 항목 금지', '접수기간' 규칙을 같이 읽는다.
#
#    분량 지시도 서로 어긋난다.
#      공용 프롬프트  : 7,000~9,000자
#      꿈해몽 시스템  : 3,000자 이상
#    재료는 웹페이지 3개 발췌인데 8,000자를 요구하면 물을 타게 된다.
#
#  되돌리는 법
#    .env 에 DREAM_PROMPT_V2=0 한 줄을 넣으면 예전 프롬프트로 돌아간다.
#    코드는 고치지 않아도 된다.
# ══════════════════════════════════════════════════════════

def use_dream_prompt_v2() -> bool:
    return os.getenv("DREAM_PROMPT_V2", "1").strip().lower() not in ("0", "false", "no", "off")


def dream_system_prompt_v2() -> str:
    return """
당신은 20년 경력의 꿈해몽 상담가입니다.
독자는 방금 그 꿈을 꾸고 불안하거나 궁금해서 검색해 들어온 사람입니다.
키워드를 설명하지 말고, 그 사람에게 상담하듯 풀어 주세요.

지켜야 할 것
- 예언처럼 단정하지 마세요. "~하게 됩니다" 대신 "~로 보는 경우가 많습니다".
- 같은 상징이라도 꿈속 감정·장소·행동·반복 여부에 따라 해석이 달라집니다.
  "달라질 수 있습니다"라고 말만 하지 말고, 실제로 다르게 써 주세요.
- 길흉을 단정해 겁주지 마세요. 재물운·복권·대박 같은 말로 기대를 부추기지 마세요.
- 몸이나 마음이 걱정되는 꿈은 병을 진단하지 말고, 수면·스트레스·최근 일 같은
  현실 점검으로 연결하세요.
- 링크를 지어내지 마세요. 링크가 필요하면 https://seaga4.tistory.com 만 쓰세요.
- HTML은 <p>, <h2>, <h3>, <ul>, <li>, <strong>, <a> 만 쓰세요.
- 마지막 소제목은 반드시 <h2>최종 정리하면,</h2> 입니다.
- FAQ, 자주 묻는 질문, Q&A 섹션은 만들지 마세요.
- 출력은 JSON 하나만. JSON 밖에 설명을 붙이지 마세요.
""".strip()


def build_dream_user_prompt_v2(
    keyword: str,
    research: "ResearchContext",
    wordpress_longtails: str = "",
) -> str:
    longtails = (wordpress_longtails or "").strip() or "(없음 - 꿈 주제에서 직접 뽑으세요)"
    참고 = "\n\n".join(
        part for part in [
            (research.snippets or "").strip(),
            (research.source_notes or "").strip(),
        ]
        if part and "확인 실패" not in part and "참고자료 없음" not in part
    ) or "(참고자료 없음 - 상담 경험을 바탕으로 쓰되, 통계나 출처를 지어내지 마세요.)"

    # 이미 발행한 꿈 글 중 관련 있는 것을 골라 본문에 넣게 한다.
    링크블록 = ""
    if dream_links is not None:
        try:
            링크블록 = dream_links.요청문블록(dream_links.관련글고르기(keyword, ""))
        except Exception as 오류:
            logging.warning("꿈해몽 지난 글 링크를 준비하지 못했습니다: %s", 오류)

    return f"""
꿈 주제: {keyword}

이 글에서 다룰 검색어:
{longtails}

참고 자료:
{참고}

{링크블록}

[이 글의 짜임]

1) 도입 - <p> 2~3문단
   이 꿈을 꾸고 검색한 사람의 마음부터 짚어 주세요.
   포커스 키워드 "{keyword}" 를 첫 문단에 자연스럽게 한 번 넣으세요.

2) <h2> 대분류 4~6개
   위 검색어에서 뽑으세요. 새로 만들지 마세요.
   상황별 / 감정별 / 등장인물별 / 반복해서 꿀 때 처럼 나누면 좋습니다.
   대분류 중 하나에는 "{keyword}" 가 들어가야 합니다.

3) 각 <h2> 아래 <h3><strong>꿈 예시</strong></h3> 를 3~6개
   예시 제목은 사람들이 실제로 검색할 말로 지으세요.
     좋은 예 : 고양이가 나를 할퀴는 꿈
     나쁜 예 : 고양이 꿈의 부정적 해석 1
   각 예시는 이 순서로 두세 문단입니다.
     그 장면에서 느낀 감정  ->  그 감정에 따른 해석  ->  현실에서 점검할 것 하나
   번호를 붙이지 말고 문장형으로 쓰세요.

4) <h2>최종 정리하면,</h2>
   5~7개 <li> 로 정리하세요.

[분량]
본문 3,000~4,500자.
예시를 늘려서 채우는 것은 좋습니다. 같은 말을 바꿔 쓰며 늘리는 것은 안 됩니다.
분량을 채우려고 원론적인 조언이나 누구나 아는 배경 설명을 넣지 마세요.

[반환 형식]
JSON 하나만 반환하세요.
{{
  "title": "50자 내외. \\"{keyword}\\" 를 반드시 포함한 검색형 제목",
  "meta": "120~160자. 첫 문장에 \\"{keyword}\\" 포함. 끝에 ' | tags: 태그1, 태그2, ...' 형태로 8~12개",
  "html": "<p>로 시작하는 본문"
}}
JSON 문자열 안의 큰따옴표는 반드시 이스케이프하고, 잘리지 않는 완전한 JSON으로 끝내세요.
""".strip()


def build_user_prompt(
    keyword: str,
    category: str,
    source_link: str = "",
    research: ResearchContext | None = None,
    wordpress_longtails: str = "",
) -> str:
    research = research or build_research_context(keyword, category, source_link)

    # 꿈해몽은 공용 프롬프트의 채용·ETF·정책 규칙이 필요 없다. 전용 프롬프트로 보낸다.
    if category == "꿈해몽" and use_dream_prompt_v2():
        return build_dream_user_prompt_v2(keyword, research, wordpress_longtails)

    link = research.link
    content_intent = classify_content_intent(keyword, category, wordpress_longtails, research.snippets)
    content_rules = intent_article_rules(content_intent, keyword)
    longtail_block = longtail_prompt_block(wordpress_longtails)
    health_news_rules = health_news_prompt_block(keyword, category, source_link)
    controversy_rules = controversy_prompt_block(keyword, content_intent)
    recent_issue_rules = recent_issue_prompt_block(keyword, category, content_intent)
    return f"""
메인 키워드: {keyword}
카테고리: {category}
글 의도 분류: {content_intent}
주제별 필수/금지 규칙:
{content_rules}
최우선 기준:
- 워드프레스용 롱테일 키워드가 있으면 그것이 글의 주제입니다.
- 카테고리와 메인 키워드가 애매해도 롱테일 키워드의 의도를 따라야 합니다.
- 롱테일과 다른 템플릿(예: ETF 롱테일인데 경기/중계, 논란 롱테일인데 프로필 위주, 건강 롱테일인데 신청방법 위주)은 실패입니다.
방금 생성 결과가 실패했다면 대부분 주제 의도와 맞지 않는 항목을 넣었기 때문입니다.
예를 들어 스포츠 감독/인물 이슈에는 정책지원, 신청방법, 지원대상, 접수기간 같은 표현을 절대 넣지 마세요.
롱테일 키워드와 검색 참고자료에 나온 실제 주제만 따라가세요.
워드프레스용 롱테일 키워드 우선순위:
{longtail_block}

롱테일 사용 규칙:
- 위 롱테일은 단순 참고가 아니라 글의 목차 설계 기준입니다.
- 작성 순서는 반드시 1) 롱테일 의도 해석 2) 제목 생성 3) H2/H3 목차 생성 4) 본문 작성 순서입니다.
- 제목에는 상위 롱테일 1~2개의 핵심 의미를 반드시 반영하세요.
- H2/H3 소제목에는 위 롱테일 중 최소 4개 이상을 자연스럽게 반영하세요.
- 본문 첫 1000자 안에 상위 롱테일 3개와 관련된 내용을 다루세요.
- AI가 새로 만든 질문이 아니라, 제공된 롱테일 키워드를 글의 뼈대로 사용하세요.
- 롱테일과 맞지 않는 정책지원/신청/자격/접수 항목은 절대 넣지 마세요.

{readability_prompt_block()}
{health_news_rules}
{controversy_rules}
{recent_issue_rules}
대표 링크: {link}

검색 참고자료:
{research.snippets}

직접 확인한 신뢰 링크 내용:
{research.source_notes}

가장 중요한 원칙:
이 글은 일반적인 설명글이 아니라, 검색자가 지금 알고 싶어 하는 "확인 가능한 사실"을 정리하는 글입니다.
표 형식은 필수가 아닙니다. 중요한 것은 표가 아니라, 독자가 궁금해하는 실제 정보가 들어가는 것입니다.
원론적인 조언, 뻔한 설명, 누구나 아는 배경 설명으로 분량을 채우지 마세요.
목표는 "키워드 설명글"이 아니라 "검색자가 궁금해서 클릭하고 오래 읽을 글"입니다.
글을 쓰기 전에 새 궁금증을 만들지 말고, 위 워드프레스용 롱테일 키워드 우선순위를 먼저 해석하세요.
각 소제목은 위 롱테일 키워드에서 직접 가져오거나, 같은 검색 의도를 유지하는 표현으로만 바꾸세요.
각 소제목에서는 해당 롱테일을 검색한 사람이 왜 그 내용을 궁금해하는지 맥락을 짚고, 그다음 현재 확인 가능한 사실·전망·비교·가능성·주의점을 설명하세요.
예를 들어 코인/주식/가격성 키워드라면 단순히 "무엇인가"보다 현재 가격, 상승 가능성, 전망, 비교 대상, 거래량, 고래 이동, 소각률, 주요 이슈, 주의점을 중심으로 쓰세요.
채용/정책/대출/건강/꿈해몽도 마찬가지로, 개념 설명보다 사람들이 실제로 검색할 만한 현재 궁금증을 우선하세요.
문체는 초보자도 이해할 수 있게 친근한 존댓말로 쓰되, 너무 광고글처럼 과장하지 마세요.

본문은 먼저 워드프레스용 롱테일 키워드와 검색 참고자료를 보고, 검색자가 실제로 원하는 정보가 무엇인지 판단한 뒤 구성하세요.
아래 항목은 모든 글에 억지로 넣는 체크리스트가 아닙니다.
주제와 롱테일 키워드에 맞는 항목만 골라서 깊게 다루고, 맞지 않는 항목은 넣지 마세요.
- 일정/기간/마감: 접수기간, 발표일, 신청기간, 시행일, 마감시간 등이 검색 의도와 관련 있을 때만 포함
- 대상/자격/조건: 누가 해당되는지, 누가 제외되는지 궁금해하는 주제일 때 포함
- 신청/확인 위치: 공식 사이트, 메뉴, 버튼, 확인 경로가 중요한 주제일 때 포함
- 준비물/서류/절차: 실제 준비나 신청 과정이 있는 주제일 때 포함
- 숫자로 보는 정보: 금액, 한도, 채용 규모, 모집 인원, 기간, 횟수, 연령, 소득 기준 등이 핵심인 주제일 때 포함
- 현재 가능한 행동: 지금 신청 가능, 대기 필요, 마감, 공식 공고 확인 필요처럼 상태 확인이 필요한 주제일 때 포함
- 공식 링크: 뉴스나 블로그가 아니라 대표 공식 사이트 링크를 중심으로 안내

확인되지 않은 날짜, 금액, 인원, 조건은 만들어내지 마세요.
확인되지 않은 경우에는 "현재 공개 자료에서는 확인되지 않으며, 공식 사이트에서 확인해야 합니다"라고 쓰고, 대신 어디에서 확인해야 하는지 구체적으로 안내하세요.

주제별 참고 방향입니다. 해당 주제일 때만 적용하세요.
- 채용 관련 롱테일 키워드일 때: 공고명, 접수기간, 마감시간, 신입/경력 구분, 직무, 근무지, 전형절차, 지원 링크, 현재 지원 가능 여부
- 정책지원/지원금 관련 롱테일 키워드일 때: 신청기간, 지급대상, 지급금액, 신청방법, 필요서류, 제외대상, 공식 신청처
- 대출 관련 롱테일 키워드일 때: 한도, 금리, 대상, 신청조건, 상환방식, 중도상환수수료, 공식 조회처
- ETF/레버리지/인버스 관련 롱테일 키워드일 때: 레버리지 ETF 뜻, 레버리지 ETF 교육, 무료 교육 위치, 교육 이수 필요 여부, 거래 전 확인사항, 투자 위험성, 일반 ETF와 차이, 초보자가 주의할 점을 중심으로 작성. 경기 일정, 중계, 라인업, 하이라이트 같은 스포츠 항목은 절대 넣지 않기
- 건강 관련 롱테일 키워드일 때: 증상, 병원에 가야 하는 기준, 검사 항목, 진료과, 생활관리, 공식 건강정보 링크
- 꿈해몽 관련 롱테일 키워드일 때: 실제 꿈 전문 해몽가처럼 장면, 상징, 감정, 등장인물, 장소, 현실의 고민을 연결해서 상세히 풀기. 상황별 다른 해석, 좋은 의미와 주의할 의미, 현실에서 점검할 부분을 포함하되 예언처럼 단정하지 않기
- 논란/의혹/쟁점 관련 롱테일 키워드일 때: 왜 논란이 됐는지, 문제가 된 구체 내용, 쟁점별 비판과 반박, 당사자 해명이나 공식 입장, 확인된 사실과 추측의 경계, 독자가 오해하기 쉬운 부분을 반드시 포함

검색자가 이 글을 클릭하는 이유는 정보를 바로 확인하기 위해서입니다.
글을 쓰기 전에 위 롱테일 키워드의 검색 의도를 먼저 파악하되, 본문에는 기계적인 질문 제목이나 질문 목록을 넣지 마세요.
본문은 사람이 직접 쓴 글처럼 부드러운 자연어 도입부로 시작하고, 첫 문단 안에 포커스 키워드 "{keyword}"를 자연스럽게 1회 포함하세요.
첫 번째 또는 두 번째 H2 제목에도 포커스 키워드 "{keyword}"를 자연스럽게 포함하세요.
각 섹션은 질문 목록이 아니라 독자가 실제로 알고 싶은 내용을 차분히 설명하는 문단형 정보글로 구성하세요.
확인된 사실은 구체적으로 쓰고, 확인되지 않은 사실은 추측하지 말고 "공식 사이트에서 확인해야 할 항목"으로 안내하세요.

아래 조건에 맞춰 한국어 SEO 블로그 콘텐츠를 생성하세요.

1. 위에 제공된 워드프레스용 롱테일 키워드에서 핵심 주제를 뽑고, 그 순서대로 제목, H2/H3, 본문을 작성하세요. 새로운 롱테일을 임의로 만들지 마세요.
2. SEO 제목은 50자 내외로 작성하되, 포커스 키워드 "{keyword}"를 반드시 포함하고 검색 의도가 분명해야 합니다. 단, 뉴스 원문 제목을 그대로 복사하지 말고 검색형 블로그 제목으로 다시 작성하세요.
3. 메타 디스크립션은 120~160자 정도로 작성하고, 첫 문장에 포커스 키워드 "{keyword}"를 반드시 포함하세요. 태그 8~12개도 함께 제안하세요.
4. HTML 본문은 WordPress/Tistory에 바로 붙여넣을 수 있게 작성하세요.
5. HTML 본문은 기존보다 훨씬 길게 7000~9000자 정도로 작성하세요.
6. 반드시 지킬 글 흐름:
   - HTML 본문은 반드시 <p>로 시작하세요. 메타 디스크립션은 별도 문장이므로, 첫 문단에서 메타의 문장이나 요약을 다시 반복하지 마세요. 첫 문단은 독자가 지금 알아야 할 배경·사실·쟁점 중 하나를 자연스럽게 설명하고 포커스 키워드 "{keyword}"를 포함
   - 기계적인 질문 목록 대신 자연스러운 설명형 문단 사용
   - 포커스 키워드 "{keyword}"가 포함된 H2 제목을 최소 1개 포함
   - 롱테일 키워드와 실제 검색 궁금증에 맞는 구체 정보만 섹션화
   - 공식/대표 사이트 바로가기 포함
   - 마지막 섹션 제목은 반드시 "최종 정리하면,"으로 쓰고, 위 내용을 5~7개 bullet로 정리
   - 마지막 정리 아래에 참고 출처를 짧게 표시
7. 대표 링크 {link} 를 본문 중간과 마지막 출처 줄에 실제 <a href="{link}">...</a> 형태로 넣으세요.
8. 검색 참고자료와 직접 확인한 신뢰 링크 내용에 없는 일정, 기간, 조건, 금액은 지어내지 마세요. 확인되지 않으면 "공식 사이트에서 확인해야 할 항목"으로 안내하세요.
9. FAQ, 자주 묻는 질문, Q&A 섹션은 만들지 마세요.
10. 출력은 반드시 JSON 하나만 반환하세요. JSON 밖 설명은 금지합니다.
11. JSON 문자열 안의 큰따옴표는 반드시 이스케이프하고, 잘리지 않는 완전한 JSON으로 끝내세요.

반환 형식:
{{
  "title": "SEO 최적화 제목",
  "meta": "메타 디스크립션 ... | tags: 태그1, 태그2, 태그3",
  "html": "<p>...</p><h2>...</h2><p>...</p>"
}}
"""


def build_retry_user_prompt(
    keyword: str,
    category: str,
    source_link: str = "",
    research: ResearchContext | None = None,
    wordpress_longtails: str = "",
) -> str:
    research = research or build_research_context(keyword, category, source_link)
    link = research.link
    content_intent = classify_content_intent(keyword, category, wordpress_longtails, research.snippets)
    content_rules = intent_article_rules(content_intent, keyword)
    longtail_block = longtail_prompt_block(wordpress_longtails)
    health_news_rules = health_news_prompt_block(keyword, category, source_link)
    controversy_rules = controversy_prompt_block(keyword, content_intent)
    recent_issue_rules = recent_issue_prompt_block(keyword, category, content_intent)
    return f"""
메인 키워드: {keyword}
카테고리: {category}
글 의도 분류: {content_intent}
주제별 필수/금지 규칙:
{content_rules}
최우선 기준:
- 워드프레스용 롱테일 키워드가 있으면 그것이 글의 주제입니다.
- 카테고리와 메인 키워드가 애매해도 롱테일 키워드의 의도를 따라야 합니다.
- 롱테일과 다른 템플릿(예: ETF 롱테일인데 경기/중계, 논란 롱테일인데 프로필 위주, 건강 롱테일인데 신청방법 위주)은 실패입니다.
워드프레스용 롱테일 키워드 우선순위:
{longtail_block}

롱테일 사용 규칙:
- 위 롱테일은 단순 참고가 아니라 글의 목차 설계 기준입니다.
- 작성 순서는 반드시 1) 롱테일 의도 해석 2) 제목 생성 3) H2/H3 목차 생성 4) 본문 작성 순서입니다.
- 제목에는 상위 롱테일 1~2개의 핵심 의미를 반드시 반영하세요.
- H2/H3 소제목에는 위 롱테일 중 최소 4개 이상을 자연스럽게 반영하세요.
- 본문 첫 1000자 안에 상위 롱테일 3개와 관련된 내용을 다루세요.
- AI가 새로 만든 질문이 아니라, 제공된 롱테일 키워드를 글의 뼈대로 사용하세요.
- 롱테일과 맞지 않는 정책지원/신청/자격/접수 항목은 절대 넣지 마세요.

{readability_prompt_block()}
{health_news_rules}
{controversy_rules}
{recent_issue_rules}
대표 링크: {link}

검색 참고자료:
{research.snippets}

직접 확인한 신뢰 링크 내용:
{research.source_notes}

가장 중요한 원칙:
표 형식이 중요한 것이 아니라, 검색자가 실제로 알고 싶어 하는 확인 가능한 사실이 중요합니다.
원론적인 설명으로 분량을 채우지 말고, 롱테일 키워드와 검색 의도에 맞는 구체 정보부터 쓰세요.
일정/기간/마감, 대상/조건, 신청 또는 확인 위치, 준비물/절차, 금액/한도/인원 같은 항목은 해당 주제에 필요할 때만 넣으세요.
확인되지 않은 사실은 만들어내지 말고, 공식 사이트에서 확인해야 할 항목과 확인 위치를 안내하세요.
목표는 키워드 설명글이 아니라 검색자가 궁금해서 클릭하고 오래 읽을 글입니다.
위 워드프레스용 롱테일 키워드를 먼저 해석한 뒤, 그 롱테일을 H2/H3 소제목에 자연스럽게 반영하세요.
각 소제목은 롱테일 검색자가 실제로 알고 싶은 내용 중심으로 작성하세요.
초보자도 이해할 수 있게 친근한 존댓말을 사용하세요.

이전 응답이 너무 길어 JSON 파싱에 실패했습니다.
이번에는 안정적으로 작성하되, 정보 밀도는 유지하세요.

필수 조건:
1. SEO 제목 1개
2. 메타 디스크립션과 태그
3. HTML 본문은 5000~7000자
4. 기계적인 질문 섹션은 만들지 말고, HTML 본문은 <p> 도입 문단으로 시작
5. SEO 제목, 메타 디스크립션 첫 문장, 본문 첫 문단, H2 제목 중 최소 1개에 포커스 키워드 "{keyword}"를 자연스럽게 포함
6. 롱테일 키워드와 검색 의도에 맞는 구체 사실을 우선 포함. 맞지 않는 항목은 억지로 넣지 않기
   - 논란형 글은 왜 논란이 됐는지와 문제가 된 구체 내용을 반드시 설명하기
   - ETF/레버리지 글에는 경기 일정, 중계, 라인업, 하이라이트를 절대 넣지 않기
7. 확인되지 않은 숫자나 날짜는 추측하지 말고 공식 사이트에서 확인해야 할 항목으로 안내
8. 대표 링크는 <a href="{link}">공식 사이트에서 확인하기</a> 형태로 삽입
9. FAQ는 만들지 말고, 마지막에 "최종 정리하면," 섹션으로 핵심 내용을 bullet 정리한 뒤 출처를 짧게 표시
10. 반드시 완전한 JSON 하나만 반환

반환 형식:
{{
  "title": "SEO 최적화 제목",
  "meta": "메타 디스크립션 ... | tags: 태그1, 태그2, 태그3",
  "html": "<p>...</p><h2>...</h2><p>...</p>"
}}
"""


def call_openai_with_retry(
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_prompt: str,
    retries: int = 3,
) -> str:
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            response = client.responses.create(
                model=model,
                max_output_tokens=14000,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "seo_article",
                        "schema": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "title": {"type": "string"},
                                "meta": {"type": "string"},
                                "html": {"type": "string"},
                            },
                            "required": ["title", "meta", "html"],
                        },
                        "strict": True,
                    }
                },
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return response.output_text
        except Exception as exc:
            last_error = exc
            logging.warning("OpenAI 호출 실패 %s/%s: %s", attempt, retries, exc)
            if attempt < retries:
                time.sleep(5 * attempt)

    raise RuntimeError(f"OpenAI API 호출 실패: {last_error}")


def keyword_in_text(keyword: str, text: str) -> bool:
    compact_keyword = re.sub(r"\s+", "", keyword or "").lower()
    compact_text = re.sub(r"\s+", "", text or "").lower()
    return bool(compact_keyword and compact_keyword in compact_text)


def first_html_text(html: str, max_chars: int = 500) -> str:
    plain_text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", plain_text).strip()[:max_chars]


def h2_texts(html: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", match).strip()
        for match in re.findall(r"<h2[^>]*>(.*?)</h2>", html, flags=re.IGNORECASE | re.DOTALL)
    ]


def meta_description_only(meta: str) -> str:
    description = re.split(r"\s+\|\s*(?:tags?|태그)\s*:", meta, maxsplit=1, flags=re.IGNORECASE)[0]
    return re.sub(r"\s+", " ", description).strip()


def ensure_meta_intro_paragraph(html: str, meta: str, keyword: str) -> str:
    description = meta_description_only(meta)
    if not description:
        return html

    first_text = first_html_text(html, 260)
    description_head = description[:40]
    if description_head and description_head in first_text:
        return html

    if keyword and not keyword_in_text(keyword, description):
        description = f"{keyword} 관련 핵심 내용을 정리했습니다. {description}"

    intro = f"<p>{description}</p>"
    return intro + "\n" + html


def is_http_url(value: str) -> bool:
    return bool(value and value.startswith(("http://", "https://")))


def is_saega2_health_news_item(category: str, source_link: str) -> bool:
    return category == "신장정신" and is_http_url(source_link)


def normalize_for_similarity(value: str) -> str:
    return re.sub(r"[^가-힣A-Za-z0-9]", "", value or "").lower()


def remove_health_news_title_from_opening(html: str, keyword: str) -> str:
    """Keep a long original news headline in the source block, not the opening."""
    if len(normalize_for_similarity(keyword)) < 20:
        return html
    match = re.search(r"(<p[^>]*>)(.*?)(</p>)", html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return html

    opening = match.group(2)
    plain_opening = html_lib.unescape(re.sub(r"<[^>]+>", " ", opening))
    if normalize_for_similarity(keyword) not in normalize_for_similarity(plain_opening):
        return html

    cleaned = opening
    for title in {keyword, html_lib.escape(keyword, quote=False)}:
        cleaned = re.sub(
            rf"[\"'‘’“”]?\s*{re.escape(title)}\s*[\"'‘’“”]?\s*(?:이라는|라는|이란)\s*(?:보도|소식|발표)(?:는|에서)?",
            "이번 발표에서는",
            cleaned,
        )
        cleaned = cleaned.replace(title, "이번 소식")

    return html[: match.start(2)] + cleaned + html[match.end(2) :]


def ensure_health_news_reference(html: str, keyword: str, source_link: str) -> str:
    if not is_http_url(source_link):
        return html
    html = remove_health_news_title_from_opening(html, keyword)
    if source_link in html and ("참고 뉴스" in html[:1200] or "출처:" in html[:1200]):
        return html

    news_title = html_lib.escape(keyword.strip())
    news_url = html_lib.escape(source_link.strip(), quote=True)
    news_box = (
        '<blockquote class="source-news">'
        f'<strong>출처:</strong> <a href="{news_url}" target="_blank" rel="noopener">{news_title} 바로가기</a>'
        "</blockquote>"
    )
    first_paragraph = re.search(r"</p>", html, flags=re.IGNORECASE)
    if first_paragraph:
        insert_at = first_paragraph.end()
        return html[:insert_at] + "\n" + news_box + html[insert_at:]
    return news_box + "\n" + html


def validate_health_news_title(article: GeneratedArticle, keyword: str, category: str, source_link: str) -> None:
    if not is_saega2_health_news_item(category, source_link):
        return

    normalized_keyword = normalize_for_similarity(keyword)
    normalized_title = normalize_for_similarity(article.title)
    if len(normalized_keyword) >= 24 and normalized_keyword[:24] in normalized_title:
        raise ValueError("신장정신 글 제목이 뉴스 원문 제목을 그대로 따라가고 있습니다.")
    if len(normalized_title) >= 24 and normalized_title[:24] in normalized_keyword:
        raise ValueError("신장정신 글 제목이 뉴스 원문 제목과 너무 유사합니다.")
    generic_guide_terms = ["증상", "원인", "검사", "치료 가이드", "생활관리", "총정리"]
    if "가이드" in article.title or sum(term in article.title for term in generic_guide_terms) >= 2:
        raise ValueError("신장정신 뉴스 글 제목이 원문과 무관한 일반 건강 가이드로 작성되었습니다.")


def health_news_title_needs_rewrite(title: str, keyword: str) -> bool:
    normalized_keyword = normalize_for_similarity(keyword)
    normalized_title = normalize_for_similarity(title)
    if len(normalized_keyword) >= 24 and normalized_keyword[:24] in normalized_title:
        return True
    if len(normalized_title) >= 24 and normalized_title[:24] in normalized_keyword:
        return True
    generic_guide_terms = ["증상", "원인", "검사", "치료 가이드", "생활관리", "총정리"]
    return "가이드" in title or sum(term in title for term in generic_guide_terms) >= 2


def health_news_focus_words(keyword: str) -> list[str]:
    """Extract factual news terms without copying a long source headline."""
    preferred = [
        "공황장애", "ADHD", "급성콩팥손상", "신장", "콩팥", "탈수", "무더위",
        "오가노이드", "신약", "세포", "임상", "세미나", "연구", "개발", "협력",
    ]
    found = [term for term in preferred if term in keyword]
    if len(found) < 2:
        for term in re.findall(r"[가-힣A-Za-z0-9]{2,}", keyword):
            if term not in found and term not in {"관련", "전문가", "직접", "개최", "환자", "주의", "뉴스"}:
                found.append(term)
            if len(found) >= 3:
                break
    return found[:3] or ["신장 건강"]


def health_news_click_title(keyword: str, tistory_variant: bool = False) -> str:
    """Create a reader-facing news explainer title without repeating the source headline."""
    focus_words = health_news_focus_words(keyword)
    focus = "·".join(word for word in focus_words if word != "세미나") or "·".join(focus_words)
    if "세미나" in keyword:
        ending = "어떤 이야기 나올까" if tistory_variant else "참여 전 알아둘 핵심"
        return f"{focus} 무료 세미나, {ending}"
    if "오가노이드" in keyword:
        ending = "왜 주목받을까" if tistory_variant else "이번 연구가 의미하는 것"
        return f"실제 신장처럼 만든 오가노이드, {ending}"
    if "탈수" in keyword or "급성콩팥손상" in keyword:
        ending = "더위 속 신장이 보내는 위험 신호" if tistory_variant else "이번 경고에서 꼭 봐야 할 내용"
        return f"{focus}, {ending}"
    if any(term in keyword for term in ["신약", "개발", "연구", "협력", "임상"]):
        ending = "무엇이 달라지는 걸까" if tistory_variant else "이번 발표가 주목받는 이유"
        return f"{focus} 뉴스, {ending}"
    ending = "쉽게 풀어봅니다" if tistory_variant else "이번 발표에서 확인된 내용"
    return f"{focus} 관련 소식, {ending}"


def normalize_health_news_title(
    article: GeneratedArticle,
    keyword: str,
    category: str,
    source_link: str,
    tistory_variant: bool = False,
) -> GeneratedArticle:
    """Keep health-news drafts usable when the model echoes a source headline."""
    if not is_saega2_health_news_item(category, source_link):
        return article
    if not health_news_title_needs_rewrite(article.title, keyword):
        return article

    title = health_news_click_title(keyword, tistory_variant=tistory_variant)
    logging.info("신장정신 뉴스 제목을 해설형 제목으로 보정합니다: %s", title)
    return GeneratedArticle(title=title, meta=article.meta, html=article.html)


def validate_health_news_focus(article: GeneratedArticle, keyword: str, category: str, source_link: str) -> None:
    """연구 뉴스가 일반 증상·검사 가이드로 바뀌는 것을 막습니다."""
    if not is_saega2_health_news_item(category, source_link):
        return

    research_terms = ["연구", "개발", "신약", "오가노이드", "세포", "임상", "협력", "계약"]
    if not any(term in keyword for term in research_terms):
        return

    headings = re.findall(r"<h2[^>]*>(.*?)</h2>", article.html, flags=re.IGNORECASE | re.DOTALL)
    heading_text = " ".join(re.sub(r"<[^>]+>", " ", heading) for heading in headings)
    generic_terms = ["증상·원인", "원인·검사", "검사·치료", "치료 가이드", "생활관리 가이드", "증상과 원인"]
    generic_count = sum(1 for term in generic_terms if term in heading_text)
    news_terms = ["개발", "기술", "연구", "신약", "오가노이드", "임상", "협력", "의미", "한계"]
    news_count = sum(1 for term in news_terms if term in heading_text)
    if generic_count >= 2 and news_count < 2:
        raise ValueError("신장 연구 뉴스가 일반 증상·검사·치료 가이드로 작성되었습니다.")


def focus_terms_for_long_keyword(keyword: str) -> list[str]:
    blocked = {
        "관련",
        "의미",
        "영향",
        "네번째",
        "파트너와",
        "개발",
        "협력",
        "신약",
        "뉴스",
        "기준",
        "현재",
    }
    terms = re.findall(r"[가-힣A-Za-z0-9]{2,}", keyword or "")
    result: list[str] = []
    for term in terms:
        clean = term.strip()
        if clean in blocked:
            continue
        if clean not in result:
            result.append(clean)
    return result[:6]


def long_focus_in_text(keyword: str, text: str) -> bool:
    terms = focus_terms_for_long_keyword(keyword)
    if not terms:
        return keyword_in_text(keyword, text)
    matched = sum(1 for term in terms if keyword_in_text(term, text))
    return matched >= min(2, len(terms))


def validate_focus_keyword(article: GeneratedArticle, keyword: str) -> None:
    compact_keyword = re.sub(r"\s+", "", keyword or "")
    if len(compact_keyword) >= 30:
        if not long_focus_in_text(keyword, article.title):
            raise ValueError(f"SEO 제목에 긴 키워드의 핵심 단어가 부족합니다: {keyword}")
        if not long_focus_in_text(keyword, article.meta):
            raise ValueError(f"메타 디스크립션에 긴 키워드의 핵심 단어가 부족합니다: {keyword}")
        if not long_focus_in_text(keyword, first_html_text(article.html)):
            raise ValueError(f"본문 초반에 긴 키워드의 핵심 단어가 부족합니다: {keyword}")
        if not any(long_focus_in_text(keyword, heading) for heading in h2_texts(article.html)):
            raise ValueError(f"H2 제목에 긴 키워드의 핵심 단어가 부족합니다: {keyword}")
        return

    if not keyword_in_text(keyword, article.title):
        raise ValueError(f"SEO 제목에 포커스 키워드가 없습니다: {keyword}")
    if not keyword_in_text(keyword, article.meta):
        raise ValueError(f"메타 디스크립션에 포커스 키워드가 없습니다: {keyword}")
    if not keyword_in_text(keyword, first_html_text(article.html)):
        raise ValueError(f"본문 초반에 포커스 키워드가 없습니다: {keyword}")
    if not any(keyword_in_text(keyword, heading) for heading in h2_texts(article.html)):
        raise ValueError(f"H2 제목에 포커스 키워드가 없습니다: {keyword}")


def parse_generated_article(
    raw_text: str,
    keyword: str = "",
    ensure_meta_intro: bool = True,
) -> GeneratedArticle:
    text = raw_text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("OpenAI 응답에서 JSON 객체를 찾지 못했습니다.")
        json_text = re.sub(r",\s*}", "}", match.group(0))
        data = json.loads(json_text)

    title = normalize_output(data.get("title", ""), 90)
    meta = normalize_output(data.get("meta", ""), 500)
    html = str(data.get("html", "")).strip()

    if not title or not meta or not html:
        raise ValueError("OpenAI JSON에 title/meta/html 중 빠진 값이 있습니다.")
    if "<h2" not in html.lower() or "<p" not in html.lower():
        raise ValueError("HTML 본문에 기본 구조가 부족합니다.")
    if "##LINK_HERE##" in html or 'href=""' in html or "href=''" in html:
        raise ValueError("HTML 본문에 빈 링크가 포함되어 있습니다.")
    if ensure_meta_intro:
        html = ensure_meta_intro_paragraph(html, meta, keyword)
    plain_text = re.sub(r"<[^>]+>", " ", html)
    plain_text = re.sub(r"\s+", " ", plain_text).strip()
    min_article_chars = int(os.getenv("MIN_ARTICLE_CHARS", "2500"))
    if len(plain_text) < min_article_chars:
        raise ValueError(f"HTML 본문이 너무 짧습니다. 현재 {len(plain_text)}자 / 최소 {min_article_chars}자")

    article = polish_readability_style(GeneratedArticle(title=title, meta=meta, html=html))
    if keyword:
        validate_focus_keyword(article, keyword)

    return article


def generate_news_bundle_article(
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> GeneratedArticle:
    """Generate a source-news brief and retry once if its body is too short."""
    raw_text = call_openai_with_retry(
        client=client,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )
    try:
        return parse_generated_article(raw_text, ensure_meta_intro=False)
    except ValueError as exc:
        if "HTML 본문이 너무 짧습니다" not in str(exc):
            raise
        logging.info("뉴스 묶음 본문이 짧아 분량 보완 재작성을 진행합니다.")
        retry_prompt = user_prompt + "\n\n[분량 보완 재작성] 본문은 최소 3,000자 이상으로 완성하세요. 뉴스마다 별도 H2를 두고, 발표 내용·확인 가능한 변화·독자가 알아둘 점을 구체적으로 충분히 설명하세요. JSON은 끝까지 완성하세요."
        raw_text = call_openai_with_retry(
            client=client,
            model=model,
            system_prompt=system_prompt,
            user_prompt=retry_prompt,
        )
        return parse_generated_article(raw_text, ensure_meta_intro=False)


def normalize_output(value: Any, max_length: int) -> str:
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    return value[:max_length]


def should_generate_tistory_variant(category: str) -> bool:
    return any(part in category for part in ["최신이슈", "대출", "대출관련", "신장정신", "꿈해몽"])


def tistory_audience_rules(category: str) -> str:
    if "대출" in category:
        return """
- 대상 독자: 20대 이상. 첫 대출, 신용점수, 한도 조회, 금리 비교, 연체 주의점처럼 실제 행동에 필요한 내용을 쉽게 설명합니다.
- 워드프레스 글과 같은 문장, 같은 소제목을 반복하지 말고 티스토리 독자용 생활 금융 글처럼 작성합니다.
- 과장된 승인 가능성이나 확정 금리 표현은 금지합니다.
"""
    if "신장정신" in category:
        return """
- 대상 독자: 환자와 가족. 워드프레스 글보다 더 친근하고 이해하기 쉬운 말투로, 증상·검사·생활관리·병원 상담 기준을 중심으로 씁니다.
- 같은 뉴스나 주제라도 워드프레스 글과 다른 제목, 다른 소제목, 다른 문장 흐름으로 작성합니다.
- 불안감을 키우지 말고, 확인해야 할 사항과 의료진 상담 기준을 차분하게 정리합니다.
"""
    if "꿈해몽" in category:
        return """
- 대상 독자: 꿈 내용을 검색한 일반 독자. 실제 꿈해몽 전문가가 풀이하듯 상징, 감정, 현실 상황을 연결해 자세히 설명합니다.
- 같은 주제라도 여러 꿈 장면을 나누어 구체적으로 해몽합니다.
"""
    return """
- 대상 독자: 40대 이상 티스토리 독자. 너무 빠른 인터넷 밈 문체보다, 왜 화제인지와 지금 확인할 핵심을 차분하고 쉽게 설명합니다.
- 워드프레스 글은 검색 정보 정리형, 티스토리 글은 클릭 유도형 요약과 생활 밀착 해석형으로 다르게 작성합니다.
- 같은 제목, 같은 소제목, 같은 문장을 반복하지 마세요.
"""


def build_tistory_user_prompt(
    keyword: str,
    category: str,
    source_link: str = "",
    research: ResearchContext | None = None,
    tistory_longtails: str = "",
    wordpress_article: GeneratedArticle | None = None,
) -> str:
    base_prompt = build_user_prompt(keyword, category, source_link, research, tistory_longtails)
    wordpress_article = wordpress_article or GeneratedArticle("", "", "")
    return f"""
{base_prompt}

[티스토리 전용 추가 지침]
이 결과물은 티스토리 전용 글입니다. 같은 주제가 워드프레스에도 올라갈 수 있으므로, 문장·제목·소제목·글 흐름을 반드시 다르게 작성하세요.
티스토리 롱테일 키워드를 우선 기준으로 삼고, 워드프레스 글과 같은 표현을 재사용하지 마세요.
[티스토리 전용 절대 규칙]
이 결과물은 워드프레스 글의 복사본이나 요약본이 아닙니다. 같은 주제라도 제목, 메타 디스크립션, 도입 문단, H2, 결론의 관점과 문장을 새로 작성하세요.
워드프레스 제목: {wordpress_article.title}
워드프레스 메타: {wordpress_article.meta}
위 두 문장을 그대로 쓰거나 어순만 바꾸어 쓰지 마세요. 티스토리 롱테일 키워드를 가장 먼저 반영해 독자에게 다른 이유로 클릭할 만한 글을 만드세요.

티스토리 글의 말투는 40대 이상 독자에게 말하듯 친절하고 쉽게 씁니다. 너무 딱딱한 보고서체, 과도한 강조, '정리합니다', '확인해야 합니다'의 반복을 피하세요.
문장 끝은 '~에요'와 '~입니다'를 자연스럽게 섞고, 어려운 표현은 바로 풀어서 설명하세요. 독자의 궁금증을 먼저 공감한 뒤 답을 알려주는 흐름으로 작성하세요.
제목은 포커스 키워드를 포함하되 워드프레스 제목과 다른 관심사·질문을 중심으로 작성하세요. 메타 디스크립션도 전혀 다른 문장으로 만드세요.
메타 디스크립션은 검색용 별도 문장입니다. HTML 본문의 첫 문단에 메타 디스크립션을 그대로 쓰거나 비슷한 요약을 반복하지 말고, 독자가 왜 이 이슈를 궁금해하는지 또는 바로 알아야 할 사실로 자연스럽게 시작하세요.
본문 중간 또는 '최종 정리하면,' 바로 앞에는 워드프레스 상세 글로 이어질 수 있는 여백을 남기되, '자세히 보기'라는 문구 자체는 HTML에 직접 넣지 마세요.
{tistory_audience_rules(category)}
"""


def compact_article_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html_lib.unescape(value)
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", value).lower()


def validate_tistory_variant(wordpress_article: GeneratedArticle, tistory_article: GeneratedArticle) -> None:
    title_ratio = SequenceMatcher(
        None,
        compact_article_text(wordpress_article.title),
        compact_article_text(tistory_article.title),
    ).ratio()
    meta_ratio = SequenceMatcher(
        None,
        compact_article_text(wordpress_article.meta),
        compact_article_text(tistory_article.meta),
    ).ratio()
    wordpress_intro = compact_article_text(wordpress_article.html)[:700]
    tistory_intro = compact_article_text(tistory_article.html)[:700]
    intro_ratio = SequenceMatcher(None, wordpress_intro, tistory_intro).ratio()
    if title_ratio >= 0.88 or meta_ratio >= 0.82 or intro_ratio >= 0.82:
        raise ValueError(
            f"티스토리 글이 워드프레스 글과 너무 유사합니다: title={title_ratio:.2f}, "
            f"meta={meta_ratio:.2f}, intro={intro_ratio:.2f}"
        )


def update_today_article(
    item: WritingQueueItem,
    article: GeneratedArticle,
    link: str,
    tistory_article: GeneratedArticle | None = None,
) -> None:
    ensure_optional_today_headers(item.worksheet)
    item.worksheet.update(
        range_name=f"D{item.row_number}:H{item.row_number}",
        values=[[article.title, article.meta, article.html, WRITING_DONE_VALUE, link]],
        value_input_option="USER_ENTERED",
    )
    if tistory_article:
        item.worksheet.update(
            range_name=f"J{item.row_number}:L{item.row_number}",
            values=[[tistory_article.title, tistory_article.meta, tistory_article.html]],
            value_input_option="USER_ENTERED",
        )


def mark_longtail_required(item: WritingQueueItem) -> None:
    item.worksheet.update_cell(item.row_number, item.status_col, "롱테일필요")


def health_news_bundle_entries(items: list[WritingQueueItem]) -> list[dict[str, str]]:
    """Build the verified source list for one health-news briefing."""
    entries: list[dict[str, str]] = []
    for item in items:
        if not is_http_url(item.source_link):
            continue
        entries.append({"title": item.keyword, "link": item.source_link, "snippet": "신장정신 탭에서 선택한 원문 뉴스"})
    return entries


def health_news_bundle_source_block(entries: list[dict[str, str]]) -> str:
    if not entries:
        return ""
    links = "".join(
        f'<li><a href="{html_lib.escape(entry["link"], quote=True)}" target="_blank" rel="noopener">'
        f'{html_lib.escape(entry["title"])} 바로가기</a></li>'
        for entry in entries
    )
    return (
        '<blockquote class="source-news"><strong>이번 글에서 정리한 뉴스 출처</strong>'
        f"<ul>{links}</ul></blockquote>"
    )


def ensure_health_news_bundle_sources(html: str, entries: list[dict[str, str]]) -> str:
    """Always place the real selected-news links directly below the opening."""
    if not entries:
        return html
    if all(entry["link"] in html for entry in entries):
        return html
    block = health_news_bundle_source_block(entries)
    first_paragraph = re.search(r"</p>", html, flags=re.IGNORECASE)
    if first_paragraph:
        return html[: first_paragraph.end()] + "\n" + block + html[first_paragraph.end() :]
    return block + "\n" + html


def build_health_news_bundle_prompt(
    entries: list[dict[str, str]],
    source_notes: str,
    tistory_variant: bool = False,
    wordpress_article: GeneratedArticle | None = None,
) -> str:
    source_list = "\n".join(
        f"- 뉴스 제목: {entry['title']}\n  원문 링크: {entry['link']}" for entry in entries
    )
    variant_rules = ""
    if tistory_variant and wordpress_article:
        variant_rules = f"""
티스토리용 별도 글입니다.
- 워드프레스 제목: {wordpress_article.title}
- 위 제목, 메타, 도입 문장을 그대로 쓰거나 어순만 바꾸지 마세요.
- 환자와 가족이 읽기 쉬운 친근한 말투로 쓰고, 뉴스 항목의 순서와 해설 관점을 다르게 구성하세요.
"""

    return f"""
당신은 신장·정신건강 분야 뉴스 편집자입니다.
아래는 사용자가 오늘 블로그 작성용으로 직접 선택한 실제 뉴스들입니다.
이 뉴스들을 **한 편의 뉴스 브리핑 글**로 간추려 설명하세요. 롱테일 키워드를 만들거나 사용하지 마세요.

[선택한 원문 뉴스]
{source_list}

[직접 확인한 원문 요약]
{source_notes or '원문 제목과 링크에서 확인되는 범위만 사용하세요.'}

가장 중요한 규칙:
- 일반적인 증상·원인·검사·치료 가이드 글이 아닙니다. 선택된 뉴스의 발표, 연구, 협력, 경고, 행사, 정책 등 **뉴스 사실 자체**를 알기 쉽게 해설하는 글입니다.
- 뉴스 제목을 블로그 제목으로 그대로 복사하지 마세요. 여러 뉴스의 공통 핵심어 또는 가장 큰 주제로 새 제목을 만드세요.
- 제목이 어렵다면 "신장·정신건강 주요 뉴스 정리"처럼 만들되, 실제 선택 뉴스와 연결되는 핵심어를 1~3개 포함하세요.
- 각 뉴스마다 무슨 일이 발표됐는지, 핵심 내용이 무엇인지, 왜 주목할 만한지, 아직 확정되지 않은 부분이 무엇인지 사실 범위에서 설명하세요.
- 질환의 증상·원인·검사·치료를 독립적인 장으로 만들거나, 뉴스와 무관한 건강 상식으로 분량을 채우지 마세요.
- 첫 문단 아래에 반드시 아래의 실제 뉴스 제목과 링크를 모두 출처 목록으로 넣으세요. 각 링크는 원문 그대로 사용해야 합니다.
- 출처 블록 외 본문에서는 긴 원문 제목을 그대로 반복하지 말고 자연스러운 문장으로 풀어 쓰세요.
- 시작 문단은 메타 디스크립션을 다시 쓰지 말고, 오늘 묶은 뉴스가 말하는 변화나 핵심 흐름을 바로 설명하세요.
- 문체는 차분하고 쉬운 존댓말로, "~에 따르면", "독자들은 궁금해합니다" 같은 어색한 문구를 반복하지 마세요.
- 마지막에는 <h2>최종 정리하면,</h2>을 넣고 4~6개 bullet로 핵심을 정리하세요.
- 마지막 출처에는 선택한 뉴스 링크들을 다시 짧게 나열하세요.
- HTML에는 <p>, <h2>, <h3>, <ul>, <li>, <strong>, <blockquote>, <a>만 사용하세요.
- 본문은 4500~7000자 정도로 작성하세요. 확인되지 않은 효과·치료 가능성·수치·일정을 지어내지 마세요.
{variant_rules}

반환 형식은 JSON 하나만 사용하세요.
{{
  "title": "새로 만든 뉴스 브리핑 제목",
  "meta": "선택 뉴스의 핵심 주제를 담은 120~160자 메타 설명 | tags: 태그1, 태그2, 태그3",
  "html": "<p>...</p><blockquote>...</blockquote><h2>...</h2><p>...</p>"
}}
"""


def process_health_news_bundle(
    health_items: list[WritingQueueItem],
    client: OpenAI,
    model: str,
) -> None:
    """Turn selected health-news rows into one source-linked briefing article."""
    if not health_items:
        return

    anchor = health_items[0]
    entries = health_news_bundle_entries(health_items)
    if not entries:
        logging.warning("신장정신 뉴스 묶음에 실제 출처 링크가 없어 글쓰기를 보류합니다.")
        return

    logging.info("신장정신 뉴스 묶음 글 생성 시작: 선택 뉴스 %s건", len(entries))
    try:
        source_notes = fetch_source_notes(entries, limit=min(len(entries), 10))
        article = generate_news_bundle_article(
            client=client,
            model=model,
            system_prompt=system_prompt_for_category("신장정신"),
            user_prompt=build_health_news_bundle_prompt(entries, source_notes),
        )
        article = GeneratedArticle(
            title=article.title,
            meta=article.meta,
            html=ensure_health_news_bundle_sources(article.html, entries),
        )
        validate_readability_style(article)

        tistory_article = None
        try:
            tistory_article = generate_news_bundle_article(
                client=client,
                model=model,
                system_prompt=system_prompt_for_category("신장정신"),
                user_prompt=build_health_news_bundle_prompt(entries, source_notes, tistory_variant=True, wordpress_article=article),
            )
            tistory_article = GeneratedArticle(
                title=tistory_article.title,
                meta=tistory_article.meta,
                html=ensure_health_news_bundle_sources(tistory_article.html, entries),
            )
            validate_readability_style(tistory_article)
            validate_tistory_variant(article, tistory_article)
        except Exception as exc:
            logging.warning("신장정신 티스토리 전용 글 생성 실패, 워드프레스 글만 저장합니다: %s", exc)

        source_links = "\n".join(entry["link"] for entry in entries)
        update_today_article(anchor, article, source_links, tistory_article)
        for item in health_items[1:]:
            item.worksheet.update_cell(item.row_number, item.status_col, "뉴스묶음처리")
        logging.info("신장정신 뉴스 묶음 글 생성 완료: %s건 -> 오늘작성 %s행", len(entries), anchor.row_number)
    except Exception as exc:
        logging.exception("신장정신 뉴스 묶음 글 생성 실패: %s", exc)


def build_policy_news_bundle_prompt(
    entries: list[dict[str, str]],
    source_notes: str,
    tistory_variant: bool = False,
    wordpress_article: GeneratedArticle | None = None,
) -> str:
    source_list = "\n".join(
        f"- 뉴스 제목: {entry['title']}\n  원문 링크: {entry['link']}" for entry in entries
    )
    variant_rule = ""
    if tistory_variant and wordpress_article:
        variant_rule = f"""
티스토리용 별도 글입니다. 워드프레스 제목 "{wordpress_article.title}"과 같은 제목·메타·도입 문장을 쓰지 말고,
생활 정보 독자가 빠르게 읽을 수 있게 다른 순서와 다른 표현으로 작성하세요.
"""
    return f"""
당신은 대한민국 정책·공공소식 편집자입니다. 아래는 사용자가 직접 선택한 실제 정책 및 지원 뉴스입니다.
실제 검색 롱테일이 충분하지 않아, 이 글은 롱테일 템플릿이 아닌 **정책 뉴스 브리핑**으로 작성합니다.

[선택 뉴스]
{source_list}

[직접 확인한 원문 요약]
{source_notes or '원문 제목과 링크에서 확인되는 범위만 사용하세요.'}

필수 규칙:
- 뉴스 제목을 블로그 제목으로 그대로 복사하지 마세요. 공통 핵심 주제와 실제 변화 내용을 이용해 새 제목을 만드세요.
- 각 뉴스에서 발표된 내용, 해당되는 사람·지역·기간·지원 내용이 원문에 있을 때만 쉽게 풀어 설명하세요.
- 뉴스에 없는 신청조건, 금액, 접수일, 필요서류를 추측해 만들지 마세요.
- 첫 문단 아래에 선택한 실제 뉴스 제목과 원문 링크를 모두 출처 목록으로 넣으세요.
- 본문은 정책·지원 뉴스의 내용 자체를 설명합니다. 무관한 일반 신청 가이드로 분량을 채우지 마세요.
- 제목·H2·본문에 '왜 화제', '핵심만 보기' 같은 빈 템플릿 표현을 쓰지 마세요.
- 마지막에는 <h2>최종 정리하면,</h2>과 4~6개 bullet, 짧은 출처 목록을 넣으세요.
- HTML은 <p>, <h2>, <h3>, <ul>, <li>, <strong>, <blockquote>, <a>, <table>만 사용하세요.
{variant_rule}

JSON 하나만 반환하세요.
{{"title":"새 정책 뉴스 제목","meta":"120~160자 메타 설명 | tags: 태그1, 태그2, 태그3","html":"<p>...</p><h2>...</h2>"}}
"""


def process_policy_news_bundle(
    policy_items: list[WritingQueueItem],
    client: OpenAI,
    model: str,
) -> None:
    """Bundle selected policy news only when real longtails were unavailable."""
    if not policy_items:
        return
    anchor = policy_items[0]
    entries = health_news_bundle_entries(policy_items)
    if not entries:
        return
    logging.info("정책지원 뉴스 묶음 글 생성 시작: 선택 뉴스 %s건", len(entries))
    try:
        source_notes = fetch_source_notes(entries, limit=min(len(entries), 10))
        article = generate_news_bundle_article(
            client=client,
            model=model,
            system_prompt=system_prompt_for_category("정책지원"),
            user_prompt=build_policy_news_bundle_prompt(entries, source_notes),
        )
        article = GeneratedArticle(article.title, article.meta, ensure_health_news_bundle_sources(article.html, entries))
        validate_readability_style(article)

        tistory_article = None
        try:
            tistory_article = generate_news_bundle_article(
                client=client,
                model=model,
                system_prompt=system_prompt_for_category("정책지원"),
                user_prompt=build_policy_news_bundle_prompt(entries, source_notes, tistory_variant=True, wordpress_article=article),
            )
            tistory_article = GeneratedArticle(
                tistory_article.title,
                tistory_article.meta,
                ensure_health_news_bundle_sources(tistory_article.html, entries),
            )
            validate_tistory_variant(article, tistory_article)
        except Exception as exc:
            logging.warning("정책지원 티스토리 전용 뉴스 글 생성 실패, 워드프레스 글만 저장합니다: %s", exc)

        update_today_article(anchor, article, "\n".join(entry["link"] for entry in entries), tistory_article)
        for item in policy_items[1:]:
            item.worksheet.update_cell(item.row_number, item.status_col, "뉴스묶음처리")
        logging.info("정책지원 뉴스 묶음 글 생성 완료: %s건 -> 오늘작성 %s행", len(entries), anchor.row_number)
    except Exception as exc:
        logging.exception("정책지원 뉴스 묶음 글 생성 실패: %s", exc)


LOAN_PRODUCT_LABELS = [
    "햇살론15", "햇살론유스", "햇살론뱅크", "새희망홀씨", "사잇돌대출",
    "버팀목 전세대출", "디딤돌대출", "특례보금자리론", "보금자리론",
    "전세자금대출", "주택담보대출", "대환대출", "소상공인 대출",
]


def loan_bundle_key(item: WritingQueueItem) -> str:
    text = normalize_cell(item.keyword).replace(" ", "")
    for label in LOAN_PRODUCT_LABELS:
        if label.replace(" ", "") in text:
            return label
    # Unrelated loans must never be grouped merely because both contain '대출'.
    return f"single:{item.row_number}"


def collect_loan_bundles(items: list[WritingQueueItem]) -> list[list[WritingQueueItem]]:
    grouped: dict[str, list[WritingQueueItem]] = {}
    for item in items:
        grouped.setdefault(loan_bundle_key(item), []).append(item)
    bundles: list[list[WritingQueueItem]] = []
    for key, group in grouped.items():
        if key.startswith("single:"):
            continue
        # Three related selected keywords make one article. Remaining one or two
        # stay in the queue for a later matching selection.
        for index in range(0, len(group) - 2, 3):
            bundles.append(group[index:index + 3])
    return bundles


def build_loan_bundle_prompt(items: list[WritingQueueItem], entries: list[dict[str, str]], source_notes: str) -> str:
    longtails = "\n".join(
        f"- {item.keyword}: {item.tistory_longtails or item.wordpress_longtails}" for item in items
    )
    source_list = "\n".join(f"- {entry['title']} | {entry['link']}" for entry in entries)
    product = loan_bundle_key(items[0])
    return f"""
당신은 20대 이상을 위한 생활금융 콘텐츠 편집자입니다.
아래 세 키워드는 모두 "{product}"과 관련된 실제 검색 주제입니다. 세 항목을 하나의 정확한 금융 정보 글로 묶으세요.

[세 개의 주제와 실제 롱테일]
{longtails}

[원문/대표 출처]
{source_list}

[직접 확인한 내용]
{source_notes or '확인 가능한 출처 범위만 사용하세요.'}

규칙:
- 제목은 "{product}"을 중심으로 세 주제가 왜 함께 중요한지 알 수 있게 새로 만드세요.
- 각 키워드의 실제 롱테일을 각각 H2 또는 H3의 내용으로 반영하세요. 세 키워드를 하나의 일반 대출 글로 뭉개지 마세요.
- 한도·금리·조건·상환·후기 같은 내용은 출처에서 확인될 때만 쓰고, 확인되지 않은 숫자나 승인 가능성을 만들지 마세요.
- 첫 문단 아래에 출처 링크를 짧게 넣고, 본문 중간에는 금융감독원·서민금융진흥원 등 확인처를 안내하세요.
- 티스토리 독자가 읽기 쉽게 친절한 존댓말과 짧은 문단을 사용하세요.
- 마지막에는 <h2>최종 정리하면,</h2>을 넣으세요.
- HTML은 <p>, <h2>, <h3>, <ul>, <li>, <strong>, <blockquote>, <a>, <table>만 사용하세요.

JSON 하나만 반환하세요.
{{"title":"SEO 제목","meta":"120~160자 메타 설명 | tags: 태그1, 태그2, 태그3","html":"<p>...</p><h2>...</h2>"}}
"""


def process_loan_bundle(items: list[WritingQueueItem], client: OpenAI, model: str) -> None:
    """Create one Tistory loan article from three related selected keywords."""
    if len(items) != 3:
        return
    entries = health_news_bundle_entries(items)
    anchor = items[0]
    try:
        source_notes = fetch_source_notes(entries, limit=min(len(entries), 3)) if entries else ""
        raw_text = call_openai_with_retry(
            client=client,
            model=model,
            system_prompt=system_prompt_for_category("대출관련"),
            user_prompt=build_loan_bundle_prompt(items, entries, source_notes),
        )
        article = parse_generated_article(raw_text, ensure_meta_intro=False)
        if entries:
            article = GeneratedArticle(article.title, article.meta, ensure_health_news_bundle_sources(article.html, entries))
        validate_readability_style(article)
        # 대출은 finwiz 티스토리 단독 발행 대상이다. API 비용을 아끼기 위해
        # 이 결과를 티스토리 전용 글로도 그대로 저장한다.
        update_today_article(anchor, article, "\n".join(entry["link"] for entry in entries), article)
        for item in items[1:]:
            item.worksheet.update_cell(item.row_number, item.status_col, "대출묶음처리")
        logging.info("대출 3키워드 묶음 글 생성 완료: %s", loan_bundle_key(anchor))
    except Exception as exc:
        logging.exception("대출 3키워드 묶음 글 생성 실패: %s", exc)


def 카테고리제한(기본: set[str] | None, 고른것: set[str] | None) -> set[str] | None:
    """--category 로 고른 것과 원래 조건을 겹친다.

    --category 를 안 쓰면(고른것이 None) 지금까지와 똑같이 돈다.
    """
    if 고른것 is None:
        return 기본
    if 기본 is None:
        return 고른것
    return 기본 & 고른것


def run_pipeline(
    credentials_path: str,
    spreadsheet_id: str,
    openai_key: str,
    model: str,
    limit: int,
    only_categories: set[str] | None = None,
) -> None:
    sheet_client = gspread.service_account(filename=credentials_path)
    spreadsheet = sheet_client.open_by_key(spreadsheet_id)
    today_sheet = ensure_today_sheet(spreadsheet)
    openai_client = OpenAI(api_key=openai_key)
    today_values = read_today_values_with_retry(today_sheet)

    health_bundle_limit = int(os.getenv("MAX_HEALTH_NEWS_PER_BRIEF", "30"))
    health_items = collect_writing_queue(
        today_sheet,
        health_bundle_limit,
        include_categories=카테고리제한({"신장정신"}, only_categories),
        values=today_values,
    )
    policy_candidates = collect_writing_queue(
        today_sheet,
        30,
        include_categories=카테고리제한({"정책지원"}, only_categories),
        values=today_values,
    )
    # 정책지원은 실제 검색 롱테일이 세 개 이상일 때만 롱테일 글로 작성한다.
    # 검색 결과가 부족하면 키워드 파이프라인이 같은 주제의 뉴스 제목을
    # 롱테일 칸에 넣는다. 그 경우도 한 편의 출처 기반 브리핑으로 전환한다.
    policy_bundle_items = [
        item for item in policy_candidates
        if len(split_longtail_keywords(item.wordpress_longtails, 3)) < 3
        or uses_source_news_longtails(item, policy_candidates)
    ]
    loan_candidates = collect_writing_queue(
        today_sheet,
        60,
        include_categories=카테고리제한({"대출관련", "대출"}, only_categories),
        values=today_values,
    )
    loan_bundles = collect_loan_bundles(loan_candidates)
    bundled_rows = {
        (item.category, item.row_number)
        for item in [*policy_bundle_items, *(member for bundle in loan_bundles for member in bundle)]
    }
    queue_items = collect_writing_queue(
        today_sheet,
        limit,
        include_categories=only_categories,
        exclude_categories=None if only_categories else {"신장정신"},
        values=today_values,
    )
    queue_items = [
        item for item in queue_items
        if (item.category, item.row_number) not in bundled_rows
    ]
    logging.info(
        "오늘작성 글쓰기대기 수: 일반 %s건, 신장정신 뉴스 묶음 %s건, 정책 뉴스 묶음 %s건, 대출 3키워드 묶음 %s건",
        len(queue_items),
        len(health_items),
        len(policy_bundle_items),
        len(loan_bundles),
    )

    # Keep the visible 오늘작성 queue intuitive: ordinary rows at the top are
    # completed before the later source-news bundles are collapsed into briefs.
    for item in queue_items:
        logging.info("글 생성 시작: [%s] %s", item.category, item.keyword)
        try:
            if len(split_longtail_keywords(item.wordpress_longtails, 3)) < 3:
                logging.warning("롱테일 키워드가 부족해 글쓰기를 건너뜁니다: %s", item.keyword)
                mark_longtail_required(item)
                continue

            research = build_research_context(item.keyword, item.category, item.source_link)
            content_intent = classify_content_intent(
                item.keyword,
                item.category,
                item.wordpress_longtails,
                research.snippets,
            )
            # ── 두 단계 글쓰기 (ARTICLE_V2_CATEGORIES 에 적은 카테고리만) ──
            # 답할 수 있는 질문을 먼저 뽑고, 그 질문에 답하는 글을 쓴다.
            # 롱테일을 H2 에 밀어 넣는 검증은 여기서 건너뛴다. 그 검증이
            # 목차를 '독자 질문' 이 아니라 '키워드 넣을 자리' 로 만들기 때문이다.
            v2쓰기 = article_v2 is not None and article_v2.enabled_for(item.category)
            if v2쓰기:
                def _부르기(system_prompt: str, user_prompt: str) -> str:
                    return call_openai_with_retry(
                        client=openai_client,
                        model=model,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                    )

                재료 = "\n\n".join(
                    부분 for 부분 in [research.snippets, research.source_notes] if 부분
                )
                article, 잰것 = article_v2.generate(
                    call_openai=_부르기,
                    parse_article=lambda raw: parse_generated_article(raw, item.keyword),
                    keyword=item.keyword,
                    category=item.category,
                    longtails=item.wordpress_longtails,
                    material=재료,
                )
                logging.info(
                    "[v2] 완성: %s자 · 모호밀도 %s · 숫자밀도 %s · 갈래 %s",
                    잰것["글자수"], 잰것["모호밀도"], 잰것["숫자밀도"], 잰것["mode"])
                raw_text = ""
            else:
                raw_text = call_openai_with_retry(
                    client=openai_client,
                    model=model,
                    system_prompt=system_prompt_for_category(item.category),
                    user_prompt=build_user_prompt(
                        item.keyword,
                        item.category,
                        item.source_link,
                        research,
                        item.wordpress_longtails,
                    ),
                )
            try:
                if not v2쓰기:
                    article = parse_generated_article(raw_text, item.keyword)
                article = normalize_health_news_title(article, item.keyword, item.category, item.source_link)
                validate_article_intent(article, content_intent)
                if not v2쓰기:
                    validate_longtail_usage(article, item.wordpress_longtails)
                    validate_template_matches_longtails(article, item.wordpress_longtails, content_intent)
                validate_recent_issue_focus(article, item.category, content_intent)
                validate_readability_style(article)
                validate_controversy_depth(article, item.keyword, content_intent)
                validate_health_news_title(article, item.keyword, item.category, item.source_link)
                validate_health_news_focus(article, item.keyword, item.category, item.source_link)
            except Exception:
                logging.warning("응답 파싱 실패, 짧은 본문으로 다시 생성합니다: %s", item.keyword)
                raw_text = call_openai_with_retry(
                    client=openai_client,
                    model=model,
                    system_prompt=system_prompt_for_category(item.category),
                    user_prompt=build_retry_user_prompt(
                        item.keyword,
                        item.category,
                        item.source_link,
                        research,
                        item.wordpress_longtails,
                    ),
                )
                article = parse_generated_article(raw_text, item.keyword)
                article = normalize_health_news_title(article, item.keyword, item.category, item.source_link)
                validate_article_intent(article, content_intent)
                validate_longtail_usage(article, item.wordpress_longtails)
                validate_template_matches_longtails(article, item.wordpress_longtails, content_intent)
                validate_recent_issue_focus(article, item.category, content_intent)
                validate_readability_style(article)
                validate_controversy_depth(article, item.keyword, content_intent)
                validate_health_news_title(article, item.keyword, item.category, item.source_link)
                validate_health_news_focus(article, item.keyword, item.category, item.source_link)
            if is_saega2_health_news_item(item.category, item.source_link):
                article = GeneratedArticle(
                    title=article.title,
                    meta=article.meta,
                    html=ensure_health_news_reference(article.html, item.keyword, item.source_link),
                )
            tistory_article = None
            if should_generate_tistory_variant(item.category):
                tistory_longtails = item.tistory_longtails or item.wordpress_longtails
                if len(split_longtail_keywords(tistory_longtails, 3)) >= 3:
                    try:
                        logging.info("티스토리 전용 글 생성 시작: [%s] %s", item.category, item.keyword)
                        raw_tistory_text = call_openai_with_retry(
                            client=openai_client,
                            model=model,
                            system_prompt=system_prompt_for_category(item.category),
                            user_prompt=build_tistory_user_prompt(
                                item.keyword,
                                item.category,
                                item.source_link,
                                research,
                                tistory_longtails,
                                article,
                            ),
                        )
                        tistory_article = parse_generated_article(
                            raw_tistory_text,
                            item.keyword,
                            ensure_meta_intro=False,
                        )
                        tistory_article = normalize_health_news_title(
                            tistory_article,
                            item.keyword,
                            item.category,
                            item.source_link,
                            tistory_variant=True,
                        )
                        validate_longtail_usage(tistory_article, tistory_longtails)
                        validate_readability_style(tistory_article)
                        try:
                            validate_tistory_variant(article, tistory_article)
                        except ValueError:
                            logging.info("티스토리 글 유사도가 높아 별도 관점으로 한 번 더 작성합니다: %s", item.keyword)
                            raw_tistory_text = call_openai_with_retry(
                                client=openai_client,
                                model=model,
                                system_prompt=system_prompt_for_category(item.category),
                                user_prompt=build_tistory_user_prompt(
                                    item.keyword,
                                    item.category,
                                    item.source_link,
                                    research,
                                    tistory_longtails,
                                    article,
                            ) + "\n[재작성] 워드프레스 제목·메타·도입 문장과 겹치지 않는 완전히 다른 관점으로 다시 작성하세요.",
                            )
                            tistory_article = parse_generated_article(
                                raw_tistory_text,
                                item.keyword,
                                ensure_meta_intro=False,
                            )
                            tistory_article = normalize_health_news_title(
                                tistory_article,
                                item.keyword,
                                item.category,
                                item.source_link,
                                tistory_variant=True,
                            )
                            validate_longtail_usage(tistory_article, tistory_longtails)
                            validate_readability_style(tistory_article)
                            validate_tistory_variant(article, tistory_article)
                        logging.info("티스토리 전용 글 생성 완료: %s", item.keyword)
                    except Exception as exc:
                        logging.warning("티스토리 전용 글 생성 실패, 워드프레스 글만 저장합니다: %s / %s", item.keyword, exc)

            output_link = item.source_link if is_saega2_health_news_item(item.category, item.source_link) else research.link

            # 꿈해몽: 요청문에 넣은 지난 글 링크가 본문에 빠졌으면 직접 채운다.
            if dream_links is not None and item.category == "꿈해몽":
                try:
                    관련글 = dream_links.관련글고르기(
                        item.keyword, article.title, spreadsheet=spreadsheet)
                    새본문, 채운것 = dream_links.링크채우기(article.html, 관련글)
                    if 채운것:
                        logging.info(
                            "꿈해몽 지난 글 링크 %s개를 직접 넣었습니다: %s",
                            len(채운것), 채운것)
                        article = GeneratedArticle(article.title, article.meta, 새본문)
                except Exception as 오류:
                    logging.warning("꿈해몽 지난 글 링크를 넣지 못했습니다: %s", 오류)

            update_today_article(item, article, output_link, tistory_article)
            logging.info("글 생성 완료: %s", item.keyword)
        except Exception as exc:
            logging.exception("키워드 처리 실패, 다음 항목으로 넘어갑니다: %s / %s", item.keyword, exc)

    # Health and policy rows are selected news sources, not standalone longtail
    # topics. Process their source-based briefs after the earlier regular rows.
    process_policy_news_bundle(policy_bundle_items, openai_client, model)
    process_health_news_bundle(health_items, openai_client, model)
    for loan_bundle in loan_bundles:
        process_loan_bundle(loan_bundle, openai_client, model)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="오늘작성 시트의 글쓰기대기 키워드로 OpenAI SEO 글을 생성합니다.")
    parser.add_argument("--env", default=".env", help=".env 파일 경로")
    parser.add_argument("--credentials", default=None, help="Google service-account JSON 경로")
    parser.add_argument("--spreadsheet-id", default=None, help="Google Sheet ID")
    parser.add_argument("--model", default=None, help="OpenAI 모델명")
    parser.add_argument("--limit", type=int, default=None, help="한 번에 처리할 최대 행 수")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument(
        "--category",
        default="",
        help="이 카테고리만 씁니다. 쉼표로 여러 개. 비우면 지금처럼 전부 씁니다. (예: 꿈해몽)",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    load_dotenv(args.env)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    credentials_path = args.credentials or os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    spreadsheet_id = args.spreadsheet_id or os.getenv("SPREADSHEET_ID", SPREADSHEET_ID)
    openai_key = os.getenv("OPENAI_API_KEY")
    model = args.model or os.getenv("OPENAI_MODEL", DEFAULT_MODEL)
    limit = args.limit or int(os.getenv("MAX_APPROVED_ROWS", "3"))

    if not credentials_path:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_FILE 값이 없습니다. .env에 추가하세요.")
    if not openai_key:
        raise RuntimeError("OPENAI_API_KEY 값이 없습니다. .env에 추가하세요.")
    if "여기에" in openai_key or "your_" in openai_key:
        raise RuntimeError("OPENAI_API_KEY에 실제 OpenAI API 키를 입력해야 합니다.")

    고른것 = {부분.strip() for 부분 in (args.category or "").split(",") if 부분.strip()}
    if 고른것:
        logging.info("이 카테고리만 씁니다: %s", ", ".join(sorted(고른것)))

    run_pipeline(
        credentials_path,
        spreadsheet_id,
        openai_key,
        model,
        limit,
        only_categories=고른것 or None,
    )


if __name__ == "__main__":
    main()
