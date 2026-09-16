# -*- coding: utf-8 -*-
"""
모델에게 직접 검색을 시켜 '재료' 를 모은다.

왜 만들었나
    같은 프롬프트인데 손으로 쓴 글은 구체적이고 API 로 쓴 글은
    두루뭉술했다. 프롬프트 탓이 아니라 **재료 탓**이었다.

    손으로 쓸 때
        챗봇 화면에 프롬프트를 넣으면 챗봇이 스스로 검색을 한다.
        여러 번 검색하고, 페이지를 끝까지 읽고, 표 안의 숫자를 보고,
        모자라면 링크를 타고 더 들어간다. 그래서 '카카오뱅크 연 3.52%'
        같은 문장이 나온다.

    API 로 쓸 때
        client.responses.create() 에 검색 도구가 붙어 있지 않았다.
        모델은 눈을 감은 채로 글을 써야 했다. 재료라고 받은 것은
        파이썬이 긁어 온 웹페이지 앞머리 몇 줄뿐이었다.

    거기에 '지어내지 마라' 가 걸려 있으니 모델이 할 수 있는 말은
    "은행마다 다릅니다", "확인이 필요합니다" 밖에 없다.
    두루뭉술은 모델이 게을러서가 아니라, 모르는 것을 모른다고
    말한 결과다. 정직한 대답이었다.

무엇을 하나
    글을 쓰기 전에 검색 전담 호출을 한 번 넣는다. 이 호출에는
    검색 도구를 붙인다. 결과를 '사실카드' 모양으로 받아 온다.
    시트에서 손으로 채우던 그 표와 같은 모양이다.

        항목 | 값 | 기준일 | 출처이름 | URL

    이걸 재료로 넘기면 글 쓰는 호출은 눈을 뜨고 쓰게 된다.

켜고 끄기
    .env
        WEB_SEARCH_ENABLED=1          기본값. 0 이면 예전처럼 돈다.
        WEB_SEARCH_MODEL=gpt-5        비우면 글쓰기와 같은 모델
        WEB_SEARCH_CACHE_DIR=...      비우면 .캐시/웹검색
        WEB_SEARCH_CACHE_HOURS=12     같은 키워드 재실행 시 아껴 쓴다

돈 이야기
    글 한 편당 검색 호출이 한 번 늘어난다. 대신 다시 쓰기가 줄고,
    무엇보다 쓸 수 있는 글이 나온다. 캐시가 있어 같은 키워드를
    다시 돌려도 12시간 안에는 다시 검색하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

# 검색 도구 이름이 SDK 판에 따라 다르다. 앞의 것부터 시도한다.
검색도구후보 = ["web_search", "web_search_preview"]

_모델이검색못함 = {"값": False}   # 한 번 실패하면 그 실행 동안은 다시 시도하지 않는다


def enabled() -> bool:
    return os.getenv("WEB_SEARCH_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


def 캐시폴더() -> Path:
    값 = os.getenv("WEB_SEARCH_CACHE_DIR", "").strip()
    폴더 = Path(값) if 값 else Path(".캐시") / "웹검색"
    폴더.mkdir(parents=True, exist_ok=True)
    return 폴더


def 캐시시간() -> float:
    try:
        return max(0.0, float(os.getenv("WEB_SEARCH_CACHE_HOURS", "12")))
    except ValueError:
        return 12.0


def 캐시열쇠(keyword: str, category: str) -> str:
    씨앗 = f"{category}|{keyword}".strip().lower()
    return hashlib.sha1(씨앗.encode("utf-8")).hexdigest()[:20]


def 캐시읽기(keyword: str, category: str) -> str:
    시간 = 캐시시간()
    if 시간 <= 0:
        return ""
    길 = 캐시폴더() / f"{캐시열쇠(keyword, category)}.json"
    if not 길.exists():
        return ""
    try:
        담긴것 = json.loads(길.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if time.time() - float(담긴것.get("때", 0)) > 시간 * 3600:
        return ""
    logging.info("웹검색 재료를 캐시에서 씁니다: %s", keyword)
    return str(담긴것.get("재료", ""))


def 캐시쓰기(keyword: str, category: str, 재료: str) -> None:
    if 캐시시간() <= 0 or not 재료:
        return
    try:
        길 = 캐시폴더() / f"{캐시열쇠(keyword, category)}.json"
        길.write_text(
            json.dumps({"때": time.time(), "키워드": keyword, "재료": 재료},
                       ensure_ascii=False),
            encoding="utf-8")
    except Exception as 오류:
        logging.warning("웹검색 캐시를 쓰지 못했습니다: %s", 오류)


검색시스템 = """
당신은 한국어 자료 조사원입니다. 글을 쓰는 사람이 아닙니다.

검색으로 확인한 것만 적습니다. 기억이나 짐작으로 채우지 않습니다.
확인하지 못한 항목은 비워 두지 말고 [확인 안 됨] 에 적습니다.
모르는 것을 모른다고 적는 것이 이 일에서 가장 중요합니다.
""".strip()


def 검색요청문(keyword: str, category: str, longtails: str) -> str:
    return f"""
주제: {keyword}
분야: {category}
관련 검색어: {longtails or "(없음)"}

이 주제로 블로그 글을 쓸 사람에게 넘길 **사실 자료**를 모아 주세요.
글을 쓰지 마세요. 자료만 모읍니다.

