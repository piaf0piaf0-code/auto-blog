"""
Stage 5: Save generated posts as WordPress drafts.

This script does not publish posts publicly by default.
It reads rows from the "오늘작성" sheet where status is "발행대기",
creates WordPress draft posts, and then marks those rows as "임시저장완료".
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import logging
import os
import re
import tempfile
import textwrap
import warnings
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlparse, urlunparse

import gspread

# 워드프레스 카테고리를 글 주제에 맞게 고르는 도우미.
# 파일이 없어도 나머지는 그대로 돈다.
try:
    import wp_category
except Exception as _카테고리오류:  # pragma: no cover
    wp_category = None
import requests
from requests.exceptions import SSLError
from urllib3.exceptions import InsecureRequestWarning
from dotenv import load_dotenv
from gspread.exceptions import WorksheetNotFound


SPREADSHEET_ID = "15bzAktttxB3aQNwzWnAvWhtIDiyL1NhIKlZpASUrnT8"
TODAY_SHEET = "오늘작성"
READY_STATUS = "발행대기"
DONE_STATUS = "임시저장완료"
PARTIAL_PUBLISHED_STATUS = "부분발행완료"
PUBLISHED_STATUS = "발행완료"
DEFAULT_POST_STATUS = "draft"
DEFAULT_ADS_CONFIG_FILE = "ads_config.json"
DEFAULT_PUBLISH_TARGETS_FILE = "publish_targets.json"
DEFAULT_THUMBNAIL_DIR = "auto_blog_thumbnails"
BASE_DIR = Path(__file__).resolve().parent
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

# 글쓰기 도우미(Apps Script)가 채우는 칸. 원본 메인 키워드가 뉴스 제목처럼
# 긴 경우가 많아, 검색엔진에 넘길 짧은 키워드를 따로 둔다.
FOCUS_KEYWORD_HEADERS = ["포커스키워드", "포커스 키워드", "포커스"]

TISTORY_TITLE_HEADER = "티스토리 SEO 제목"
TISTORY_META_HEADER = "티스토리 메타 디스크립션 및 태그"
TISTORY_HTML_HEADER = "티스토리 HTML 본문"

COMPLETION_SHEET_ALIASES = {
    "최신이슈": "1.완료이슈",
    "정책지원": "2.완료정책",
    "대출관련": "3.완료대출",
    "대출": "3.완료대출",
    "신장정신": "4.완료신장정신",
    "웰빙": "5.완료웰빙",
    "꿈해몽": "6.완료꿈",
}

COMPLETION_HEADERS = [
    "키워드",
    "카테고리",
    "제목",
    "워드프레스 초안 URL",
    "임시저장 일시",
    "상태",
    "플랫폼",
    "포스트 ID",
]


@dataclass(frozen=True)
class DraftItem:
    row_number: int
    keyword: str
    category: str
    title: str
    meta: str
    html: str
    status_col: int
    result_link: str = ""
    tistory_title: str = ""
    tistory_meta: str = ""
    tistory_html: str = ""
    focus_keyword: str = ""

    @property
    def seo_keyword(self) -> str:
        """검색엔진에 넘길 키워드. 없으면 원본 메인 키워드로 돌아간다."""
        return self.focus_keyword or self.keyword


@dataclass(frozen=True)
class DraftResult:
    post_id: str
    link: str
    status: str
    platform: str = "WordPress"


@dataclass(frozen=True)
class PublishTarget:
    category: str
    platform: str
    url: str
    username: str = ""
    app_password: str = ""
    category_id: str | None = None
    ads_config_path: str = DEFAULT_ADS_CONFIG_FILE
    tistory_blog_name: str = ""
    tistory_access_token: str = ""


def normalize_cell(value: Any) -> str:
    return str(value or "").strip()


def env_or_value(config: dict[str, Any], key: str, env_key: str, fallback: str = "") -> str:
    value = normalize_cell(config.get(key, ""))
    env_name = normalize_cell(config.get(env_key, ""))
    if env_name:
        env_value = normalize_cell(os.getenv(env_name, ""))
        if env_value and "여기에" not in env_value:
            return env_value
        return fallback
    return value or fallback


def load_publish_target_config(path: str) -> dict[str, Any]:
    target_path = Path(path)
    if not target_path.is_absolute():
        target_path = BASE_DIR / target_path
    if not target_path.exists():
        return {"categories": {}}
    return json.loads(target_path.read_text(encoding="utf-8"))


def publish_target_from_config(
    category: str,
    target_config: dict[str, Any],
    default_wp_url: str,
    default_username: str,
    default_app_password: str,
    default_category_id: str | None,
    default_ads_config_path: str,
    use_default_credentials: bool,
) -> PublishTarget:
    platform = normalize_cell(target_config.get("platform", "wordpress")).lower()
    url = normalize_cell(target_config.get("url", "")) or default_wp_url
    credential_fallback = default_username if use_default_credentials else ""
    password_fallback = default_app_password if use_default_credentials else ""
    username = env_or_value(target_config, "username", "username_env", credential_fallback)
    app_password = env_or_value(target_config, "app_password", "app_password_env", password_fallback)
    category_id = normalize_cell(target_config.get("category_id", "")) or default_category_id
    ads_config_path = normalize_cell(target_config.get("ads_config_file", "")) or default_ads_config_path
    tistory_blog_name = normalize_cell(target_config.get("blog_name", ""))
    tistory_access_token = env_or_value(target_config, "access_token", "access_token_env", "")

    return PublishTarget(
        category=category,
        platform=platform,
        url=url,
        username=username,
        app_password=app_password,
        category_id=category_id,
        ads_config_path=ads_config_path,
        tistory_blog_name=tistory_blog_name,
        tistory_access_token=tistory_access_token,
    )


def resolve_publish_target(
    category: str,
    config: dict[str, Any],
    default_wp_url: str,
    default_username: str,
    default_app_password: str,
    default_category_id: str | None,
    default_ads_config_path: str,
) -> PublishTarget:
    categories = config.get("categories", {})
    target_config = categories.get(category, {})
    return publish_target_from_config(
        category,
        target_config,
        default_wp_url,
        default_username,
        default_app_password,
        default_category_id,
        default_ads_config_path,
        use_default_credentials=not bool(target_config),
    )


def resolve_publish_targets(
    category: str,
    config: dict[str, Any],
    default_wp_url: str,
    default_username: str,
    default_app_password: str,
    default_category_id: str | None,
    default_ads_config_path: str,
) -> list[PublishTarget]:
    targets = [
        resolve_publish_target(
            category,
            config,
            default_wp_url,
            default_username,
            default_app_password,
            default_category_id,
            default_ads_config_path,
        )
    ]
    for extra_config in config.get("extra_categories", {}).get(category, []):
        if isinstance(extra_config, dict):
            targets.append(
                publish_target_from_config(
                    category,
                    extra_config,
                    default_wp_url,
                    default_username,
                    default_app_password,
                    default_category_id,
                    default_ads_config_path,
                    use_default_credentials=False,
                )
            )
    return targets


def header_index(headers: list[str], candidates: list[str], fallback: int) -> int:
    for candidate in candidates:
        if candidate in headers:
            return headers.index(candidate) + 1
    return fallback


def optional_header_index(headers: list[str], candidates: list[str]) -> int:
    """있으면 열 번호, 없으면 0. header_index 와 달리 엉뚱한 열로 넘어가지 않는다."""
    for candidate in candidates:
        if candidate in headers:
            return headers.index(candidate) + 1
    return 0


def read_optional_cell(row: list[str], col: int) -> str:
    if not col or col > len(row):
        return ""
    return normalize_cell(row[col - 1])


def collect_draft_items(today_sheet, limit: int) -> list[DraftItem]:
    values = today_sheet.get_all_values()
    if len(values) < 2:
        return []

    headers = values[0]
    if headers[: len(TODAY_HEADERS)] != TODAY_HEADERS:
        today_sheet.update(
            range_name="A1:H1",
            values=[TODAY_HEADERS],
        )
        headers = today_sheet.row_values(1)

    keyword_col = header_index(headers, ["원본 메인 키워드", "메인 키워드"], 1)
    category_col = header_index(headers, ["카테고리"], 2)
    title_col = header_index(headers, ["SEO 최적화 제목", "SEO 제목"], 4)
    meta_col = header_index(headers, ["메타 디스크립션 및 태그", "메타"], 5)
    html_col = header_index(headers, ["HTML 본문 내용", "HTML 본문"], 6)
    status_col = header_index(headers, ["작업 상태", "상태"], 7)
    link_col = header_index(headers, ["대표 링크/결과 URL", "결과 URL", "URL"], 8)
    tistory_title_col = header_index(headers, [TISTORY_TITLE_HEADER], 10)
    tistory_meta_col = header_index(headers, [TISTORY_META_HEADER], 11)
    tistory_html_col = header_index(headers, [TISTORY_HTML_HEADER], 12)
    focus_col = optional_header_index(headers, FOCUS_KEYWORD_HEADERS)

    items: list[DraftItem] = []
    for row_number, row in enumerate(values[1:], start=2):
        keyword = normalize_cell(row[keyword_col - 1] if keyword_col <= len(row) else "")
        category = normalize_cell(row[category_col - 1] if category_col <= len(row) else "")
        title = normalize_cell(row[title_col - 1] if title_col <= len(row) else "")
        meta = normalize_cell(row[meta_col - 1] if meta_col <= len(row) else "")
        html = normalize_cell(row[html_col - 1] if html_col <= len(row) else "")
        status = normalize_cell(row[status_col - 1] if status_col <= len(row) else "")
        result_link = normalize_cell(row[link_col - 1] if link_col <= len(row) else "")
        tistory_title = normalize_cell(row[tistory_title_col - 1] if tistory_title_col <= len(row) else "")
        tistory_meta = normalize_cell(row[tistory_meta_col - 1] if tistory_meta_col <= len(row) else "")
        tistory_html = normalize_cell(row[tistory_html_col - 1] if tistory_html_col <= len(row) else "")
        focus_keyword = read_optional_cell(row, focus_col)

        if status != READY_STATUS:
            continue
        if not keyword or not category or not title or not html:
            continue

        items.append(
            DraftItem(
                row_number,
                keyword,
                category,
                title,
                meta,
                html,
                status_col,
                result_link,
                tistory_title,
                tistory_meta,
                tistory_html,
                focus_keyword,
            )
        )
        if len(items) >= limit:
            break

    return items


def collect_saved_draft_items(today_sheet, limit: int) -> list[tuple[DraftItem, str]]:
    values = today_sheet.get_all_values()
    if len(values) < 2:
        return []

    headers = values[0]
    keyword_col = header_index(headers, ["원본 메인 키워드", "메인 키워드"], 1)
    category_col = header_index(headers, ["카테고리"], 2)
    title_col = header_index(headers, ["SEO 최적화 제목", "SEO 제목"], 4)
    meta_col = header_index(headers, ["메타 디스크립션 및 태그", "메타"], 5)
    html_col = header_index(headers, ["HTML 본문 내용", "HTML 본문"], 6)
    status_col = header_index(headers, ["작업 상태", "상태"], 7)
    link_col = header_index(headers, ["대표 링크/결과 URL", "결과 URL", "URL"], status_col + 1)
    tistory_title_col = header_index(headers, [TISTORY_TITLE_HEADER], 10)
    tistory_meta_col = header_index(headers, [TISTORY_META_HEADER], 11)
    tistory_html_col = header_index(headers, [TISTORY_HTML_HEADER], 12)
    focus_col = optional_header_index(headers, FOCUS_KEYWORD_HEADERS)

    items: list[tuple[DraftItem, str]] = []
    for row_number, row in enumerate(values[1:], start=2):
        keyword = normalize_cell(row[keyword_col - 1] if keyword_col <= len(row) else "")
        category = normalize_cell(row[category_col - 1] if category_col <= len(row) else "")
        title = normalize_cell(row[title_col - 1] if title_col <= len(row) else "")
        meta = normalize_cell(row[meta_col - 1] if meta_col <= len(row) else "")
        html = normalize_cell(row[html_col - 1] if html_col <= len(row) else "")
        status = normalize_cell(row[status_col - 1] if status_col <= len(row) else "")
        link = normalize_cell(row[link_col - 1] if link_col <= len(row) else "")
        tistory_title = normalize_cell(row[tistory_title_col - 1] if tistory_title_col <= len(row) else "")
        tistory_meta = normalize_cell(row[tistory_meta_col - 1] if tistory_meta_col <= len(row) else "")
        tistory_html = normalize_cell(row[tistory_html_col - 1] if tistory_html_col <= len(row) else "")
        focus_keyword = read_optional_cell(row, focus_col)

        if status not in {DONE_STATUS, PARTIAL_PUBLISHED_STATUS}:
            continue
        if not keyword or not category or not title or not html or not link:
            continue

        items.append(
            (
                DraftItem(
                    row_number,
                    keyword,
                    category,
                    title,
                    meta,
                    html,
                    status_col,
                    link,
                    tistory_title,
                    tistory_meta,
                    tistory_html,
                    focus_keyword,
                ),
                link,
            )
        )
        if len(items) >= limit:
            break

    return items


def wordpress_posts_endpoint(base_url: str) -> str:
    if not base_url.startswith(("http://", "https://")):
        base_url = "https://" + base_url
    return base_url.rstrip("/") + "/wp-json/wp/v2/posts"


def wordpress_tags_endpoint(base_url: str) -> str:
    if not base_url.startswith(("http://", "https://")):
        base_url = "https://" + base_url
    return base_url.rstrip("/") + "/wp-json/wp/v2/tags"


def wordpress_media_endpoint(base_url: str) -> str:
    if not base_url.startswith(("http://", "https://")):
        base_url = "https://" + base_url
    return base_url.rstrip("/") + "/wp-json/wp/v2/media"


def wordpress_users_me_endpoint(base_url: str) -> str:
    if not base_url.startswith(("http://", "https://")):
        base_url = "https://" + base_url
    return base_url.rstrip("/") + "/wp-json/wp/v2/users/me"


def wordpress_post_endpoint(base_url: str, post_id: str) -> str:
    return wordpress_posts_endpoint(base_url).rstrip("/") + f"/{post_id}"


def extract_post_id_from_link(link: str) -> str | None:
    query_post_id = parse_qs(urlparse(link).query).get("p", [None])[0]
    if query_post_id and str(query_post_id).isdigit():
        return str(query_post_id)

    match = re.search(r"(?:p=|/)(\d{2,})(?:[/#?]|$)", link)
    if match:
        return match.group(1)
    return None


def homepage_url(url: str) -> str:
    candidate = url if url.startswith(("http://", "https://")) else "https://" + url
    parsed = urlparse(candidate)
    if not parsed.netloc:
        return url
    return urlunparse((parsed.scheme or "https", parsed.netloc, "/", "", "", ""))


def normalize_external_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("//"):
        return "https:" + url
    if url.startswith(("http://", "https://", "mailto:", "tel:", "#")):
        return url
    parsed = urlparse(url)
    if not parsed.scheme and not parsed.netloc and "." in url.split("/")[0]:
        return "https://" + url
    return url


def fallback_search_link(url: str) -> str:
    parsed = urlparse(url if url.startswith(("http://", "https://")) else "https://" + url)
    query = parsed.netloc or url
    return f"https://www.google.com/search?q={quote_plus(query)}"


def request_url_once(url: str, timeout: int, verify: bool = True) -> requests.Response:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.6,en;q=0.5",
    }
    return requests.get(url, headers=headers, timeout=timeout, allow_redirects=True, verify=verify)


def response_looks_broken(response: requests.Response) -> bool:
    content_type = response.headers.get("Content-Type", "").lower()
    if "text/html" not in content_type and "application/xhtml" not in content_type:
        return False

    text = response.text[:8000].lower()
    broken_phrases = [
        "404 not found",
        "page not found",
        "페이지를 찾을 수",
        "존재하지 않는 페이지",
        "존재하지 않는 게시",
        "게시글이 존재하지",
        "삭제되었거나",
        "잘못된 접근",
        "잘못된 주소",
        "요청하신 페이지",
        "오류가 발생",
    ]
    return any(phrase in text for phrase in broken_phrases)


def checked_reachable_url(url: str, timeout: int | None = None) -> str:
    if not url.startswith(("http://", "https://")):
        return ""
    if timeout is None:
        try:
            timeout = int(os.getenv("LINK_CHECK_TIMEOUT", "8"))
        except ValueError:
            timeout = 8

    try:
        response = request_url_once(url, timeout, verify=True)
        return str(response.url) if 200 <= response.status_code < 400 and not response_looks_broken(response) else ""
    except SSLError:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", InsecureRequestWarning)
                response = request_url_once(url, timeout, verify=False)
            return str(response.url) if 200 <= response.status_code < 400 and not response_looks_broken(response) else ""
        except Exception:
            return ""
    except Exception:
        return ""


def link_is_reachable(url: str, timeout: int | None = None) -> bool:
    return bool(checked_reachable_url(url, timeout))


def safe_body_link(url: str, cache: dict[str, str]) -> str:
    url = normalize_external_url(url)
    if url in cache:
        return cache[url]
    if not url.startswith(("http://", "https://")):
        cache[url] = url
        return url

    checked = checked_reachable_url(url)
    if checked:
        cache[url] = checked
        return cache[url]

    home = homepage_url(url)
    if home != url:
        logging.warning("Broken body link replaced with homepage: %s -> %s", url, home)
    checked_home = checked_reachable_url(home)
    if checked_home:
        cache[url] = checked_home
        return cache[url]

    fallback = fallback_search_link(home)
    logging.warning("Homepage link also failed, using safe search link: %s -> %s", home, fallback)
    cache[url] = fallback
    return cache[url]


def sanitize_body_links(html: str) -> str:
    if os.getenv("LINK_CHECK_ENABLED", "true").lower() not in {"1", "true", "yes", "y"}:
        return html

    cache: dict[str, str] = {}

    def replace_href(match: re.Match[str]) -> str:
        quote = match.group(1)
        href = match.group(2)
        if not href or href in {"#", "##LINK_HERE##"}:
            return match.group(0)
        checked = safe_body_link(href, cache)
        return f"href={quote}{checked}{quote}"

    return re.sub(r"href=(['\"])(.*?)\1", replace_href, html, flags=re.IGNORECASE)


def parse_meta_description_and_tags(meta_text: str, keyword: str, category: str) -> tuple[str, list[str]]:
    meta_text = normalize_cell(meta_text)
    description = meta_text
    tags: list[str] = []

    tag_match = re.search(r"(?:tags?|태그)\s*[:：]\s*(.+)$", meta_text, flags=re.IGNORECASE)
    if tag_match:
        description = meta_text[: tag_match.start()].strip(" |,")
        raw_tags = tag_match.group(1)
        tags.extend(re.split(r"[,#|/·\n]+", raw_tags))

    compact_keyword = re.sub(r"\s+", "", keyword).lower()
    compact_description = re.sub(r"\s+", "", description).lower()
    if compact_keyword and compact_keyword not in compact_description:
        description = f"{keyword} 관련 핵심 정보를 정리했습니다. {description}".strip()

    tags.extend([keyword, category])
    clean_tags: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        cleaned = re.sub(r"\s+", " ", str(tag).strip(" #,|/·"))
        if not cleaned or len(cleaned) > 40:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        clean_tags.append(cleaned)
        if len(clean_tags) >= 12:
            break

    return description[:300], clean_tags


def category_key(value: str) -> str:
    return re.sub(r"[\s\d\.\-_/]+", "", normalize_cell(value))


def is_saega2_health_news_item(category: str, wp_url: str = "") -> bool:
    key = category_key(category)
    url = normalize_cell(wp_url).lower()
    return (
        "saega2.seaga.co.kr" in url
        or "신장정신" in key
        or ("신장" in key and "정신" in key)
    )


def strip_source_news_block(content_html: str) -> str:
    return re.sub(
        r'<blockquote[^>]*class=["\'][^"\']*source-news[^"\']*["\'][\s\S]*?</blockquote>\s*',
        "",
        content_html,
        flags=re.IGNORECASE,
    )


def remove_health_news_title_from_opening(content_html: str, keyword: str) -> str:
    """Avoid showing the original long news headline before its source link."""
    normalized_keyword = re.sub(r"[^가-힣A-Za-z0-9]", "", keyword or "").lower()
    if len(normalized_keyword) < 20:
        return content_html
    match = re.search(r"(<p[^>]*>)(.*?)(</p>)", content_html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return content_html

    opening = match.group(2)
    plain_opening = html.unescape(re.sub(r"<[^>]+>", " ", opening))
    normalized_opening = re.sub(r"[^가-힣A-Za-z0-9]", "", plain_opening).lower()
    if normalized_keyword not in normalized_opening:
        return content_html

    cleaned = opening
    for title in {keyword, html.escape(keyword, quote=False)}:
        cleaned = re.sub(
            rf"[\"'‘’“”]?\s*{re.escape(title)}\s*[\"'‘’“”]?\s*(?:이라는|라는|이란)\s*(?:보도|소식|발표)(?:는|에서)?",
            "이번 발표에서는",
            cleaned,
        )
        cleaned = cleaned.replace(title, "이번 소식")
    return content_html[: match.start(2)] + cleaned + content_html[match.end(2) :]


def ensure_wordpress_meta_intro(content_html: str, meta_description: str) -> str:
    meta_description = normalize_cell(meta_description)
    if not meta_description:
        return content_html

    first_area = re.sub(r"<[^>]+>", " ", content_html[:600])
    first_area = re.sub(r"\s+", " ", first_area)
    if meta_description[:40] and meta_description[:40] in first_area:
        return content_html

    return f"<p>{html.escape(meta_description)}</p>\n{content_html}"


def ensure_saega2_source_news_reference(content_html: str, item: DraftItem, wp_url: str) -> str:
    if not is_saega2_health_news_item(item.category, wp_url):
        return content_html

    source_link = normalize_cell(item.result_link)
    if not source_link.startswith(("http://", "https://")):
        return content_html

    news_title = html.escape(item.keyword)
    # 신장정신 뉴스는 수집한 원문 링크를 그대로 유지합니다.
    # 검사 실패만으로 검색/홈페이지 주소로 대체하면 참고 뉴스가 달라질 수 있습니다.
    news_url = html.escape(source_link, quote=True)
    news_box = (
        '<blockquote class="source-news">'
        f'<strong>출처:</strong> <a href="{news_url}" target="_blank" rel="noopener">{news_title} 바로가기</a>'
        "</blockquote>"
    )

    content_html = remove_health_news_title_from_opening(content_html, item.keyword)
    content_html = strip_source_news_block(content_html)
    first_paragraph = re.search(r"</p>", content_html, flags=re.IGNORECASE)
    if first_paragraph:
        insert_at = first_paragraph.end()
        return content_html[:insert_at] + "\n" + news_box + "\n" + content_html[insert_at:]
    return news_box + "\n" + content_html


def ensure_wordpress_tags(
    wp_url: str,
    username: str,
    app_password: str,
    tag_names: list[str],
) -> list[int]:
    tag_ids: list[int] = []
    endpoint = wordpress_tags_endpoint(wp_url)

    for tag_name in tag_names:
        try:
            search_response = requests.get(
                endpoint,
                params={"search": tag_name, "per_page": 20},
                auth=(username, app_password),
                timeout=20,
            )
            search_response.raise_for_status()
            found = search_response.json()
            tag_id = None
            for item in found:
                if str(item.get("name", "")).strip().lower() == tag_name.lower():
                    tag_id = int(item["id"])
                    break

            if tag_id is None:
                create_response = requests.post(
                    endpoint,
                    json={"name": tag_name},
                    auth=(username, app_password),
                    timeout=20,
                )
                if create_response.status_code == 400:
                    existing_id = create_response.json().get("data", {}).get("term_id")
                    if existing_id:
                        tag_id = int(existing_id)
                    else:
                        create_response.raise_for_status()
                else:
                    create_response.raise_for_status()
                    tag_id = int(create_response.json()["id"])

            if tag_id and tag_id not in tag_ids:
                tag_ids.append(tag_id)
        except Exception as exc:
            logging.warning("워드프레스 태그 처리 실패, 건너뜁니다: %s / %s", tag_name, exc)

    return tag_ids


def seo_meta_payload(item: DraftItem, description: str) -> dict[str, str]:
    return {
        "rank_math_title": item.title,
        "rank_math_description": description,
        "rank_math_focus_keyword": item.seo_keyword,
        "_yoast_wpseo_title": item.title,
        "_yoast_wpseo_metadesc": description,
        "_yoast_wpseo_focuskw": item.seo_keyword,
    }


def auto_blog_seo_comment(item: DraftItem, description: str, tags: list[str]) -> str:
    payload = {
        "title": item.title,
        "description": description,
        "focus_keyword": item.keyword,
        "tags": tags[:12],
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded = base64.b64encode(raw).decode("ascii")
    return f"<!-- AUTO_BLOG_SEO:{encoded} -->"


def ensure_auto_blog_seo_comment(content_html: str, item: DraftItem, description: str, tags: list[str]) -> str:
    content_html = re.sub(r"<!--\s*AUTO_BLOG_SEO:[A-Za-z0-9+/=]+\s*-->\s*", "", content_html)
    return auto_blog_seo_comment(item, description, tags) + "\n" + content_html


def domain_from_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    domain = urlparse(url).netloc.lower()
    return domain[4:] if domain.startswith("www.") else domain


def load_ads_config(config_path: str) -> dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        logging.info("광고 설정 파일이 없어 광고 삽입을 건너뜁니다: %s", config_path)
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def ad_slots_for_domain(config: dict[str, Any], domain: str) -> list[dict[str, Any]]:
    if not config:
        return []
    excluded = {str(item).lower() for item in config.get("excluded_domains", [])}
    if domain in excluded:
        return []

    domain_config = config.get("domains", {}).get(domain)
    selected = domain_config if domain_config else config.get("default", {})
    if not selected.get("enabled", False):
        return []
    return list(selected.get("slots", []))


def read_ad_code(file_path: str) -> str:
    path = Path(file_path)
    if not path.exists():
        logging.warning("광고 파일을 찾지 못해 건너뜁니다: %s", file_path)
        return ""
    code = path.read_text(encoding="utf-8").strip()
    if not code or "여기에" in code:
        logging.warning("광고 코드가 비어 있거나 안내문만 있어 건너뜁니다: %s", file_path)
        return ""
    return code


ADSENSE_LOADER_PATTERN = re.compile(
    r"<script\b[^>]*\bsrc=[\"'][^\"']*pagead2\.googlesyndication\.com/pagead/js/adsbygoogle\.js[^\"']*[\"'][^>]*>\s*</script>",
    flags=re.IGNORECASE,
)


def compact_ad_code(ad_code: str, include_loader: bool) -> str:
    """Keep an ad as one compact HTML block and load the AdSense library once."""
    code = ad_code.strip()
    if not include_loader:
        code = ADSENSE_LOADER_PATTERN.sub("", code).strip()
    # Whitespace between tags is not needed, and TinyMCE otherwise turns it
    # into extra empty paragraphs around the ad code.
    code = re.sub(r">\s+<", "><", code)
    return '<div class="auto-blog-ad" style="margin:16px 0;">' + code + "</div>"


def insert_after_nth_paragraph(html: str, ad_code: str, paragraph_number: int) -> str:
    matches = list(re.finditer(r"</p\s*>", html, flags=re.IGNORECASE))
    if not matches:
        return html + "\n" + ad_code
    index = min(max(paragraph_number, 1), len(matches)) - 1
    insert_at = matches[index].end()
    return html[:insert_at] + "\n" + ad_code + "\n" + html[insert_at:]


# 광고가 들어가면 안 되는 덩어리. 목록이나 표 한가운데를 광고가 갈라놓으면
# 화면이 깨지고, 애드센스도 콘텐츠를 방해하는 배치로 본다.
AD_PROTECTED_TAGS = ("ul", "ol", "table", "blockquote", "pre", "figure")

# 본문에서 광고를 놓고 싶은 대략적인 위치(글 길이 대비 비율).
# 도입부 바로 아래(4%)는 광고가 글 맨 위에 오는 자리라 쓰지 않는다.
# 이보다 짧은 글에는 광고를 넣지 않는다. 내용보다 광고가 많아 보이는 글은
# 애드센스가 싫어하고, 클릭도 나오지 않는다.
MIN_BODY_LENGTH_FOR_ADS = 800

AD_POSITION_RATIOS = {
    "after_intro": 0.22,
    "first_section_end": 0.22,
    "middle": 0.55,
    "mid_content": 0.55,
}


def ad_protected_ranges(html: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for tag in AD_PROTECTED_TAGS:
        pattern = rf"<{tag}\b.*?</{tag}\s*>"
        for match in re.finditer(pattern, html, flags=re.IGNORECASE | re.DOTALL):
            ranges.append((match.start(), match.end()))
    return ranges


def inside_protected_range(offset: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start < offset < end for start, end in ranges)


def ad_anchor_points(html: str) -> list[tuple[int, bool]]:
    """광고를 끼워 넣어도 안전한 자리 목록. (위치, 소제목앞인가)"""
    ranges = ad_protected_ranges(html)
    anchors: list[tuple[int, bool]] = []
    for match in re.finditer(r"</p\s*>", html, flags=re.IGNORECASE):
        offset = match.end()
        if not inside_protected_range(offset, ranges):
            anchors.append((offset, False))
    for match in re.finditer(r"<h[23]\b", html, flags=re.IGNORECASE):
        offset = match.start()
        if not inside_protected_range(offset, ranges):
            anchors.append((offset, True))
    anchors.sort()

    # 문단 끝과 바로 뒤 소제목은 사실상 같은 자리다. 소제목 쪽만 남긴다.
    merged: list[tuple[int, bool]] = []
    for offset, is_heading in anchors:
        if merged and offset - merged[-1][0] <= 3:
            if is_heading:
                merged[-1] = (offset, True)
            continue
        merged.append((offset, is_heading))
    return merged


def choose_ad_anchor(
    anchors: list[tuple[int, bool]],
    body_length: int,
    ratio: float,
    used: list[int],
) -> int | None:
    """원하는 비율에 가장 가까운 자리를 고르되, 소제목 바로 앞을 우대한다.

    소제목 앞이 광고 효율이 가장 좋다. 읽던 사람이 한 단락을 끝내고
    다음 소제목으로 눈을 옮기는 길목이라, 광고가 자연스럽게 시야에 들어온다.
    """
    if not anchors:
        return None
    target = int(body_length * ratio)
    floor = int(body_length * 0.15)  # 글 맨 위에는 광고를 두지 않는다
    base_gap = max(300, body_length // 8)
    heading_bonus = max(200, int(body_length * 0.08))

    # 광고끼리 충분히 떨어뜨리는 것이 먼저지만, 그것 때문에 광고를
    # 아예 못 넣으면 손해다. 간격을 단계적으로 풀어가며 자리를 찾는다.
    # 다만 마지막 선까지 풀지는 않는다. 광고 두 개가 내용 없이 붙어 있는
    # 것은 광고 하나를 못 넣는 것보다 나쁘다(애드센스 정책 위반).
    hard_gap = min(400, max(body_length // 6, 1))
    for min_gap in (base_gap, max(base_gap // 2, hard_gap), hard_gap):
        best_offset = None
        best_score = None
        for offset, is_heading in anchors:
            if offset < floor:
                continue
            if any(abs(offset - mark) < min_gap for mark in used):
                continue
            score = abs(offset - target) - (heading_bonus if is_heading else 0)
            if best_score is None or score < best_score:
                best_score = score
                best_offset = offset
        if best_offset is not None:
            return best_offset
    return None


def summary_ad_offset(html: str, anchors: list[tuple[int, bool]]) -> int | None:
    """맺음말 바로 앞. 끝까지 읽은 사람이 다음 행동을 찾는 자리라 반응이 좋다."""
    match = re.search(r"<h2[^>]*>\s*최종\s*정리하면[,，]?", html, flags=re.IGNORECASE)
    if match:
        return match.start()
    headings = [offset for offset, is_heading in anchors if is_heading]
    if headings:
        return headings[-1]
    if anchors:
        return anchors[-1][0]
    return None


def insert_before_summary(html: str, ad_code: str) -> str:
    offset = summary_ad_offset(html, ad_anchor_points(html))
    if offset is None:
        return html + "\n" + ad_code
    return html[:offset] + "\n" + ad_code + "\n" + html[offset:]


def insert_ad_by_position(html: str, ad_code: str, position: str) -> str:
    """슬롯 하나만 넣을 때 쓰는 옛 방식. 여러 개는 insert_manual_ads 가 처리한다."""
    anchors = ad_anchor_points(html)
    if position in {"before_summary", "summary"}:
        offset = summary_ad_offset(html, anchors)
    else:
        ratio = AD_POSITION_RATIOS.get(position, AD_POSITION_RATIOS["after_intro"])
        offset = choose_ad_anchor(anchors, len(html), ratio, [])
    if offset is None:
        return insert_after_nth_paragraph(html, ad_code, 2)
    return html[:offset] + "\n" + ad_code + "\n" + html[offset:]


def plan_ad_positions(html: str, slots: list[dict[str, Any]]) -> list[tuple[int, dict[str, Any]]]:
    """광고를 넣을 자리를 미리 다 정한다.

    맺음말 앞부터 자리를 잡고, 나머지를 비율로 채운다. 이렇게 해야
    광고끼리 너무 붙지 않는다.
    """
    anchors = ad_anchor_points(html)
    if not anchors:
        return []

    body_length = len(html)
    used: list[int] = []
    plan: list[tuple[int, dict[str, Any]]] = []

    summary_slots = [slot for slot in slots if str(slot.get("position", "")) in {"before_summary", "summary"}]
    other_slots = [slot for slot in slots if slot not in summary_slots]

    for slot in summary_slots:
        offset = summary_ad_offset(html, anchors)
        if offset is None:
            continue
        plan.append((offset, slot))
        used.append(offset)

    for slot in other_slots:
        ratio = AD_POSITION_RATIOS.get(str(slot.get("position", "")), AD_POSITION_RATIOS["after_intro"])
        offset = choose_ad_anchor(anchors, body_length, ratio, used)
        if offset is None:
            continue
        plan.append((offset, slot))
        used.append(offset)

    plan.sort(key=lambda item: item[0])
    return plan


def insert_manual_ads(html: str, wp_url: str, ads_config_path: str) -> str:
    if "AUTO_BLOG_AD_INSERTED" in html:
        return html

    config = load_ads_config(ads_config_path)
    domain = domain_from_url(wp_url)
    slots = ad_slots_for_domain(config, domain)
    if not slots:
        logging.info("이 도메인은 광고 삽입 대상이 아닙니다: %s", domain)
        return html

    if len(html) < MIN_BODY_LENGTH_FOR_ADS:
        logging.info("본문이 너무 짧아 광고를 넣지 않습니다: %s자", len(html))
        return html

    usable: list[dict[str, Any]] = []
    for slot in slots[:3]:
        ad_code = read_ad_code(str(slot.get("file", "")))
        if not ad_code:
            continue
        usable.append({**slot, "_code": ad_code})

    plan = plan_ad_positions(html, usable)
    if not plan:
        logging.info("광고를 넣을 자리를 찾지 못했습니다: %s", domain)
        return html

    updated_html = html
    # 뒤에서부터 넣어야 앞쪽 위치가 밀리지 않는다.
    for order, (offset, slot) in reversed(list(enumerate(plan))):
        compact_code = compact_ad_code(str(slot.get("_code", "")), include_loader=order == 0)
        wrapped_ad = f'<!-- AUTO_BLOG_AD_INSERTED:{slot.get("name", "ad")} -->{compact_code}'
        updated_html = updated_html[:offset] + "\n" + wrapped_ad + "\n" + updated_html[offset:]

    spots = ", ".join(f"{int(offset / max(len(html), 1) * 100)}%" for offset, _ in plan)
    logging.info("수동광고 삽입 수: %s / 위치: %s / 도메인: %s", len(plan), spots, domain)
    return updated_html


def thumbnail_enabled() -> bool:
    return os.getenv("THUMBNAIL_ENABLED", "true").lower() in {"1", "true", "yes", "y"}


def thumbnail_size() -> tuple[int, int]:
    try:
        width = int(os.getenv("THUMBNAIL_WIDTH", "1200"))
        height = int(os.getenv("THUMBNAIL_HEIGHT", "630"))
        return width, height
    except ValueError:
        return 1200, 630


def thumbnail_quality() -> int:
    try:
        quality = int(os.getenv("THUMBNAIL_WEBP_QUALITY", "78"))
    except ValueError:
        quality = 78
    return max(45, min(95, quality))


def category_label(category: str) -> str:
    labels = {
        "최신이슈": "최신이슈",
        "정책지원": "정책지원",
        "대출관련": "대출정보",
        "대출": "대출정보",
        "신장정신": "건강정보",
        "웰빙": "웰빙",
        "꿈해몽": "꿈해몽",
    }
    return labels.get(category, category or "정보")


def thumbnail_colors(category: str) -> tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]:
    if "정책" in category:
        return (15, 89, 117), (238, 246, 249), (249, 197, 84)
    if "대출" in category:
        return (31, 79, 69), (241, 247, 244), (245, 185, 96)
    if "꿈" in category:
        return (64, 48, 104), (245, 242, 252), (190, 161, 255)
    if "웰빙" in category or "신장" in category:
        return (42, 100, 74), (243, 249, 245), (131, 198, 151)
    return (37, 67, 99), (244, 248, 252), (255, 197, 91)


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9가-힣_-]+", "-", value or "").strip("-")
    digest = hashlib.sha1((value or "").encode("utf-8")).hexdigest()[:8]
    ascii_slug = re.sub(r"[^a-z0-9-]+", "-", (value or "").lower()).strip("-")
    return f"{ascii_slug[:40] or 'thumbnail'}-{digest}.webp"


def upload_safe_filename(image_path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", image_path.stem).strip("-._")
    if not stem:
        stem = "thumbnail"
    return f"{stem[:80]}.webp"


def load_thumbnail_font(size: int):
    from PIL import ImageFont

    candidates = [
        os.getenv("THUMBNAIL_FONT", ""),
        "C:/Windows/Fonts/malgun.ttf",
        "C:/Windows/Fonts/malgunbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def text_width(draw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def wrap_korean_text(draw, text: str, font, max_width: int, max_lines: int) -> list[str]:
    tokens = re.split(r"(\s+)", text.strip())
    lines: list[str] = []
    current = ""
    for token in tokens:
        trial = current + token
        if text_width(draw, trial, font) <= max_width:
            current = trial
            continue
        if current.strip():
            lines.append(current.strip())
            current = token.strip()
        else:
            chunk = ""
            for char in token:
                if text_width(draw, chunk + char, font) <= max_width:
                    chunk += char
                else:
                    if chunk:
                        lines.append(chunk)
                    chunk = char
            current = chunk
        if len(lines) >= max_lines:
            break
    if current.strip() and len(lines) < max_lines:
        lines.append(current.strip())
    if lines and len(lines) == max_lines and len(text) > len(" ".join(lines)):
        lines[-1] = lines[-1].rstrip(". ") + "..."
    return lines[:max_lines]


def ensure_thumbnail_dir() -> Path:
    configured_dir = os.getenv("THUMBNAIL_DIR", DEFAULT_THUMBNAIL_DIR).strip().strip('"')
    configured_path = Path(configured_dir)
    candidates = [
        Path(tempfile.gettempdir()) / "auto_blog_thumbnails",
        configured_path if configured_path.is_absolute() else BASE_DIR / configured_path,
    ]

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            os.makedirs(str(candidate), exist_ok=True)
            test_file = candidate / ".write_test"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink(missing_ok=True)
            return candidate
        except Exception as exc:
            last_error = exc
            logging.warning("썸네일 폴더 생성 실패, 다른 위치를 시도합니다: %s / %s", candidate, exc)

    raise RuntimeError(f"썸네일 폴더를 만들 수 없습니다: {last_error}")


def generate_thumbnail(item: DraftItem) -> Path | None:
    if not thumbnail_enabled():
        return None
    try:
        from PIL import Image, ImageDraw

        width, height = thumbnail_size()
        primary, background, accent = thumbnail_colors(item.category)
        image = Image.new("RGB", (width, height), background)
        draw = ImageDraw.Draw(image)

        margin = int(width * 0.075)
        label_font = load_thumbnail_font(44)
        title_font = load_thumbnail_font(74)
        small_font = load_thumbnail_font(34)

        draw.rectangle((0, 0, width, 28), fill=accent)
        draw.rounded_rectangle((margin, 76, margin + 280, 136), radius=18, fill=primary)
        draw.text((margin + 28, 87), category_label(item.category), fill=(255, 255, 255), font=label_font)

        title = re.sub(r"\s+", " ", item.title or item.keyword).strip()
        lines = wrap_korean_text(draw, title, title_font, width - margin * 2, 3)
        y = 190
        for line in lines:
            draw.text((margin, y), line, fill=primary, font=title_font)
            y += 92

        keyword_text = f"핵심 키워드: {item.seo_keyword}"
        draw.line((margin, height - 128, width - margin, height - 128), fill=accent, width=5)
        draw.text((margin, height - 94), keyword_text[:60], fill=(67, 80, 91), font=small_font)

        output_dir = ensure_thumbnail_dir()
        output_path = output_dir / safe_filename(item.keyword or item.title)
        image.save(output_path, "WEBP", quality=thumbnail_quality(), method=6)
        return output_path
    except ImportError:
        logging.warning("Pillow가 설치되어 있지 않아 썸네일 생성을 건너뜁니다. pip install pillow 후 다시 실행하세요.")
        return None
    except Exception as exc:
        logging.warning("썸네일 생성 실패, 대표 이미지 없이 임시저장을 계속합니다: %s", exc)
        return None


def upload_thumbnail(
    wp_url: str,
    username: str,
    app_password: str,
    image_path: Path,
    alt_text: str,
) -> int | None:
    try:
        response = requests.post(
            wordpress_media_endpoint(wp_url),
            data=image_path.read_bytes(),
            headers={
                "Content-Disposition": f'attachment; filename="{upload_safe_filename(image_path)}"',
                "Content-Type": "image/webp",
            },
            auth=(username, app_password),
            timeout=60,
        )
        response.raise_for_status()
        media_id = int(response.json()["id"])
        try:
            requests.post(
                f"{wordpress_media_endpoint(wp_url)}/{media_id}",
                json={"alt_text": alt_text, "title": alt_text},
                auth=(username, app_password),
                timeout=20,
            )
        except Exception as exc:
            logging.debug("썸네일 alt/title 업데이트 실패: %s", exc)
        return media_id
    except Exception as exc:
        logging.warning("워드프레스 썸네일 업로드 실패, 대표 이미지 없이 진행합니다: %s", exc)
        return None


def update_wordpress_seo_meta(
    wp_url: str,
    username: str,
    app_password: str,
    post_id: str,
    item: DraftItem,
) -> bool:
    meta_description, _ = parse_meta_description_and_tags(item.meta, item.keyword, item.category)
    payload = {
        "excerpt": meta_description,
        "meta": seo_meta_payload(item, meta_description),
    }
    response = requests.post(
        wordpress_post_endpoint(wp_url, post_id),
        json=payload,
        auth=(username, app_password),
        timeout=40,
    )
    if response.status_code >= 400:
        logging.warning(
            "SEO 메타 업데이트 실패: %s / %s",
            post_id,
            response.text[:300],
        )
        return False
    logging.info("SEO 메타 업데이트 완료: post_id=%s focus_keyword=%s", post_id, item.keyword)
    return True


def verify_wordpress_write_access(wp_url: str, username: str, app_password: str) -> None:
    try:
        response = requests.get(
            wordpress_users_me_endpoint(wp_url),
            params={"context": "edit"},
            auth=(username, app_password),
            timeout=20,
        )
    except Exception as exc:
        raise RuntimeError(f"WordPress 로그인 확인 실패: {wp_url} / {exc}") from exc

    if response.status_code == 401:
        raise RuntimeError(
            f"WordPress 인증 실패: {wp_url}. "
            "해당 사이트에서 만든 응용 프로그램 비밀번호와 워드프레스 로그인 아이디를 다시 확인하세요."
        )
    if response.status_code == 403:
        raise RuntimeError(
            f"WordPress 권한 부족: {wp_url}. "
            "이 계정에 글 작성 권한(작성자/편집자/관리자)이 필요합니다."
        )
    if response.status_code >= 400:
        raise RuntimeError(f"WordPress 로그인 확인 실패: {wp_url} / {response.status_code} / {response.text[:200]}")

    data = response.json()
    capabilities = data.get("capabilities", {}) or {}
    if capabilities and not (capabilities.get("edit_posts") or capabilities.get("publish_posts")):
        roles = ", ".join(data.get("roles", [])) or "확인 불가"
        raise RuntimeError(
            f"WordPress 글 작성 권한이 없습니다: {wp_url}. "
            f"현재 역할: {roles}. 사용자 역할을 작성자/편집자/관리자로 변경하세요."
        )


def save_wordpress_draft(
    item: DraftItem,
    wp_url: str,
    username: str,
    app_password: str,
    category_id: str | None = None,
    ads_config_path: str = DEFAULT_ADS_CONFIG_FILE,
) -> DraftResult:
    verify_wordpress_write_access(wp_url, username, app_password)
    meta_description, tag_names = parse_meta_description_and_tags(item.meta, item.keyword, item.category)
    checked_html = sanitize_body_links(item.html)
    checked_html = ensure_wordpress_meta_intro(checked_html, meta_description)
    checked_html = ensure_saega2_source_news_reference(checked_html, item, wp_url)
    content_html = insert_manual_ads(checked_html, wp_url, ads_config_path)
    content_html = ensure_auto_blog_seo_comment(content_html, item, meta_description, tag_names)
    tag_ids = ensure_wordpress_tags(wp_url, username, app_password, tag_names)
    thumbnail_path = generate_thumbnail(item)
    featured_media_id = None
    if thumbnail_path:
        featured_media_id = upload_thumbnail(
            wp_url,
            username,
            app_password,
            thumbnail_path,
            alt_text=item.seo_keyword,
        )

    # 사이트에 실제로 있는 카테고리 목록을 보고 글 주제에 맞는 것을 고른다.
    # 못 고르면 publish_targets.json 의 값을 그대로 쓴다.
    if wp_category is not None:
        try:
            category_id = wp_category.pick_category_id(
                wp_url, item.keyword, item.title, category_id, username, app_password)
        except Exception as 오류:
            logging.warning("워드프레스 카테고리를 고르지 못했습니다: %s", 오류)

    payload: dict[str, Any] = {
        "title": item.title,
        "content": content_html,
        "status": "draft",
        "excerpt": meta_description,
    }
    if category_id:
        payload["categories"] = [int(category_id)]
    if tag_ids:
        payload["tags"] = tag_ids
    if featured_media_id:
        payload["featured_media"] = featured_media_id
    if os.getenv("WORDPRESS_SEO_META_ENABLED", "true").lower() in {"1", "true", "yes", "y"}:
        payload["meta"] = seo_meta_payload(item, meta_description)

    response = requests.post(
        wordpress_posts_endpoint(wp_url),
        json=payload,
        auth=(username, app_password),
        timeout=40,
    )
    if response.status_code >= 400 and "meta" in payload:
        logging.warning("SEO 메타 직접 입력이 거부되어 메타 필드 없이 다시 임시저장합니다: %s", response.text[:300])
        payload.pop("meta", None)
        response = requests.post(
            wordpress_posts_endpoint(wp_url),
            json=payload,
            auth=(username, app_password),
            timeout=40,
        )
    response.raise_for_status()
    data = response.json()

    # 워드프레스는 등록되지 않은 meta 를 오류 없이 버린다. 그러면 Rank Math
    # 포커스 키워드와 스니펫이 비어 있는 채로 글만 올라간다. 조용히 넘어가면
    # 나중에 글마다 손으로 채워야 하므로 여기서 알려 준다.
    if "meta" in payload:
        saved_meta = data.get("meta") or {}
        if not str(saved_meta.get("rank_math_focus_keyword", "")).strip():
            logging.warning(
                "SEO 메타가 저장되지 않았습니다: %s. "
                "wp-content/mu-plugins/rank-math-rest.php 가 올라가 있는지 확인하세요.",
                domain_from_url(wp_url),
            )

    return DraftResult(
        post_id=str(data.get("id", "")),
        link=str(data.get("link", "")),
        status=str(data.get("status", "draft")),
        platform="WordPress",
    )


def tistory_write_endpoint() -> str:
    return "https://www.tistory.com/apis/post/write"


def tistory_attach_endpoint() -> str:
    return "https://www.tistory.com/apis/post/attach"


def upload_tistory_image(target: PublishTarget, image_path: Path) -> str:
    with image_path.open("rb") as image_file:
        response = requests.post(
            tistory_attach_endpoint(),
            data={
                "access_token": target.tistory_access_token,
                "output": "json",
                "blogName": target.tistory_blog_name,
            },
            files={
                "uploadedfile": (upload_safe_filename(image_path), image_file, "image/webp"),
            },
            timeout=60,
        )
    response.raise_for_status()
    data = response.json()
    tistory_data = data.get("tistory", {})
    status = str(tistory_data.get("status", ""))
    if status and status != "200":
        raise RuntimeError(f"Tistory 이미지 업로드 실패: {data}")

    item_data = tistory_data.get("item", {})
    image_url = str(item_data.get("url", "") or item_data.get("replacer", ""))
    if not image_url:
        raise RuntimeError(f"Tistory 이미지 URL을 찾지 못했습니다: {data}")
    return image_url


def add_tistory_top_image(content_html: str, image_url: str, keyword: str) -> str:
    if not image_url:
        return content_html
    image_html = (
        f'<p><img src="{image_url}" alt="{keyword}" '
        'style="width:100%;height:auto;display:block;margin:0 auto 24px;" /></p>'
    )
    return f"{image_html}\n{content_html}"


def save_tistory_private_post(item: DraftItem, target: PublishTarget) -> DraftResult:
    if not target.tistory_access_token or not target.tistory_blog_name:
        raise RuntimeError(
            f"Tistory 설정이 부족합니다: {item.category}. "
            "publish_targets.json의 blog_name과 .env의 access token을 확인하세요."
        )

    checked_html = sanitize_body_links(item.html)
    content_html = insert_manual_ads(checked_html, target.url, target.ads_config_path)
    meta_description, tag_names = parse_meta_description_and_tags(item.meta, item.keyword, item.category)
    if meta_description:
        content_html = f"<p>{meta_description}</p>\n{content_html}"
    thumbnail_path = generate_thumbnail(item)
    if thumbnail_path:
        try:
            image_url = upload_tistory_image(target, thumbnail_path)
            content_html = add_tistory_top_image(content_html, image_url, item.keyword)
            logging.info("Tistory 본문 상단 이미지 삽입 완료: %s", image_url)
        except Exception as exc:
            logging.warning("Tistory 이미지 업로드 실패, 이미지 없이 저장합니다: %s", exc)

    response = requests.post(
        tistory_write_endpoint(),
        data={
            "access_token": target.tistory_access_token,
            "output": "json",
            "blogName": target.tistory_blog_name,
            "title": item.title,
            "content": content_html,
            "visibility": "0",
            "tag": ",".join(tag_names),
            "acceptComment": "1",
        },
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    tistory_data = data.get("tistory", {})
    status = str(tistory_data.get("status", ""))
    if status and status != "200":
        raise RuntimeError(f"Tistory 저장 실패: {data}")

    item_data = tistory_data.get("item", {})
    post_id = str(item_data.get("postId", ""))
    link = str(item_data.get("url", "")) or (
        f"https://{target.url.strip('/')}/{post_id}" if post_id else f"https://{target.url.strip('/')}"
    )
    return DraftResult(post_id=post_id, link=link, status="private", platform="Tistory")


def save_item_to_target(item: DraftItem, target: PublishTarget) -> DraftResult:
    if target.platform == "wordpress":
        if not target.url or not target.username or not target.app_password:
            raise RuntimeError(f"WordPress 설정이 부족합니다: {item.category} -> {target.url}")
        return save_wordpress_draft(
            item,
            target.url,
            target.username,
            target.app_password,
            target.category_id,
            target.ads_config_path,
        )
    if target.platform == "tistory":
        return save_tistory_private_post(item, target)
    raise RuntimeError(f"지원하지 않는 저장 대상입니다: {target.platform}")


def attach_thumbnail_to_saved_draft(
    item: DraftItem,
    draft_link: str,
    wp_url: str,
    username: str,
    app_password: str,
) -> bool:
    post_id = extract_post_id_from_link(draft_link)
    if not post_id:
        logging.warning("Cannot find WordPress post id from link: %s", draft_link)
        return False

    thumbnail_path = generate_thumbnail(item)
    if not thumbnail_path:
        logging.warning("Thumbnail file was not created: %s", item.keyword)
        return False

    logging.info("Thumbnail file created: %s", thumbnail_path)
    media_id = upload_thumbnail(
        wp_url,
        username,
        app_password,
        thumbnail_path,
        alt_text=item.seo_keyword,
    )
    if not media_id:
        return False

    response = requests.post(
        wordpress_post_endpoint(wp_url, post_id),
        json={"featured_media": media_id},
        auth=(username, app_password),
        timeout=40,
    )
    response.raise_for_status()
    logging.info("Featured image attached: post_id=%s media_id=%s", post_id, media_id)
    return True


def repair_wordpress_draft(
    item: DraftItem,
    draft_link: str,
    target: PublishTarget,
) -> None:
    post_id = extract_post_id_from_link(draft_link)
    if not post_id:
        logging.warning("임시저장 링크에서 글 ID를 찾지 못했습니다: %s", draft_link)
        return
    if not target.url or not target.username or not target.app_password:
        logging.warning("보정 작업 설정 부족: [%s] %s", item.category, target.url)
        return

    post = fetch_wordpress_post(target.url, target.username, target.app_password, post_id)
    if not post:
        return

    raw_content = normalize_cell(post.get("content", {}).get("raw", ""))
    if raw_content:
        meta_description, tag_names = parse_meta_description_and_tags(item.meta, item.keyword, item.category)
        checked_content = sanitize_body_links(raw_content)
        checked_content = ensure_wordpress_meta_intro(checked_content, meta_description)
        checked_content = ensure_saega2_source_news_reference(checked_content, item, target.url)
        checked_content = ensure_auto_blog_seo_comment(checked_content, item, meta_description, tag_names)
        if checked_content != raw_content:
            response = requests.post(
                wordpress_post_endpoint(target.url, post_id),
                json={"content": checked_content},
                auth=(target.username, target.app_password),
                timeout=40,
            )
            response.raise_for_status()
            logging.info("본문 링크 보정 완료: post_id=%s", post_id)

    featured_media = int(post.get("featured_media") or 0)
    if featured_media:
        logging.info("대표 이미지가 이미 있습니다: post_id=%s media_id=%s", post_id, featured_media)
    else:
        thumbnail_path = generate_thumbnail(item)
        if not thumbnail_path:
            logging.warning("썸네일 파일을 만들지 못했습니다: %s", item.keyword)
        else:
            media_id = upload_thumbnail(
                target.url,
                target.username,
                target.app_password,
                thumbnail_path,
                alt_text=item.seo_keyword,
            )
            if media_id:
                response = requests.post(
                    wordpress_post_endpoint(target.url, post_id),
                    json={"featured_media": media_id},
                    auth=(target.username, target.app_password),
                    timeout=40,
                )
                response.raise_for_status()
                logging.info("대표 이미지 보정 완료: post_id=%s media_id=%s", post_id, media_id)

    update_wordpress_seo_meta(target.url, target.username, target.app_password, post_id, item)


def ensure_completion_sheet(spreadsheet, category: str):
    title = COMPLETION_SHEET_ALIASES.get(category, "완료시트")
    try:
        sheet = spreadsheet.worksheet(title)
    except WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(title=title, rows=1000, cols=len(COMPLETION_HEADERS))

    if not sheet.row_values(1):
        sheet.update(range_name="A1:H1", values=[COMPLETION_HEADERS])
    return sheet


def append_completion(spreadsheet, item: DraftItem, result: DraftResult) -> None:
    sheet = ensure_completion_sheet(spreadsheet, item.category)
    sheet.append_row(
        [
            item.keyword,
            item.category,
            item.title,
            result.link,
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            "임시저장완료",
            result.platform,
            result.post_id,
        ],
        value_input_option="USER_ENTERED",
    )


def completion_has_post(sheet, link: str, post_id: str) -> bool:
    values = sheet.get_all_values()
    if len(values) < 2:
        return False
    headers = values[0]
    link_col = header_index(headers, ["링크", "워드프레스 초안 URL", "URL"], 4)
    post_id_col = header_index(headers, ["포스트 ID", "post_id"], 8)
    for row in values[1:]:
        existing_link = normalize_cell(row[link_col - 1] if link_col <= len(row) else "")
        existing_post_id = normalize_cell(row[post_id_col - 1] if post_id_col <= len(row) else "")
        if post_id and existing_post_id == post_id:
            return True
        if link and existing_link == link:
            return True
    return False


def append_published_completion(spreadsheet, item: DraftItem, result: DraftResult, final_title: str) -> bool:
    sheet = ensure_completion_sheet(spreadsheet, item.category)
    if completion_has_post(sheet, result.link, result.post_id):
        return False
    sheet.append_row(
        [
            item.keyword,
            item.category,
            final_title or item.title,
            result.link,
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            PUBLISHED_STATUS,
            result.platform,
            result.post_id,
        ],
        value_input_option="USER_ENTERED",
    )
    return True


def clean_wordpress_title(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def fetch_wordpress_post(
    wp_url: str,
    username: str,
    app_password: str,
    post_id: str,
) -> dict[str, Any] | None:
    response = requests.get(
        wordpress_post_endpoint(wp_url, post_id),
        params={"context": "edit"},
        auth=(username, app_password),
        timeout=30,
    )
    if response.status_code == 404:
        logging.warning("워드프레스 글을 찾지 못했습니다: %s / post_id=%s", wp_url, post_id)
        return None
    response.raise_for_status()
    return response.json()


def wordpress_draft_post_id(result_links: str, target: PublishTarget) -> str | None:
    """Return an ID only when the saved URL belongs to this WordPress site.

    The result column also preserves the original news URL.  Extracting a
    numeric ID from that unrelated URL caused published posts to be missed.
    """
    target_host = urlparse(target.url if target.url.startswith(("http://", "https://")) else f"https://{target.url}").netloc.lower()
    for link in normalize_cell(result_links).splitlines():
        parsed = urlparse(link.strip())
        if parsed.netloc.lower() != target_host:
            continue
        post_id = extract_post_id_from_link(link)
        if post_id:
            return post_id
    return None


def find_published_wordpress_post(
    item: DraftItem,
    target: PublishTarget,
) -> dict[str, Any] | None:
    """Find a manually published WordPress post when its draft URL was lost.

    Exact title matches are preferred. A keyword-and-title similarity fallback
    is deliberately conservative so an unrelated older post is never moved.
    """
    expected_title = title_match_key(item.title)
    keyword_key = title_match_key(item.keyword)
    try:
        response = requests.get(
            wordpress_posts_endpoint(target.url),
            params={"status": "publish", "search": item.keyword, "per_page": 100, "context": "edit"},
            auth=(target.username, target.app_password),
            timeout=30,
        )
        response.raise_for_status()
        posts = response.json()
    except Exception as exc:
        logging.warning("워드프레스 공개 글 제목 검색 실패: [%s] %s / %s", item.category, item.keyword, exc)
        return None

    best_post: dict[str, Any] | None = None
    best_score = 0.0
    for post in posts if isinstance(posts, list) else []:
        if normalize_cell(post.get("status", "")) != "publish":
            continue
        candidate_title = clean_wordpress_title(post.get("title", {}).get("rendered", ""))
        candidate_key = title_match_key(candidate_title)
        if candidate_key and candidate_key == expected_title:
            return post
        if not keyword_key or keyword_key not in candidate_key:
            continue
        score = SequenceMatcher(None, expected_title, candidate_key).ratio()
        if score > best_score:
            best_post = post
            best_score = score

    if best_post and best_score >= 0.58:
        logging.info("워드프레스 공개 글 유사 제목으로 확인: [%s] %s (score=%.2f)", item.category, item.keyword, best_score)
        return best_post
    return None


def sync_published_completion_rows(
    spreadsheet,
    publish_target_config: dict[str, Any],
    default_wp_url: str,
    default_username: str,
    default_app_password: str,
    default_category_id: str | None,
    default_ads_config_path: str,
    limit: int,
) -> None:
    checked_count = 0
    updated_count = 0
    sheet_names = list(dict.fromkeys(COMPLETION_SHEET_ALIASES.values()))

    for sheet_name in sheet_names:
        try:
            sheet = spreadsheet.worksheet(sheet_name)
        except WorksheetNotFound:
            continue

        values = sheet.get_all_values()
        if len(values) < 2:
            continue

        headers = values[0]
        keyword_col = header_index(headers, ["키워드", "기본키워드"], 1)
        category_col = header_index(headers, ["카테고리"], 2)
        title_col = header_index(headers, ["제목", "발행 제목"], 3)
        link_col = header_index(headers, ["링크", "워드프레스 초안 URL", "URL"], 4)
        status_col = header_index(headers, ["상태"], 6)
        platform_col = header_index(headers, ["플랫폼"], 7)
        post_id_col = header_index(headers, ["포스트 ID", "post_id"], 8)

        for row_number, row in enumerate(values[1:], start=2):
            if checked_count >= limit:
                logging.info("발행 확인 종료: checked=%s updated=%s", checked_count, updated_count)
                return

            category = normalize_cell(row[category_col - 1] if category_col <= len(row) else "")
            status = normalize_cell(row[status_col - 1] if status_col <= len(row) else "")
            platform = normalize_cell(row[platform_col - 1] if platform_col <= len(row) else "")
            link = normalize_cell(row[link_col - 1] if link_col <= len(row) else "")
            post_id = normalize_cell(row[post_id_col - 1] if post_id_col <= len(row) else "") or extract_post_id_from_link(link)

            if not category or not post_id:
                continue
            if platform and platform.lower() != "wordpress":
                continue
            if status == PUBLISHED_STATUS:
                continue
            if status and status != DONE_STATUS:
                continue

            target = resolve_publish_target(
                category,
                publish_target_config,
                default_wp_url,
                default_username,
                default_app_password,
                default_category_id,
                default_ads_config_path,
            )
            if target.platform != "wordpress":
                continue
            if not target.url or not target.username or not target.app_password:
                logging.warning("발행 확인 설정 부족: %s -> %s", category, target.url)
                continue

            checked_count += 1
            keyword = normalize_cell(row[keyword_col - 1] if keyword_col <= len(row) else "")
            try:
                post = fetch_wordpress_post(target.url, target.username, target.app_password, post_id)
                if not post:
                    continue
                post_status = normalize_cell(post.get("status", ""))
                if post_status != "publish":
                    logging.info("아직 발행 전: [%s] %s / status=%s", category, keyword, post_status)
                    continue

                final_title = clean_wordpress_title(post.get("title", {}).get("rendered", "")) or normalize_cell(
                    row[title_col - 1] if title_col <= len(row) else ""
                )
                final_link = normalize_cell(post.get("link", "")) or link
                sheet.update_cell(row_number, title_col, final_title)
                sheet.update_cell(row_number, link_col, final_link)
                sheet.update_cell(row_number, status_col, PUBLISHED_STATUS)
                updated_count += 1
                logging.info("발행완료 반영: [%s] %s -> %s", category, final_title, final_link)
            except Exception as exc:
                logging.warning("발행 확인 실패, 건너뜁니다: [%s] %s / %s", category, keyword, exc)

    logging.info("발행 확인 완료: checked=%s updated=%s", checked_count, updated_count)


def title_match_key(value: str) -> str:
    """Compare published titles without being tripped up by spacing or punctuation."""
    clean = html.unescape(re.sub(r"<[^>]+>", " ", value or "")).lower()
    return re.sub(r"[^0-9a-z가-힣]", "", clean)


def tistory_rss_url(target: PublishTarget) -> str:
    base_url = target.url.strip()
    if not base_url.startswith(("http://", "https://")):
        base_url = "https://" + base_url
    parsed = urlparse(base_url)
    return urlunparse((parsed.scheme or "https", parsed.netloc, "/rss", "", "", ""))


def rss_child_text(element: ET.Element, name: str) -> str:
    for child in element:
        child_name = child.tag.rsplit("}", 1)[-1].lower()
        if child_name == name.lower():
            return normalize_cell("".join(child.itertext()))
    return ""


def find_published_tistory_post(item: DraftItem, target: PublishTarget) -> dict[str, str] | None:
    """Find a matching public Tistory post through the blog's RSS feed.

    RSS is public, requires no token, and gives us the final visitor URL only
    after the owner has actually published the draft.
    """
    rss_url = tistory_rss_url(target)
    try:
        response = requests.get(
            rss_url,
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 AutoBlog published-sync"},
        )
        response.raise_for_status()
        root = ET.fromstring(response.content)
    except Exception as exc:
        logging.warning("티스토리 RSS 확인 실패: %s / %s", rss_url, exc)
        return None

    expected_titles = {
        title_match_key(title)
        for title in (item.tistory_title, item.title)
        if title_match_key(title)
    }
    if not expected_titles:
        return None

    closest_match: dict[str, str] | None = None
    closest_score = 0.0
    keyword_key = title_match_key(item.keyword)
    for entry in root.iter():
        if entry.tag.rsplit("}", 1)[-1].lower() != "item":
            continue
        published_title = clean_wordpress_title(rss_child_text(entry, "title"))
        published_link = html.unescape(rss_child_text(entry, "link") or rss_child_text(entry, "guid"))
        if not published_title or not published_link:
            continue
        published_key = title_match_key(published_title)
        if published_key in expected_titles:
            return {"title": published_title, "link": published_link}
        if not keyword_key or keyword_key not in published_key:
            continue
        score = max((SequenceMatcher(None, expected, published_key).ratio() for expected in expected_titles), default=0.0)
        if score > closest_score:
            closest_match = {"title": published_title, "link": published_link}
            closest_score = score

    if closest_match and closest_score >= 0.58:
        logging.info("티스토리 공개 글 유사 제목으로 확인: [%s] %s (score=%.2f)", item.category, item.keyword, closest_score)
        return closest_match
    return None


def sync_published_from_today_rows(
    spreadsheet,
    today_sheet,
    publish_target_config: dict[str, Any],
    default_wp_url: str,
    default_username: str,
    default_app_password: str,
    default_category_id: str | None,
    default_ads_config_path: str,
    limit: int,
) -> None:
    items = collect_saved_draft_items(today_sheet, limit)
    checked_count = 0
    moved_count = 0
    logging.info("오늘작성 발행 확인 대상 수: %s", len(items))

    for item, draft_link in items:
        targets = resolve_publish_targets(
            item.category,
            publish_target_config,
            default_wp_url,
            default_username,
            default_app_password,
            default_category_id,
            default_ads_config_path,
        )
        active_targets = [target for target in targets if target.platform in {"wordpress", "tistory"}]
        if not active_targets:
            continue

        published_targets = 0
        latest_link = draft_link
        for target in active_targets:
            checked_count += 1
            try:
                if target.platform == "wordpress":
                    if not target.url or not target.username or not target.app_password:
                        logging.warning("발행 확인 설정 부족: %s -> %s", item.category, target.url)
                        continue
                    post_id = wordpress_draft_post_id(draft_link, target)
                    post = (
                        fetch_wordpress_post(target.url, target.username, target.app_password, post_id)
                        if post_id
                        else find_published_wordpress_post(item, target)
                    )
                    if not post or normalize_cell(post.get("status", "")) != "publish":
                        logging.info("아직 워드프레스 발행 전: [%s] %s", item.category, item.keyword)
                        continue
                    post_id = normalize_cell(post.get("id", "")) or post_id or "wordpress-unknown"
                    final_title = clean_wordpress_title(post.get("title", {}).get("rendered", "")) or item.title
                    final_link = normalize_cell(post.get("link", "")) or draft_link
                    result = DraftResult(post_id=post_id, link=final_link, status=PUBLISHED_STATUS, platform="WordPress")
                else:
                    published_post = find_published_tistory_post(item, target)
                    if not published_post:
                        logging.info("아직 티스토리 발행 전 또는 RSS 반영 대기: [%s] %s -> %s", item.category, item.keyword, target.url)
                        continue
                    final_title = published_post["title"]
                    final_link = published_post["link"]
                    result = DraftResult(
                        post_id="tistory-" + hashlib.sha256(final_link.encode("utf-8")).hexdigest()[:16],
                        link=final_link,
                        status=PUBLISHED_STATUS,
                        platform="Tistory",
                    )

                published_targets += 1
                latest_link = merge_result_link(latest_link, final_link)
                appended = append_published_completion(spreadsheet, item, result, final_title)
                if appended:
                    moved_count += 1
                    logging.info("완료시트 이동 완료: [%s] %s -> %s", item.category, final_title, final_link)
                else:
                    logging.info("이미 완료시트에 반영된 글입니다: [%s] %s", item.category, final_title)
            except Exception as exc:
                logging.warning("오늘작성 발행 확인 실패, 건너뜁니다: [%s] %s / %s", item.category, item.keyword, exc)

        if published_targets:
            final_status = PUBLISHED_STATUS if published_targets == len(active_targets) else PARTIAL_PUBLISHED_STATUS
            today_sheet.update_cell(item.row_number, item.status_col, final_status)
            today_sheet.update_cell(item.row_number, item.status_col + 1, latest_link)
            logging.info("오늘작성 상태 갱신: [%s] %s -> %s", item.category, item.keyword, final_status)

    logging.info("오늘작성 발행 확인 완료: checked=%s moved=%s", checked_count, moved_count)


def merge_result_link(existing: str, new_link: str) -> str:
    existing = normalize_cell(existing)
    new_link = normalize_cell(new_link)
    if not new_link:
        return existing
    if new_link in existing.splitlines():
        return existing
    if not existing:
        return new_link
    return f"{existing}\n{new_link}"


def update_draft_result_link(today_sheet, item: DraftItem, result: DraftResult, existing_link: str | None = None) -> str:
    merged = merge_result_link(item.result_link if existing_link is None else existing_link, result.link)
    today_sheet.update_cell(item.row_number, item.status_col + 1, merged)
    return merged


def mark_draft_saved(today_sheet, item: DraftItem, result: DraftResult) -> None:
    merged = merge_result_link(item.result_link, result.link)
    today_sheet.update_cell(item.row_number, item.status_col, DONE_STATUS)
    today_sheet.update_cell(item.row_number, item.status_col + 1, merged)


def run_pipeline(
    credentials_path: str,
    spreadsheet_id: str,
    wp_url: str,
    username: str,
    app_password: str,
    limit: int,
    category_id: str | None,
    ads_config_path: str,
    publish_targets_path: str,
) -> None:
    sheet_client = gspread.service_account(filename=credentials_path)
    spreadsheet = sheet_client.open_by_key(spreadsheet_id)
    today_sheet = spreadsheet.worksheet(TODAY_SHEET)
    publish_target_config = load_publish_target_config(publish_targets_path)

    items = collect_draft_items(today_sheet, limit)
    logging.info("임시저장 대상 글 수: %s", len(items))

    for item in items:
        logging.info("임시저장 시작: %s", item.title)
        try:
            targets = resolve_publish_targets(
                item.category,
                publish_target_config,
                wp_url,
                username,
                app_password,
                category_id,
                ads_config_path,
            )
            tistory_targets = [target for target in targets if target.platform == "tistory"]
            non_tistory_targets = [target for target in targets if target.platform != "tistory"]
            target = non_tistory_targets[0] if non_tistory_targets else targets[0]
            logging.info("저장 대상: [%s] %s -> %s", item.category, target.platform, target.url)
            if target.platform == "tistory":
                logging.info("Tistory item will be handled by browser draft saver: [%s] %s", item.category, item.title)
                continue
            current_result_link = item.result_link
            if target.url and target.url in current_result_link:
                logging.info("이미 임시저장된 워드프레스 대상은 건너뜁니다: [%s] %s", item.category, target.url)
            else:
                result = save_item_to_target(item, target)
                current_result_link = update_draft_result_link(today_sheet, item, result)
            for extra_target in non_tistory_targets[1:]:
                if extra_target.url and extra_target.url in current_result_link:
                    logging.info("이미 임시저장된 대상은 건너뜁니다: [%s] %s", item.category, extra_target.url)
                    continue
                logging.info("?????? [%s] %s -> %s", item.category, extra_target.platform, extra_target.url)
                extra_result = save_item_to_target(item, extra_target)
                current_result_link = update_draft_result_link(today_sheet, item, extra_result, current_result_link)
                logging.info("?꾩떆????꾨즺: %s", extra_result.link)
            if tistory_targets:
                logging.info("Tistory draft remains pending for browser saver: [%s] %s", item.category, item.title)
            else:
                today_sheet.update_cell(item.row_number, item.status_col, DONE_STATUS)
            logging.info("임시저장 처리 완료: %s", current_result_link)
        except Exception as exc:
            logging.exception("임시저장 실패, 다음 항목으로 넘어갑니다: %s / %s", item.keyword, exc)


def run_tistory_browser_after_publish(
    credentials_path: str,
    spreadsheet_id: str,
    publish_targets_path: str,
    limit: int,
) -> None:
    if os.getenv("TISTORY_BROWSER_AUTORUN", "true").lower() not in {"1", "true", "yes", "y"}:
        logging.info("Tistory browser draft saver is disabled by TISTORY_BROWSER_AUTORUN.")
        return

    try:
        from blog_tistory_browser_draft import run_pipeline as run_tistory_browser_pipeline
    except Exception as exc:
        logging.warning("Tistory browser draft saver could not be loaded: %s", exc)
        return

    category_filter = os.getenv("TISTORY_BROWSER_CATEGORY", "최신이슈,정책지원,대출,신장정신,꿈해몽")
    profile_dir = os.getenv("TISTORY_BROWSER_PROFILE_DIR", ".tistory_browser_profile")
    headless = os.getenv("TISTORY_BROWSER_HEADLESS", "false").lower() in {"1", "true", "yes", "y"}

    logging.info("Starting Tistory browser draft saver after WordPress drafts.")
    try:
        run_tistory_browser_pipeline(
            credentials_path=credentials_path,
            spreadsheet_id=spreadsheet_id,
            publish_targets_path=publish_targets_path,
            limit=limit,
            category_filter=category_filter,
            profile_dir=profile_dir,
            headless=headless,
        )
    except Exception as exc:
        logging.exception("Tistory browser draft saver failed: %s", exc)


def run_published_sync_pipeline(
    credentials_path: str,
    spreadsheet_id: str,
    wp_url: str,
    username: str,
    app_password: str,
    limit: int,
    category_id: str | None,
    ads_config_path: str,
    publish_targets_path: str,
) -> None:
    sheet_client = gspread.service_account(filename=credentials_path)
    spreadsheet = sheet_client.open_by_key(spreadsheet_id)
    today_sheet = spreadsheet.worksheet(TODAY_SHEET)
    publish_target_config = load_publish_target_config(publish_targets_path)
    sync_published_from_today_rows(
        spreadsheet,
        today_sheet,
        publish_target_config,
        wp_url,
        username,
        app_password,
        category_id,
        ads_config_path,
        limit,
    )


def run_thumbnail_attach_pipeline(
    credentials_path: str,
    spreadsheet_id: str,
    wp_url: str,
    username: str,
    app_password: str,
    limit: int,
    category_id: str | None,
    ads_config_path: str,
    publish_targets_path: str,
) -> None:
    sheet_client = gspread.service_account(filename=credentials_path)
    spreadsheet = sheet_client.open_by_key(spreadsheet_id)
    today_sheet = spreadsheet.worksheet(TODAY_SHEET)
    publish_target_config = load_publish_target_config(publish_targets_path)

    items = collect_saved_draft_items(today_sheet, limit)
    logging.info("임시저장 글 이미지/SEO 보정 대상 수: %s", len(items))

    for item, draft_link in items:
        logging.info("이미지/SEO 보정 시작: %s", item.title)
        try:
            target = resolve_publish_target(
                item.category,
                publish_target_config,
                wp_url,
                username,
                app_password,
                category_id,
                ads_config_path,
            )
            if target.platform != "wordpress":
                logging.info("WordPress가 아니어서 보정 작업을 건너뜁니다: [%s] %s", item.category, target.platform)
                continue
            repair_wordpress_draft(item, draft_link, target)
        except Exception as exc:
            logging.exception("이미지/SEO 보정 실패, 건너뜁니다: %s / %s", item.keyword, exc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="오늘작성의 발행대기 글을 워드프레스 초안으로 임시저장합니다.")
    parser.add_argument("--env", default=".env", help=".env 파일 경로")
    parser.add_argument("--limit", type=int, default=None, help="한 번에 처리할 최대 글 수")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--attach-thumbnails", action="store_true", help="이미 임시저장된 글의 대표 이미지와 SEO 메타를 보정합니다.")
    parser.add_argument("--sync-published", action="store_true", help="완료시트의 임시저장 글이 실제 발행됐는지 확인하고 발행완료로 갱신합니다.")
    parser.add_argument("--targets", default=None, help="카테고리별 저장 대상 설정 파일")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    load_dotenv(args.env)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    credentials_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    spreadsheet_id = os.getenv("SPREADSHEET_ID", SPREADSHEET_ID)
    wp_url = os.getenv("WORDPRESS_URL")
    username = os.getenv("WORDPRESS_USERNAME")
    app_password = os.getenv("WORDPRESS_APP_PASSWORD")
    category_id = os.getenv("WORDPRESS_CATEGORY_ID") or None
    ads_config_path = os.getenv("ADS_CONFIG_FILE", DEFAULT_ADS_CONFIG_FILE)
    publish_targets_path = args.targets or os.getenv("PUBLISH_TARGETS_FILE", DEFAULT_PUBLISH_TARGETS_FILE)
    limit = args.limit or int(os.getenv("MAX_PUBLISH_ROWS", "3"))

    if not credentials_path:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_FILE 값이 없습니다.")
    if not wp_url or not username or not app_password:
        raise RuntimeError("WORDPRESS_URL, WORDPRESS_USERNAME, WORDPRESS_APP_PASSWORD 값을 .env에 입력하세요.")
    if "여기에" in wp_url or "여기에" in username or "여기에" in app_password:
        raise RuntimeError("워드프레스 설정에 실제 값을 입력하세요.")

    if args.sync_published:
        run_published_sync_pipeline(
            credentials_path,
            spreadsheet_id,
            wp_url,
            username,
            app_password,
            limit,
            category_id,
            ads_config_path,
            publish_targets_path,
        )
    elif args.attach_thumbnails:
        run_thumbnail_attach_pipeline(
            credentials_path,
            spreadsheet_id,
            wp_url,
            username,
            app_password,
            limit,
            category_id,
            ads_config_path,
            publish_targets_path,
        )
    else:
        run_pipeline(
            credentials_path,
            spreadsheet_id,
            wp_url,
            username,
            app_password,
            limit,
            category_id,
            ads_config_path,
            publish_targets_path,
        )
        run_tistory_browser_after_publish(
            credentials_path,
            spreadsheet_id,
            publish_targets_path,
            limit,
        )


if __name__ == "__main__":
    main()