[꼭 찾아야 하는 것]
읽는 사람이 "그래서 어디가, 얼마인데?" 하고 물을 것들입니다.
- 기관·은행·상품의 **실제 이름**  (예: 카카오뱅크, 국민행복기금)
- **숫자**  금리·한도·금액·기간·나이·비율·순위
- **날짜**  그 숫자가 언제 기준인지
- **어디서 확인하는지**  기관 이름 + 메뉴 이름 + 주소

비교 주제(가장 싼 곳, 조건이 좋은 곳)라면 **이름을 최소 3개** 찾으세요.
이름 없는 비교는 자료가 아닙니다.

[검색 요령]
- 한 번만 검색하고 끝내지 마세요. 항목마다 따로 검색하세요.
- 공식 기관(.go.kr, .or.kr), 은행연합회, 언론사 원문을 먼저 보세요.
- 값이 적힌 표가 있으면 표 안의 숫자를 그대로 가져오세요.
- 오래된 숫자는 기준일을 꼭 같이 적으세요.

[출력 형식 — 이 모양 그대로]

[확인된 사실]
항목 | 값 | 기준일 | 출처이름 | URL
(한 줄에 하나씩. 최소 5줄, 많을수록 좋습니다.)

[배경]
- 글의 맥락에 필요한 설명을 짧은 문장으로. 각 줄 끝에 출처이름.

[확인 안 됨]
- 찾아봤지만 확인하지 못한 것. 왜 못 찾았는지 한 줄.

[확인하는 방법]
- 독자가 자기 경우를 직접 확인할 수 있는 길.
  기관 이름 > 메뉴 이름 > 무엇을 보면 되는지. 전화번호가 있으면 함께.

숫자를 지어내지 마세요. 확인 못 한 것은 [확인 안 됨] 으로 보내세요.
[확인 안 됨] 이 길어도 괜찮습니다. 틀린 숫자보다 낫습니다.
""".strip()


def _한번부르기(client: Any, model: str, 도구이름: str, 요청문: str) -> str:
    응답 = client.responses.create(
        model=model,
        tools=[{"type": 도구이름}],
        max_output_tokens=6000,
        input=[
            {"role": "system", "content": 검색시스템},
            {"role": "user", "content": 요청문},
        ],
    )
    return str(getattr(응답, "output_text", "") or "").strip()


def gather(client: Any, model: str, keyword: str, category: str, longtails: str = "") -> str:
    """검색으로 모은 재료. 못 모으면 빈 글자."""
    if not enabled() or _모델이검색못함["값"]:
        return ""

    담긴것 = 캐시읽기(keyword, category)
    if 담긴것:
        return 담긴것

    검색모델 = os.getenv("WEB_SEARCH_MODEL", "").strip() or model
    요청문 = 검색요청문(keyword, category, longtails)

    마지막오류: Exception | None = None
    for 도구이름 in 검색도구후보:
        try:
            재료 = _한번부르기(client, 검색모델, 도구이름, 요청문)
        except Exception as 오류:
            마지막오류 = 오류
            logging.info("검색 도구 '%s' 를 쓰지 못했습니다: %s", 도구이름, 오류)
            continue

        if not 재료:
            logging.warning("검색은 됐는데 자료가 비어 돌아왔습니다: %s", keyword)
            return ""

        확인수 = len(확인된줄(재료))
        logging.info("웹검색 재료를 모았습니다: %s · 확인된 사실 %s줄 · %s자",
                     keyword, 확인수, len(재료))
        if 확인수 == 0:
            logging.warning(
                "확인된 사실이 한 줄도 없습니다: %s\n"
                "  이 주제는 검색으로 숫자를 못 찾았다는 뜻입니다.\n"
                "  글이 '확인하는 방법' 쪽으로 쓰이게 됩니다.", keyword)
        캐시쓰기(keyword, category, 재료)
        return 재료

    _모델이검색못함["값"] = True
    logging.warning(
        "이 모델/SDK 로는 웹검색을 못 합니다. 재료 없이 예전 방식으로 씁니다: %s\n"
        "  openai 패키지를 올리거나(pip install -U openai) WEB_SEARCH_MODEL 을\n"
        "  검색을 지원하는 모델로 지정해 보세요. 마지막 오류: %s",
        keyword, 마지막오류)
    return ""


_확인줄 = re.compile(r"^\s*([^|\n]{1,60})\|([^|\n]{1,80})\|")


def 확인된줄(재료: str) -> list[str]:
    """[확인된 사실] 칸에서 실제로 값이 적힌 줄만."""
    글 = str(재료 or "")
    시작 = 글.find("[확인된 사실]")
    if 시작 < 0:
        return []
    끝 = len(글)
    for 표시 in ("[배경]", "[확인 안 됨]", "[확인하는 방법]"):
        자리 = 글.find(표시, 시작 + 1)
        if 자리 > 0:
            끝 = min(끝, 자리)
    줄들 = []
    for 줄 in 글[시작:끝].splitlines():
        if 줄.strip().startswith("항목") or "|" not in 줄:
            continue
        if _확인줄.match(줄):
            줄들.append(줄.strip())
    return 줄들


def 재료가얇은가(재료: str) -> bool:
    """확인된 사실이 3줄도 안 되면 얇다고 본다."""
    return len(확인된줄(재료)) < 3
