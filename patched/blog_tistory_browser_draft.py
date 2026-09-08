"""
Save Tistory drafts without an API token by controlling a logged-in browser.

First run:
    1. Install Playwright:
       pip install playwright
       python -m playwright install chromium
    2. Run this script. A browser opens.
    3. Log in to Tistory in that browser if asked.
    4. Press Enter in the terminal to continue.

Normal run:
    python blog_tistory_browser_draft.py

This script reads "오늘작성" rows with status "발행대기", keeps only Tistory
targets from publish_targets.json, writes the post in the Tistory editor, clicks
draft save, and marks the sheet row as "임시저장완료".
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import html as html_module
import logging
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import gspread
import requests
from dotenv import load_dotenv

from blog_publish_pipeline import (
    DEFAULT_ADS_CONFIG_FILE,
    DEFAULT_PUBLISH_TARGETS_FILE,
    DONE_STATUS,
    PUBLISHED_STATUS,
    SPREADSHEET_ID,
    TODAY_SHEET,
    DraftItem,
    DraftResult,
    add_tistory_top_image,
    collect_draft_items,
    completion_has_post,
    ensure_completion_sheet,
    generate_thumbnail,
    insert_manual_ads,
    load_publish_target_config,
    mark_draft_saved,
    normalize_cell,
    parse_meta_description_and_tags,
    resolve_publish_targets,
    sanitize_body_links,
    upload_thumbnail,
    wordpress_media_endpoint,
)


BASE_DIR = Path(__file__).resolve().parent
LOCAL_PROFILE_BASE = Path(os.getenv("LOCALAPPDATA") or os.getenv("TEMP") or BASE_DIR) / "AutoBlog"
DEFAULT_PROFILE_DIR = str(LOCAL_PROFILE_BASE / "tistory_browser_profile")
DEFAULT_WRITE_URL = "https://{target_url}/manage/newpost/"


def tistory_category_label(item: DraftItem, target_url: str) -> str:
    """Choose a visible Tistory category from the article's subject."""
    host = normalized_host(target_url)
    if host != "seaga.tistory.com":
        return ""

    subject = f"{item.keyword} {item.category} {item.tistory_title or item.title}".lower()
    if "정책" in item.category:
        return "관공서 제도 혜택 관련"
    if "꿈" in item.category:
        return "꿈 해몽 전문"
    if "웰빙" in item.category or any(word in subject for word in ["건강", "음식", "영양", "다이어트"]):
        return "건강 음식 관련"
    if "신장" in item.category:
        return "이겨낼 불안 · 공황" if any(word in subject for word in ["불안", "공황"]) else "지켜볼 콩팥"
    if any(word in subject for word in ["여행", "관광", "숙소", "항공", "호텔"]):
        return "여행"
    return "도움 되는 정보"


def finwiz_category_candidates(item: DraftItem) -> list[str]:
    """finwiz 글에 어울리는 티스토리 카테고리를 우선순위대로 돌려준다.

    티스토리에 없는 이름은 select_tistory_category 가 알아서 건너뛴다.
    나중에 카테고리를 새로 만들면 코드를 고치지 않아도 그쪽으로 붙는다.
    """
    글 = f"{item.keyword} {item.tistory_title or item.title}"

    def 있나(*말들: str) -> bool:
        return any(말 in 글 for 말 in 말들)

    후보: list[str] = []
    if 있나("자동차", "차량", "중고차", "오토론", "할부"):
        후보 += ["자동차대출"]
    if 있나("무직자", "백수", "직업 없", "무소득", "소득없음", "소득 없"):
        후보 += ["무직자대출"]
    if 있나("소액", "비상금", "100만", "300만", "500만", "30만", "50만",
             "당일대출", "당일 대출", "즉시대출", "비대면", "캐피탈", "대부"):
        후보 += ["소액대출"]
    if 있나("전세", "임차", "보증금", "버팀목"):
        후보 += ["전세대출", "청년전세대출"]
    if 있나("청년", "사회초년생", "대학생"):
        후보 += ["청년대출", "청년 주거지원"]
    if 있나("신혼", "신생아", "출산"):
        후보 += ["신혼부부대출"]
    if 있나("디딤돌", "보금자리", "주택담보", "주담대", "생애최초", "주택구입"):
        후보 += ["주택담보대출", "주택구입대출"]
    if 있나("햇살론", "정책", "서민금융", "정부지원", "미소금융", "새희망",
             "생활안정", "근로복지", "장학재단", "불법사금융", "중금리", "지원제도"):
        후보 += ["정부지원대출", "서민금융"]
    if 있나("DSR", "dsr", "신용점수", "신용등급", "대환", "갈아타기",
             "금리인하", "대출금리", "금리 비교", "연소득", "원천징수",
             "신용불량", "신용조회", "연체"):
        후보 += ["신용대출", "신용관리"]
    if 있나("사업자", "소상공인", "직장인", "프리랜서"):
        후보 += ["직장인·사업자대출", "사업자대출"]

    # 끝까지 못 고르면 넓은 이름만 시도한다.
    # 여기에 '소액대출' 같은 실제 카테고리를 넣으면 안 맞는 글까지 전부
    # 그 하나로 몰린다(실제로 167편 중 146편이 그렇게 몰렸다).
    # 아무것도 못 고르면 '카테고리 없음' 인 채로 두는 편이 낫다.
    후보 += ["대출정보", "대출"]

    본것: set[str] = set()
    정리: list[str] = []
    for 이름 in 후보:
        if 이름 in 본것:
            continue
        본것.add(이름)
        정리.append(이름)
    return 정리


def tistory_category_candidates(
    item: DraftItem, target_url: str, target_config: dict[str, Any]
) -> list[str]:
    """설정에 적힌 카테고리를 먼저 쓰고, 없으면 주제로 고른다."""
    설정 = configured_tistory_category_label(item, target_url, target_config)
    if 설정:
        return [설정]
    if normalized_host(target_url) == "finwiz.tistory.com":
        return finwiz_category_candidates(item)
    return []


def configured_tistory_category_label(item: DraftItem, target_url: str, target_config: dict[str, Any]) -> str:
    """Use an explicitly configured category first, then the topic-based default."""
    target_host = normalized_host(target_url)
    candidates: list[dict[str, Any]] = []
    primary = target_config.get("categories", {}).get(item.category, {})
    if isinstance(primary, dict):
        candidates.append(primary)
    extras = target_config.get("extra_categories", {}).get(item.category, [])
    if isinstance(extras, list):
        candidates.extend(extra for extra in extras if isinstance(extra, dict))

    for candidate in candidates:
        if normalized_host(normalize_cell(candidate.get("url", ""))) != target_host:
            continue
        label = normalize_cell(candidate.get("tistory_category", ""))
        if label:
            return label
    return tistory_category_label(item, target_url)


def bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "y"}


def resolve_profile_path(profile_dir: str) -> Path:
    """OneDrive 보호 폴더 대신 로컬 앱 데이터에 티스토리 로그인 정보를 보관합니다."""
    profile_path = Path(profile_dir)
    if profile_path.is_absolute():
        return profile_path
    return LOCAL_PROFILE_BASE / profile_path.name


def stale_browser_pids(profile_path: Path) -> list[int]:
    """이 프로필 폴더를 붙잡고 있는 크롬이 아직 살아 있는지 본다.

    지난번 실행에서 창을 닫지 않았거나 비정상 종료되면 그 크롬이 프로필을
    잠근 채 남는다. 그 상태로 다시 켜면 새 크롬이 "기존 브라우저 세션에서
    열고 있습니다" 하고 그냥 꺼져 버린다.
    """
    if os.name != "nt":
        return []
    marker = profile_path.name
    command = (
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{marker}*' }} | "
        "Select-Object -ExpandProperty ProcessId"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception:
        return []
    pids = []
    for line in completed.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids


def close_stale_browser(profile_path: Path) -> int:
    pids = stale_browser_pids(profile_path)
    for pid in pids:
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=20,
            )
        except Exception:
            logging.warning("남아 있는 브라우저를 닫지 못했습니다: pid=%s", pid)
    return len(pids)


def launch_browser_context(playwright: Any, profile_path: Path, headless: bool):
    """브라우저를 켠다. 프로필이 잠겨 있으면 한 번 풀고 다시 시도한다."""
    def _launch():
        return playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_path),
            headless=headless,
            viewport={"width": 1400, "height": 950},
            args=["--disable-blink-features=AutomationControlled"],
        )

    남은수 = close_stale_browser(profile_path)
    if 남은수:
        logging.info("지난번에 안 닫힌 브라우저 %s개를 닫았습니다.", 남은수)
        time.sleep(2)

    try:
        return _launch()
    except Exception as exc:
        logging.warning("브라우저를 켜지 못했습니다. 프로필이 잠겨 있는지 확인합니다: %s", exc)
        남은수 = close_stale_browser(profile_path)
        if 남은수:
            logging.info("붙잡고 있던 브라우저 %s개를 닫았습니다. 다시 켜 봅니다.", 남은수)
            time.sleep(3)
            return _launch()
        logging.error(
            "\n"
            "브라우저를 켤 수 없습니다. 아래를 순서대로 해 보세요.\n"
            "  1) 화면에 열려 있는 크롬 창을 전부 닫습니다\n"
            "  2) 컴퓨터를 다시 시작합니다\n"
            "  3) 그래도 안 되면 이 폴더를 통째로 지우고 다시 로그인합니다\n"
            "     %s\n"
            "     (지우면 티스토리 로그인만 다시 하면 됩니다. 글은 안 지워집니다)\n",
            profile_path,
        )
        raise


def insert_wordpress_detail_link(content_html: str, wordpress_detail_url: str) -> str:
    """Place the WordPress follow-up link before the closing summary."""
    detail_url = html_module.escape(wordpress_detail_url, quote=True)
    callout = (
        "<blockquote class=\"more-guide\"><p>여기까지 읽어도 궁금한 부분이 남는다면 "
        f"<a href=\"{detail_url}\" target=\"_blank\" rel=\"noopener\">자세히 보기</a>에서 "
        "조금 더 구체적인 내용을 확인해 보세요.</p></blockquote>"
    )
    match = re.search(r"<h2[^>]*>\s*최종\s*정리하면,?\s*</h2>", content_html, flags=re.IGNORECASE)
    if match:
        return content_html[: match.start()] + callout + "\n" + content_html[match.start() :]

    headings = list(re.finditer(r"</h2>", content_html, flags=re.IGNORECASE))
    if len(headings) >= 2:
        insert_at = headings[len(headings) // 2].end()
        return content_html[:insert_at] + "\n" + callout + content_html[insert_at:]
    return content_html + "\n" + callout


def build_tistory_content(
    item: DraftItem,
    target_url: str,
    ads_config_path: str,
    wordpress_detail_url: str = "",
) -> tuple[str, list[str], Path | None]:
    tistory_meta = item.tistory_meta or item.meta
    tistory_html = item.tistory_html or item.html
    meta_description, tag_names = parse_meta_description_and_tags(tistory_meta, item.keyword, item.category)
    content_html = sanitize_body_links(tistory_html)
    # Tistory has no separate REST field for this generated SEO description.
    # Do not print it above the article: the generated opening already serves
    # as the reader-facing introduction and duplicating both hurts readability.
    if False and wordpress_detail_url:
        detail_url = html_module.escape(wordpress_detail_url, quote=True)
        content_html += (
            "\n<p><a href=\""
            f"{detail_url}\" target=\"_blank\" rel=\"noopener\">자세히 보기</a></p>"
        )
    if wordpress_detail_url:
        content_html = insert_wordpress_detail_link(content_html, wordpress_detail_url)
    content_html = insert_manual_ads(content_html, target_url, ads_config_path)
    thumbnail_path = None
    try:
        thumbnail_item = replace(item, title=item.tistory_title or item.title)
        thumbnail_path = generate_thumbnail(thumbnail_item)
    except Exception as exc:
        logging.warning("티스토리 썸네일 생성 실패, 글 임시저장은 계속합니다: %s", exc)
    return content_html, tag_names, thumbnail_path


def upload_tistory_thumbnail_via_wordpress(
    item: DraftItem,
    thumbnail_path: Path | None,
    target_config: dict[str, Any],
) -> str:
    """Host the Tistory-specific WebP on its paired WordPress site as a reliable fallback."""
    if not thumbnail_path or not thumbnail_path.exists():
        return ""

    targets = resolve_publish_targets(item.category, target_config, "", "", "", None, DEFAULT_ADS_CONFIG_FILE)
    wordpress_target = next(
        (
            target
            for target in targets
            if target.platform == "wordpress" and target.url and target.username and target.app_password
        ),
        None,
    )
    if wordpress_target:
        wp_url = wordpress_target.url
        username = wordpress_target.username
        app_password = wordpress_target.app_password
    else:
        # Tistory-only categories (loan and dream interpretation) still need a
        # public image URL. Use the shared WordPress media host configured in
        # publish_targets.json instead of silently dropping their thumbnail.
        host_config = target_config.get("tistory_image_host", {})
        if not isinstance(host_config, dict):
            return ""
        wp_url = normalize_cell(host_config.get("url", ""))
        username = normalize_cell(host_config.get("username", ""))
        app_password = normalize_cell(host_config.get("app_password", ""))
        username_env = normalize_cell(host_config.get("username_env", ""))
        password_env = normalize_cell(host_config.get("app_password_env", ""))
        if username_env:
            username = normalize_cell(os.getenv(username_env, username))
        if password_env:
            app_password = normalize_cell(os.getenv(password_env, app_password))
        if not wp_url or not username or not app_password:
            logging.warning("Tistory shared image host is not configured for %s", item.category)
            return ""

    if not wp_url or not username or not app_password:
        return ""

    media_id = upload_thumbnail(
        wp_url,
        username,
        app_password,
        thumbnail_path,
        item.tistory_title or item.title or item.keyword,
    )
    if not media_id:
        return ""

    try:
        response = requests.get(
            f"{wordpress_media_endpoint(wp_url)}/{media_id}",
            auth=(username, app_password),
            timeout=30,
        )
        response.raise_for_status()
        image_url = normalize_cell(response.json().get("source_url", ""))
        if image_url:
            logging.info("Tistory thumbnail hosted on WordPress media: %s", image_url)
        return image_url
    except Exception as exc:
        logging.warning("WordPress media URL lookup failed for Tistory thumbnail: %s", exc)
        return ""


def tistory_write_url(blog_name: str, target_url: str) -> str:
    configured = normalize_cell(os.getenv("TISTORY_BROWSER_WRITE_URL", ""))
    if configured:
        return configured.format(blog_name=blog_name, target_url=target_url)
    return DEFAULT_WRITE_URL.format(target_url=target_url.strip().removeprefix("https://").removeprefix("http://").strip("/"))


def normalized_host(url: str) -> str:
    value = normalize_cell(url)
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    return urlparse(value).netloc.lower()


def wordpress_detail_link(item: DraftItem, target_config: dict[str, Any]) -> str:
    """결과 URL 중 이 카테고리의 워드프레스 초안 주소만 골라냅니다."""
    targets = resolve_publish_targets(item.category, target_config, "", "", "", None, DEFAULT_ADS_CONFIG_FILE)
    wordpress_hosts = {
        normalized_host(target.url)
        for target in targets
        if target.platform == "wordpress" and normalized_host(target.url)
    }
    if not wordpress_hosts:
        return ""

    for candidate in item.result_link.splitlines():
        candidate = normalize_cell(candidate)
        if candidate.startswith(("http://", "https://")) and normalized_host(candidate) in wordpress_hosts:
            return candidate
    return ""


def visible_text_locator(page: Any, texts: list[str]):
    for text in texts:
        locator = page.get_by_text(text, exact=False)
        try:
            if locator.count() > 0:
                return locator.first
        except Exception:
            continue
    return None


def wait_for_editor_or_login(page: Any, write_url: str = "") -> None:
    title_selectors = [
        "textarea[placeholder*='제목']",
        "input[placeholder*='제목']",
        "#post-title-inp",
        "textarea",
    ]
    for selector in title_selectors:
        try:
            page.locator(selector).first.wait_for(timeout=5000)
            return
        except Exception:
            pass

    # 여기까지 왔다는 것은 글쓰기 화면이 아니라는 뜻이다.
    # 로그인이 풀리면 티스토리가 /manage/newpost/ 를 블로그 첫 화면으로 되돌린다.
    #
    # 예전에는 Enter 를 기다렸는데, 미리 눌린 줄바꿈이 남아 있으면 input() 이
    # 곧바로 끝나 버려서 "Enter 를 눌러도 그대로"인 것처럼 보였다.
    # 그래서 Enter 를 기다리지 않고, 글쓰기 화면이 나타날 때까지 지켜본다.
    지금주소 = ""
    try:
        지금주소 = page.url
    except Exception:
        pass

    기다릴초 = float(os.getenv("TISTORY_BROWSER_LOGIN_WAIT_SECONDS", "300"))
    print("\n" + "=" * 58)
    print("티스토리 로그인이 필요합니다.")
    print("지금 열려 있는 이 브라우저 창에서 아래를 해 주세요.")
    print("  1) 오른쪽 위에서 티스토리(카카오)로 로그인")
    print("  2) 카카오 로그인과 본인 확인을 끝까지 완료하세요.")
    print("  3) 로그인 뒤에는 창을 그대로 두세요. 글쓰기 화면은 알아서 엽니다.")
    print(f"  (지금 브라우저 주소: {지금주소 or '알 수 없음'})")
    if write_url:
        print(f"  (로그인만 하시면 됩니다. 주소 이동은 이쪽에서 다시 시도합니다: {write_url})")
    print(f"  최대 {int(기다릴초)}초 기다립니다. Enter 를 누를 필요는 없습니다.")
    print("=" * 58)

    # 카카오 인증 중에 글쓰기 주소로 다시 가면 인증 과정이 초기화된다.
    # 따라서 로그인 화면에서는 절대 이동하지 않고, 인증이 끝난 뒤에만 한 번
    # 글쓰기 주소를 연다.
    마감 = time.time() + 기다릴초
    로그인화면을봤나 = False
    처음재시도했나 = False
    로그인뒤이동했나 = False

    def 로그인화면인가(주소: str) -> bool:
        주소 = (주소 or "").lower()
        return (
            "accounts.kakao.com" in 주소
            or "/auth/login" in 주소
            or "tistory.com/auth" in 주소
        )

    while time.time() < 마감:
        for selector in title_selectors:
            try:
                page.locator(selector).first.wait_for(timeout=1000)
                logging.info("티스토리 글쓰기 화면을 확인했습니다. 이어서 진행합니다.")
                return
            except Exception:
                continue

        try:
            현재주소 = page.url
        except Exception:
            현재주소 = ""

        if 로그인화면인가(현재주소):
            로그인화면을봤나 = True
            page.wait_for_timeout(750)
            continue

        # 처음에는 로그인 여부를 확인하기 위해 한 번만 글쓰기 화면을 연다.
        # 이후 카카오 인증 화면을 거쳤다면, 로그인 완료 후 다시 한 번만 연다.
        이동할때 = (
            write_url
            and not 처음재시도했나
            and not 로그인화면을봤나
        ) or (
            write_url
            and 로그인화면을봤나
            and not 로그인뒤이동했나
        )
        if 이동할때:
            if 로그인화면을봤나:
                로그인뒤이동했나 = True
                logging.info("티스토리 로그인 완료를 확인해 글쓰기 화면으로 이동합니다.")
            else:
                처음재시도했나 = True
            try:
                page.goto(write_url, wait_until="domcontentloaded", timeout=20000)
            except Exception:
                pass
        page.wait_for_timeout(750)
    raise RuntimeError(
        "티스토리 글쓰기 제목 입력칸을 찾지 못했습니다. "
        "이 브라우저 프로필의 티스토리 로그인이 풀린 것 같습니다."
    )


def fill_title(page: Any, title: str) -> None:
    selectors = [
        "textarea[placeholder*='제목']",
        "input[placeholder*='제목']",
        "[name='title']",
        ".post-title textarea",
        ".editor-title textarea",
        "textarea[placeholder*='제목']",
        "input[placeholder*='제목']",
        "#post-title-inp",
        "textarea",
    ]
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            locator.wait_for(timeout=3000)
            locator.fill(title)
            saved_title = (locator.input_value(timeout=2000) or "").strip()
            if saved_title == title.strip():
                logging.info("티스토리 제목 입력 완료")
                return
        except Exception:
            continue
    raise RuntimeError("티스토리 제목 입력칸을 찾지 못했습니다.")


def select_tistory_category(page: Any, category_label: Any) -> bool:
    """카테고리를 고른다.

    후보를 여러 개 받아 목록에 실제로 있는 첫 번째 것을 고른다.
    하나도 없으면 '카테고리 없음' 인 채로 두고 넘어간다. 여기서 멈추지 않는다.
    """
    후보들 = [category_label] if isinstance(category_label, str) else list(category_label or [])
    후보들 = [str(이름).strip() for 이름 in 후보들 if str(이름).strip()]
    if not 후보들:
        return False

    try:
        opened = page.locator("button, [role='button'], div, select, label").evaluate_all(
            """
            (elements) => {
                const visible = (element) => {
                    const rect = element.getBoundingClientRect();
                    return rect.width > 8 && rect.height > 8 && rect.bottom > 0;
                };
                const control = elements
                    .filter(visible)
                    .map((element) => {
                        const marker = [
                            element.innerText,
                            element.getAttribute('aria-label'),
                            element.getAttribute('title'),
                            element.className,
                            element.id,
                        ].join(' ').toLowerCase();
                        const score = (/카테고리|category/.test(marker) ? 20 : 0)
                            + (element.tagName === 'SELECT' ? 10 : 0)
                            + (element.tagName === 'BUTTON' ? 3 : 0);
                        return { element, score };
                    })
                    .filter((item) => item.score > 0)
                    .sort((a, b) => b.score - a.score)[0];
                if (!control) return false;
                control.element.click();
                return true;
            }
            """
        )
        if not opened:
            logging.warning("티스토리 카테고리 선택 상자를 찾지 못했습니다.")
            return False
        page.wait_for_timeout(500)
        for 이름 in 후보들:
            try:
                option = page.get_by_text(이름, exact=True).last
                option.wait_for(state="visible", timeout=1200)
                option.click(timeout=2000)
                logging.info("티스토리 카테고리 선택 완료: %s", 이름)
                return True
            except Exception:
                continue

        보이는것 = []
        try:
            보이는것 = page.locator("li, [role='option'], a, button").evaluate_all(
                """
                (elements) => elements
                    .filter((element) => {
                        const rect = element.getBoundingClientRect();
                        return rect.width > 10 && rect.height > 5 && rect.bottom > 0;
                    })
                    .map((element) => element.textContent.trim())
                    .filter((text) => text && text.length < 30)
                    .slice(0, 25)
                """
            )
        except Exception:
            pass
        logging.warning(
            "티스토리 카테고리를 고르지 못했습니다. 찾던 이름: %s / 목록에 보이는 것: %s",
            후보들, 보이는것)
        return False
    except Exception as exc:
        logging.warning("티스토리 카테고리 선택 실패(%s): %s", 후보들, exc)
        return False


def set_editor_html(page: Any, html: str) -> str:
    """Write only to Tistory's actual post editor and prove that it kept the text."""
    expected_text = re.sub(r"<[^>]+>", " ", html)
    expected_text = html_module.unescape(re.sub(r"\s+", " ", expected_text)).strip()
    minimum_length = min(80, max(20, len(expected_text) // 8))

    # Tistory's classic editor uses a TinyMCE iframe. Restricting the target to
    # known editor frames prevents a hidden settings or tag field from being used.
    iframe_selectors = [
        "iframe#tinymce_ifr",
        "iframe[id$='_ifr']",
        ".tox-edit-area iframe",
        ".mce-edit-area iframe",
        "iframe[title*='Rich Text Area']",
    ]
    for selector in iframe_selectors:
        try:
            body = page.frame_locator(selector).locator("body")
            body.wait_for(state="visible", timeout=5000)
            body.click(timeout=3000)
            body.evaluate(
                """
                (element, value) => {
                    element.innerHTML = value;
                    element.dispatchEvent(new InputEvent('input', {
                        bubbles: true, inputType: 'insertText', data: null
                    }));
                    element.dispatchEvent(new Event('change', { bubbles: true }));
                    element.dispatchEvent(new Event('blur', { bubbles: true }));
                }
                """,
                html,
            )
            body_text = body.inner_text(timeout=3000).strip()
            if len(body_text) >= minimum_length:
                return f"iframe:{selector}"
        except Exception:
            continue

    # Some versions expose TinyMCE only through its JavaScript API. Use the
    # visible editor container rather than activeEditor, which may be a hidden one.
    for _ in range(20):
        result = page.evaluate(
            """
            (html) => {
                function visible(el) {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 10 && rect.height > 10 && style.visibility !== 'hidden' && style.display !== 'none';
                }
                function fireInput(el) {
                    el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: null }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new Event('blur', { bubbles: true }));
                }
                function result(kind, text) {
                    return { kind, length: (text || '').trim().length };
                }

                const editors = window.tinymce && Array.isArray(window.tinymce.editors)
                    ? window.tinymce.editors : [];
                const tiny = editors.find((editor) => {
                    const container = editor.getContainer && editor.getContainer();
                    return container && visible(container) && editor.getBody && editor.getBody();
                });
                if (tiny) {
                    tiny.focus();
                    tiny.setContent(html, { format: 'html' });
                    tiny.setDirty(true);
                    tiny.fire('input');
                    tiny.fire('change');
                    tiny.save();
                    return result('tinymce', tiny.getContent({ format: 'text' }));
                }

                return null;
            }
            """,
            html,
        )
        if result and int(result.get("length", 0)) >= minimum_length:
            return str(result["kind"])
        page.wait_for_timeout(750)

    raise RuntimeError(
        "티스토리 본문 편집기에 글이 실제로 입력되었는지 확인하지 못해 임시저장을 중단했습니다."
    )


def fill_tags(page: Any, tags: list[str]) -> None:
    if not tags:
        return
    selectors = [
        "input[placeholder*='태그']",
        "input[aria-label*='태그']",
        ".tag input",
        "input[type='text']",
    ]
    for selector in selectors:
        locator = page.locator(selector).last
        try:
            locator.wait_for(timeout=2000)
            for tag in tags[:10]:
                tag = re.sub(r"\s+", " ", tag).strip()
                if not tag:
                    continue
                locator.fill(tag)
                locator.press("Enter")
                time.sleep(0.15)
            return
        except Exception:
            continue
    logging.warning("티스토리 태그 입력칸을 찾지 못해 태그 입력을 건너뜁니다.")


def upload_to_tistory_file_input(page: Any, image_path: Path) -> bool:
    """Try every page/frame file input because Tistory changes editor shells often."""
    scopes = [page, *page.frames]
    for scope in scopes:
        for selector in ["input[type='file'][accept*='image']", "input[type='file']"]:
            try:
                inputs = scope.locator(selector)
                for index in range(inputs.count()):
                    inputs.nth(index).set_input_files(str(image_path), timeout=5000)
                    page.wait_for_timeout(4000)
                    logging.info("Tistory image uploaded through file input: %s", image_path.name)
                    return True
            except Exception:
                continue
    return False


def open_tistory_image_menu(page: Any) -> bool:
    """Open the toolbar image menu even when its icon has no accessible label."""
    try:
        return bool(page.locator("button, [role='button'], a, label, span, div").evaluate_all(
            """
            (elements) => {
                const matcher = /image|photo|picture|attach|media|upload|file|\\uc0ac\\uc9c4|\\uc774\\ubbf8\\uc9c0|\\ucca8\\ubd80/i;
                const visible = (element) => {
                    const rect = element.getBoundingClientRect();
                    return rect.width > 12 && rect.height > 12 && rect.top >= 0 && rect.top < 300;
                };
                const score = (element) => {
                    const text = [element.innerText, element.getAttribute('aria-label'), element.getAttribute('title'), element.className, element.outerHTML].join(' ');
                    if (!matcher.test(text)) return -1;
                    let value = 1;
                    if (/image|photo|picture|\\uc0ac\\uc9c4|\\uc774\\ubbf8\\uc9c0/i.test(text)) value += 10;
                    if (element.tagName === 'BUTTON') value += 3;
                    return value;
                };
                const control = elements.filter(visible).map(element => ({element, score: score(element)}))
                    .filter(item => item.score >= 0).sort((a, b) => b.score - a.score)[0];
                if (!control) return false;
                control.element.click();
                return true;
            }
            """
        ))
    except Exception:
        return False


def try_upload_thumbnail(page: Any, image_path: Path | None) -> bool:
    if not image_path or not image_path.exists():
        return False
    if not bool_env("TISTORY_BROWSER_UPLOAD_IMAGE", True):
        return False

    # Tistory can keep the input in the page, inside TinyMCE, or create it only
    # after the toolbar menu opens. Check those routes before older fallbacks.
    if upload_to_tistory_file_input(page, image_path):
        return True
    if open_tistory_image_menu(page):
        page.wait_for_timeout(1200)
        if upload_to_tistory_file_input(page, image_path):
            return True

    # Tistory commonly keeps an image input in the page even while it is hidden.
    # Direct assignment is more stable than depending on a toolbar caption.
    for selector in ["input[type='file'][accept*='image']", "input[type='file']"]:
        try:
            upload_input = page.locator(selector).first
            upload_input.set_input_files(str(image_path), timeout=5000)
            page.wait_for_timeout(4000)
            logging.info("티스토리 본문 이미지 업로드 완료: %s", image_path.name)
            return True
        except Exception:
            continue

    # On some Tistory editors the picture icon opens a small menu first. Its
    # visible label can be absent, so inspect the icon markup as well.
    try:
        opened = page.locator("button, [role='button']").evaluate_all(
            """
            (controls) => {
                const matcher = /image|photo|picture|attach|media|upload|사진|이미지|첨부/i;
                const control = controls.find((element) => {
                    const rect = element.getBoundingClientRect();
                    return rect.width > 8 && rect.height > 8 && matcher.test(element.outerHTML);
                });
                if (!control) return false;
                control.click();
                return true;
            }
            """
        )
        if opened:
            page.wait_for_timeout(1000)
            for selector in ["input[type='file'][accept*='image']", "input[type='file']"]:
                try:
                    upload_input = page.locator(selector).last
                    upload_input.set_input_files(str(image_path), timeout=5000)
                    page.wait_for_timeout(4000)
                    logging.info("티스토리 본문 이미지 업로드 완료: %s", image_path.name)
                    return True
                except Exception:
                    continue
    except Exception:
        pass

    image_control_selectors = [
        "button[aria-label*='사진']",
        "button[aria-label*='이미지']",
        "button[title*='사진']",
        "button[title*='이미지']",
        "[data-command='image']",
        "[data-command='attach']",
        "[data-type='image']",
        "button[class*='image']",
        "button[class*='photo']",
        "button[class*='attach']",
    ]
    for selector in image_control_selectors:
        try:
            control = page.locator(selector).first
            control.wait_for(state="visible", timeout=2000)
            with page.expect_file_chooser(timeout=4000) as file_chooser_info:
                control.click(timeout=2000)
            file_chooser_info.value.set_files(str(image_path))
            page.wait_for_timeout(4000)
            logging.info("티스토리 본문 이미지 업로드 완료: %s", image_path.name)
            return True
        except Exception:
            continue

    image_buttons = ["사진", "이미지", "첨부"]
    for text in ["사진", "이미지", "첨부"]:
        try:
            with page.expect_file_chooser(timeout=4000) as file_chooser_info:
                page.get_by_text(text, exact=False).first.click(timeout=2000)
            file_chooser_info.value.set_files(str(image_path))
            page.wait_for_timeout(4000)
            logging.info("티스토리 본문 이미지 업로드 완료: %s", image_path.name)
            return True
        except Exception:
            continue

    for text in image_buttons:
        try:
            with page.expect_file_chooser(timeout=3000) as file_chooser_info:
                page.get_by_text(text, exact=False).first.click(timeout=2000)
            file_chooser_info.value.set_files(str(image_path))
            page.wait_for_timeout(3000)
            return True
        except Exception:
            continue
    logging.warning("티스토리 이미지 업로드 버튼을 찾지 못해 이미지는 건너뜁니다: %s", image_path)
    return False


def move_editor_caret_to_start(page: Any) -> None:
    """Place an image uploaded through the toolbar at the top of the post body."""
    for selector in [
        "iframe#tinymce_ifr",
        "iframe[id$='_ifr']",
        ".tox-edit-area iframe",
        ".mce-edit-area iframe",
    ]:
        try:
            body = page.frame_locator(selector).locator("body")
            body.click(timeout=2000)
            body.press("Control+Home", timeout=2000)
            return
        except Exception:
            continue


def set_tistory_body_image_alt(page: Any, alt_text: str) -> bool:
    """Set the article title as alt text on the first Tistory body image."""
    clean_alt = re.sub(r"\s+", " ", alt_text).strip()
    if not clean_alt:
        return False

    iframe_selectors = [
        "iframe#tinymce_ifr",
        "iframe[id$='_ifr']",
        ".tox-edit-area iframe",
        ".mce-edit-area iframe",
    ]
    for selector in iframe_selectors:
        try:
            body = page.frame_locator(selector).locator("body")
            body.wait_for(state="visible", timeout=3000)
            result = body.evaluate(
                """
                (element, altText) => {
                    const images = Array.from(element.querySelectorAll('img'));
                    const image = images[0];
                    if (!image) return false;
                    image.setAttribute('alt', altText);
                    image.setAttribute('title', altText);
                    element.dispatchEvent(new InputEvent('input', { bubbles: true }));
                    element.dispatchEvent(new Event('change', { bubbles: true }));
                    return image.getAttribute('alt') === altText;
                }
                """,
                clean_alt,
            )
            if result:
                logging.info("티스토리 첫 이미지 대체 텍스트 설정 완료: %s", clean_alt)
                return True
        except Exception:
            continue

    result = page.evaluate(
        """
        (altText) => {
            const editors = window.tinymce && Array.isArray(window.tinymce.editors)
                ? window.tinymce.editors : [];
            const editor = editors.find((candidate) => candidate.getBody && candidate.getBody());
            const image = editor && editor.getBody().querySelector('img');
            if (!image) return false;
            image.setAttribute('alt', altText);
            image.setAttribute('title', altText);
            editor.setDirty(true);
            editor.fire('change');
            editor.save();
            return image.getAttribute('alt') === altText;
        }
        """,
        clean_alt,
    )
    if result:
        logging.info("티스토리 첫 이미지 대체 텍스트 설정 완료: %s", clean_alt)
    return bool(result)


def open_tistory_publish_settings(page: Any) -> bool:
    """Open Tistory's publish settings without confirming publication."""
    def has_representative_image_control() -> bool:
        try:
            controls = page.get_by_text("대표이미지", exact=False)
            return any(controls.nth(index).is_visible(timeout=300) for index in range(controls.count()))
        except Exception:
            return False

    if has_representative_image_control():
        return True

    # The first "발행" button opens settings. The final confirmation button is
    # inside that settings panel, so this function never clicks it a second time.
    try:
        opened = page.locator("button, [role='button'], a, div[class*='publish'], div[class*='Publish']").evaluate_all(
            """
            (controls) => {
                const visible = (element) => {
                    const rect = element.getBoundingClientRect();
                    return rect.width > 20 && rect.height > 20 && rect.bottom > 0;
                };
                const publishMatcher = /\ubc1c\ud589|\uc644\ub8cc|publish/i;
                const candidates = controls
                    .filter((element) => visible(element))
                    .map((element) => {
                        const label = [
                            element.textContent,
                            element.getAttribute('aria-label'),
                            element.getAttribute('title'),
                            element.className,
                        ].join(' ').trim();
                        const rect = element.getBoundingClientRect();
                        const text = element.textContent.trim();
                        // '발행'이 있으면 그쪽을 먼저 누른다. 없을 때만 '완료'를 쓴다.
                        const exact = text === '\ubc1c\ud589' ? 100
                                    : text === '\uc644\ub8cc' ? 60 : 0;
                        const score = exact + (publishMatcher.test(label) ? 20 : 0)
                            + (rect.right > window.innerWidth * 0.6 ? 5 : 0);
                        return { element, score };
                    })
                    .filter((item) => item.score > 0)
                    .sort((a, b) => b.score - a.score);
                if (!candidates.length) return false;
                candidates[0].element.click();
                return true;
            }
            """
        )
        if opened:
            page.wait_for_timeout(1800)
            if has_representative_image_control():
                return True
    except Exception:
        pass

    # DOM click can be ignored by Tistory's UI shell. Retry with Playwright's
    # normal click on every visible publish control, while stopping as soon as
    # the settings panel appears. This deliberately never clicks a control
    # after the panel has opened.
    try:
        controls = page.locator("button, [role='button'], a, [class*='publish'], [class*='Publish']")
        for index in range(min(controls.count(), 120)):
            control = controls.nth(index)
            try:
                text = (control.inner_text(timeout=500) or "").strip()
                aria_label = (control.get_attribute("aria-label") or "").strip()
                title = (control.get_attribute("title") or "").strip()
                marker = f"{text} {aria_label} {title}".lower()
                if ("발행" not in marker and "완료" not in marker
                        and "publish" not in marker):
                    continue
                if not control.is_visible(timeout=500):
                    continue
                control.click(timeout=3000)
                page.wait_for_timeout(1500)
                if has_representative_image_control():
                    return True
            except Exception:
                continue
    except Exception:
        pass

    try:
        controls = page.locator("button, [role='button'], a").evaluate_all(
            """
            (elements) => elements
                .filter((element) => {
                    const rect = element.getBoundingClientRect();
                    return rect.width > 20 && rect.height > 20 && rect.bottom > 0;
                })
                .slice(0, 80)
                .map((element) => [
                    element.textContent.trim(),
                    element.getAttribute('aria-label') || '',
                    element.getAttribute('title') || '',
                    String(element.className || '').slice(0, 80),
                ].filter(Boolean).join(' | '))
                .filter(Boolean)
            """
        )
        logging.warning(
            "티스토리 발행/완료 버튼을 찾지 못했습니다. 보이는 제어 항목: %s", controls[:20])
    except Exception:
        pass
    return False


def request_manual_publish_settings(page: Any) -> bool:
    """One-click fallback for Tistory themes that hide the publish control from Playwright."""
    if not bool_env("TISTORY_BROWSER_MANUAL_REPRESENTATIVE_FALLBACK", True):
        return False

    print(
        "\n티스토리 대표이미지 설정 화면을 열어주세요.\n"
        "열린 브라우저에서 '발행'을 한 번 눌러 '대표이미지 추가'가 보이는 화면까지만 이동하세요.\n"
        "실제 발행을 확정하는 마지막 버튼은 누르지 말고, 화면이 열리면 Enter를 누르세요."
    )
    def 대표이미지화면이열렸나() -> bool:
        try:
            return bool(page.get_by_text("대표이미지", exact=False).count())
        except Exception:
            return False

    try:
        input("계속하려면 Enter: ")
    except EOFError:
        # 터미널이 Enter 를 못 받는 경우가 있다. 미리 눌린 줄바꿈이 남아 있거나
        # 파이프로 실행될 때 input() 이 곧바로 끝나 버린다. 그때는 사람이
        # 브라우저에서 화면을 열 때까지 기다린다.
        기다릴초 = float(os.getenv("TISTORY_BROWSER_MANUAL_WAIT_SECONDS", "180"))
        logging.info(
            "Enter 입력을 받지 못했습니다. 브라우저에서 대표이미지 화면을 여시면 "
            "자동으로 이어갑니다. (최대 %s초 대기)", int(기다릴초))
        마감 = time.time() + 기다릴초
        while time.time() < 마감:
            if 대표이미지화면이열렸나():
                logging.info("대표이미지 화면을 확인했습니다. 이어서 진행합니다.")
                return True
            page.wait_for_timeout(1000)
        logging.warning("대표이미지 화면이 열리지 않아 기다리기를 마칩니다.")
        return False
    return 대표이미지화면이열렸나()


def close_tistory_publish_settings(page: Any) -> None:
    """Return from the publish panel to the editor without confirming publication."""
    if not bool_env("TISTORY_BROWSER_SET_REPRESENTATIVE_IMAGE", True):
        return

    # The panel is normally dismissible with Escape. Wait because Tistory may
    # still be attaching the uploaded file when the chooser has just closed.
    for _ in range(2):
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(900)
        except Exception:
            pass

    try:
        drafts = page.get_by_text("임시저장", exact=False)
        if any(drafts.nth(index).is_visible(timeout=300) for index in range(drafts.count())):
            return
    except Exception:
        pass

    for selector in [
        "button[aria-label*='닫기']",
        "button[title*='닫기']",
        "button[class*='close']",
        "[role='button'][aria-label*='닫기']",
        "[class*='close']",
    ]:
        try:
            control = page.locator(selector).first
            if control.is_visible(timeout=700):
                control.click(timeout=2000)
                page.wait_for_timeout(900)
                break
        except Exception:
            continue


def set_tistory_representative_image(page: Any, image_path: Path | None) -> bool:
    """Upload the WebP in Tistory's publish panel as the post's representative image."""
    if not image_path or not image_path.exists():
        return False
    if not bool_env("TISTORY_BROWSER_SET_REPRESENTATIVE_IMAGE", True):
        return True
    if not open_tistory_publish_settings(page):
        logging.warning("티스토리 발행 설정에서 대표이미지 추가 버튼을 찾지 못했습니다.")
        if not request_manual_publish_settings(page):
            return False

    # Do not select the first file input on the page. Tistory also keeps an
    # editor attachment input there, which inserts into the body but does not
    # set the representative image.
    for text in ["대표이미지 추가", "대표 이미지 추가"]:
        try:
            buttons = page.get_by_text(text, exact=False)
            for index in range(buttons.count()):
                button = buttons.nth(index)
                if not button.is_visible(timeout=700):
                    continue
                try:
                    with page.expect_file_chooser(timeout=6000) as file_chooser_info:
                        button.click(timeout=3000)
                    file_chooser_info.value.set_files(str(image_path))
                    page.wait_for_timeout(3500)
                    logging.info("티스토리 대표이미지 업로드 완료: %s", image_path.name)
                    close_tistory_publish_settings(page)
                    return True
                except Exception:
                    continue
        except Exception:
            continue

    # Fallback only after the representative button was tried. Limit the
    # search to the visible publish panel to avoid the editor's image input.
    selectors = [
        "[class*='represent'] input[type='file']",
        "[class*='thumbnail'] input[type='file']",
        "[class*='publish'] input[type='file']",
    ]
    for selector in selectors:
        try:
            inputs = page.locator(selector)
            for index in range(inputs.count()):
                input_box = inputs.nth(index)
                input_box.set_input_files(str(image_path), timeout=6000)
                page.wait_for_timeout(3500)
                logging.info("티스토리 대표이미지 업로드 완료: %s", image_path.name)
                close_tistory_publish_settings(page)
                return True
        except Exception:
            continue

    logging.warning("티스토리 대표이미지 파일 선택기를 열지 못했습니다: %s", image_path)
    return False


# ══════════════════════════════════════════════════════════
#  홈주제 (발행 설정 안의 '홈주제')
#
#  티스토리 홈에서 이 글이 어느 분류로 노출될지 정한다.
#  대출·정책금융 글은 '시사·지식 > 경제' 가 맞다.
#  '경영·직장' 은 직장인 대상이라 무직자 대출 글과 안 맞는다.
#
#  다른 값으로 하고 싶으면 .env 에 TISTORY_HOME_TOPIC=원하는이름
#  빈 값으로 두면 홈주제를 건드리지 않는다.
# ══════════════════════════════════════════════════════════

def tistory_home_topic() -> str:
    return os.getenv("TISTORY_HOME_TOPIC", "경제").strip()


def set_tistory_home_topic(page: Any, topic: str) -> bool:
    """발행 설정 안의 홈주제를 고른다. 실패해도 임시저장은 계속한다."""
    if not topic:
        return False

    def 지금값() -> str:
        try:
            return page.evaluate(
                """
                () => {
                    const labels = Array.from(document.querySelectorAll('*'))
                        .filter((element) => element.children.length === 0
                            && (element.textContent || '').trim() === '\ud648\uc8fc\uc81c');
                    for (const label of labels) {
                        const row = label.parentElement;
                        if (!row) continue;
                        const text = (row.textContent || '').replace('\ud648\uc8fc\uc81c', '').trim();
                        if (text) return text;
                    }
                    return '';
                }
                """
            ) or ""
        except Exception:
            return ""

    이미 = 지금값()
    if topic and topic in 이미:
        logging.info("티스토리 홈주제가 이미 '%s' 입니다.", topic)
        return True

    # 홈주제 옆의 선택 상자를 연다
    try:
        열림 = page.evaluate(
            """
            () => {
                const labels = Array.from(document.querySelectorAll('*'))
                    .filter((element) => element.children.length === 0
                        && (element.textContent || '').trim() === '\ud648\uc8fc\uc81c');
                for (const label of labels) {
                    const row = label.parentElement;
                    if (!row) continue;
                    const targets = Array.from(row.querySelectorAll('button, a, select, div, span'))
                        .filter((element) => {
                            const rect = element.getBoundingClientRect();
                            return rect.width > 20 && rect.height > 10
                                && (element.textContent || '').trim() !== '\ud648\uc8fc\uc81c';
                        });
                    if (targets.length) {
                        targets[0].click();
                        return true;
                    }
                }
                return false;
            }
            """
        )
    except Exception as exc:
        logging.warning("티스토리 홈주제 상자를 열지 못했습니다: %s", exc)
        return False

    if not 열림:
        보인것 = []
        try:
            보인것 = page.locator("*").evaluate_all(
                """
                (elements) => elements
                    .filter((el) => el.children.length === 0)
                    .map((el) => (el.textContent || '').trim())
                    .filter((t) => t && t.length <= 10)
                    .slice(0, 40)
                """
            )
        except Exception:
            pass
        logging.warning(
            "티스토리 홈주제 상자를 찾지 못했습니다. 화면에 보인 글자: %s", 보인것)
        return False
    page.wait_for_timeout(700)

    # 목록에서 고른다. 항목 앞에 '- ' 가 붙어 보일 수 있어 기호를 떼고 맞춘다.
    try:
        골랐나 = page.evaluate(
            r"""
            (topic) => {
                const clean = (text) => (text || '').replace(/[\s\u00b7\u2010-\u2015\-]/g, '');
                const target = clean(topic);
                const items = Array.from(
                    document.querySelectorAll("li, [role='option'], button, a, span, div"));
                for (const element of items) {
                    if (element.children.length > 1) continue;
                    if (clean(element.textContent) !== target) continue;
                    const rect = element.getBoundingClientRect();
                    if (rect.width < 10 || rect.height < 5) continue;
                    element.click();
                    return true;
                }
                return false;
            }
            """,
            topic,
        )
    except Exception as exc:
        logging.warning("티스토리 홈주제 '%s' 를 고르지 못했습니다: %s", topic, exc)
        return False

    page.wait_for_timeout(700)

    if not 골랐나:
        # 목록이 접혀 있거나 스크롤해야 보이는 경우가 있다. 한 번 더 훑는다.
        try:
            골랐나 = page.evaluate(
                r"""
                (topic) => {
                    const clean = (t) => (t || '').replace(/[\s\u00b7\u2010-\u2015\-]/g, '');
                    const target = clean(topic);
                    const items = Array.from(document.querySelectorAll("li, [role='option'], a, span, div"));
                    for (const el of items) {
                        if (el.children.length > 1) continue;
                        if (clean(el.textContent) !== target) continue;
                        el.scrollIntoView({block: 'center'});
                        el.click();
                        return true;
                    }
                    return false;
                }
                """,
                topic,
            )
            page.wait_for_timeout(700)
        except Exception:
            pass

    if not 골랐나:
        # 무엇이 보였는지 남긴다. 이게 없으면 다음에 무엇을 고쳐야 할지 알 수 없다.
        보인것 = []
        try:
            보인것 = page.locator("li, [role='option'], a, span, div").evaluate_all(
                """
                (elements) => elements
                    .filter((el) => {
                        const r = el.getBoundingClientRect();
                        return r.width > 10 && r.height > 5 && r.bottom > 0 && el.children.length <= 1;
                    })
                    .map((el) => (el.textContent || '').trim())
                    .filter((t) => t && t.length <= 12)
                    .slice(0, 40)
                """
            )
        except Exception:
            pass
        logging.warning(
            "티스토리 홈주제 목록에서 '%s' 를 찾지 못했습니다. 목록에 보인 것: %s",
            topic, 보인것)
        return False

    확인 = 지금값()
    if topic in 확인:
        logging.info("티스토리 홈주제 설정 완료: %s", topic)
        return True
    logging.warning("티스토리 홈주제를 눌렀지만 '%s' 로 바뀌지 않았습니다. 지금 값: %s", topic, 확인)
    return False


# ══════════════════════════════════════════════════════════
#  자동 공개 발행 (기본 꺼짐)
#
#  임시저장에서 멈추는 것이 안전장치다. 사람이 한 번 보고 발행한다.
#  다만 꿈해몽처럼 금액·금리 같은 사실이 없는 주제는 그 확인이 덜 필요하다.
#
#  .env 에서 켠다. 비워 두면(기본) 지금처럼 임시저장까지만 한다.
#      TISTORY_AUTO_PUBLISH_CATEGORIES=꿈해몽
#  쉼표로 여러 개도 된다. 대출관련을 여기에 넣지 마세요.
#
#  공개된 글은 되돌리기 어렵다. 검색엔진이 이미 가져갈 수 있다.
# ══════════════════════════════════════════════════════════

def auto_publish_categories() -> list[str]:
    값 = os.getenv("TISTORY_AUTO_PUBLISH_CATEGORIES", "").strip()
    return [부분.strip() for 부분 in 값.split(",") if 부분.strip()]


def should_auto_publish(category: str) -> bool:
    켠것 = auto_publish_categories()
    if not 켠것:
        return False
    이름 = str(category or "").strip()
    return any(하나 in 이름 for 하나 in 켠것)


def click_publish_now(page: Any) -> bool:
    """발행 설정 화면의 '공개 발행' 을 누른다. 못 찾으면 False."""
    if not open_tistory_publish_settings(page):
        logging.warning("발행 설정 화면을 열지 못해 자동 발행을 건너뜁니다.")
        return False

    for 글자 in ["공개 발행", "공개발행"]:
        try:
            단추 = page.get_by_text(글자, exact=False).last
            단추.wait_for(state="visible", timeout=2500)
            단추.click(timeout=3000)
            page.wait_for_timeout(3000)
            logging.info("티스토리 공개 발행을 눌렀습니다.")
            return True
        except Exception:
            continue

    logging.warning("'공개 발행' 단추를 찾지 못해 임시저장 상태로 둡니다.")
    return False


def click_draft_save(page: Any) -> None:
    close_tistory_publish_settings(page)
    candidates = ["임시저장", "임시 저장", "저장"]
    for text in candidates:
        try:
            button = page.get_by_text(text, exact=False).first
            button.wait_for(timeout=3000)
            button.click()
            page.wait_for_timeout(2500)
            return
        except Exception:
            continue
    raise RuntimeError("티스토리 임시저장 버튼을 찾지 못했습니다.")


def record_tistory_completion(spreadsheet, item: DraftItem, result: DraftResult) -> bool:
    """티스토리에 올린 글을 카테고리별 완료시트에 한 줄 남긴다.

    지금까지는 오늘작성 시트의 상태만 바꾸고 끝이었다. 그래서 티스토리 글은
    완료시트에 아예 안 쌓였고, 꿈해몽 지난 글 링크(dream_links)가 참고할
    목록도 비어 있었다. 워드프레스 쪽은 원래 이 기록을 남긴다.
    """
    sheet = ensure_completion_sheet(spreadsheet, item.category)
    링크 = result.link or ""
    if completion_has_post(sheet, 링크, result.post_id):
        logging.info("완료시트에 이미 있는 글이라 넘어갑니다: %s", item.title)
        return False
    상태 = PUBLISHED_STATUS if result.status == "published" else DONE_STATUS
    sheet.append_row(
        [
            item.keyword,
            item.category,
            item.tistory_title or item.title,
            링크,
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            상태,
            result.platform,
            result.post_id,
        ],
        value_input_option="USER_ENTERED",
    )
    logging.info("완료시트에 기록했습니다: [%s] %s", 상태, item.title)
    return True


def save_tistory_draft_with_browser(
    page: Any,
    item: DraftItem,
    blog_name: str,
    target_url: str,
    ads_config_path: str,
    target_config: dict[str, Any],
    wordpress_detail_url: str = "",
) -> DraftResult:
    write_url = tistory_write_url(blog_name, target_url)
    content_html, tags, thumbnail_path = build_tistory_content(
        item,
        target_url,
        ads_config_path,
        wordpress_detail_url,
    )
    hosted_thumbnail_url = upload_tistory_thumbnail_via_wordpress(item, thumbnail_path, target_config)

    page.goto(write_url, wait_until="domcontentloaded", timeout=60000)
    wait_for_editor_or_login(page, write_url)
    select_tistory_category(page, tistory_category_candidates(item, target_url, target_config))
    fill_title(page, item.tistory_title or item.title)
    editor_type = set_editor_html(page, content_html)
    logging.info("티스토리 본문 입력 완료: editor=%s", editor_type)
    move_editor_caret_to_start(page)
    fill_tags(page, tags)
    # Prefer Tistory's own image upload. It is needed for the separate
    # representative-image setting; the WordPress-hosted image remains a
    # fallback when the editor hides its upload control.
    image_inserted = try_upload_thumbnail(page, thumbnail_path)
    if not image_inserted and hosted_thumbnail_url:
        content_html = add_tistory_top_image(content_html, hosted_thumbnail_url, item.keyword)
        editor_type = set_editor_html(page, content_html)
        logging.info("티스토리 본문 이미지 보완 입력 완료: editor=%s", editor_type)
    if not image_inserted:
        image_inserted = bool(hosted_thumbnail_url)
    if not image_inserted:
        logging.warning("티스토리 본문 이미지가 삽입되지 않아 이미지 없이 임시저장을 중단합니다.")
        raise RuntimeError("티스토리 본문 이미지 삽입에 실패했습니다.")
    # 대체 텍스트는 키워드가 아니라 글 제목을 쓴다.
    # 키워드('무직자 소액대출')보다 제목이 이미지가 무엇인지 더 잘 설명하고,
    # 티스토리가 이 이미지를 썸네일로 쓸 때 그대로 노출된다.
    if not set_tistory_body_image_alt(page, item.tistory_title or item.title or item.keyword):
        raise RuntimeError("티스토리 본문 이미지의 대체 텍스트를 설정하지 못했습니다.")
    # Save the actual post content before opening the publish settings. Tistory
    # may keep that panel open after a representative image is selected, which
    # otherwise hides the draft button and wrongly reports a failed draft.
    click_draft_save(page)
    logging.info("티스토리 본문 임시저장 완료. 대표이미지 설정을 이어갑니다.")
    if not set_tistory_representative_image(page, thumbnail_path):
        raise RuntimeError("티스토리 대표이미지를 설정하지 못했습니다.")
    # 발행 설정 화면이 열려 있는 지금이 홈주제를 고를 자리다.
    # 실패해도 글은 그대로 임시저장한다. 홈주제는 나중에 손으로 바꿀 수 있다.
    set_tistory_home_topic(page, tistory_home_topic())
    # Representative-image selection changes the draft after the first save.
    # Save once more after closing the panel so the selection is persisted.
    click_draft_save(page)
    logging.info("티스토리 대표이미지를 포함해 임시저장 완료했습니다.")

    상태 = "draft"
    if should_auto_publish(item.category):
        logging.info("[%s] 자동 공개 발행 대상입니다.", item.category)
        if click_publish_now(page):
            상태 = "published"
        else:
            logging.warning("자동 발행에 실패했습니다. 임시저장 상태로 남습니다.")

    return DraftResult(post_id="", link=page.url, status=상태, platform="TistoryBrowser")


def run_pipeline(
    credentials_path: str,
    spreadsheet_id: str,
    publish_targets_path: str,
    limit: int,
    category_filter: str,
    profile_dir: str,
    headless: bool,
) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "playwright가 설치되어 있지 않습니다. 먼저 실행하세요: pip install playwright && python -m playwright install chromium"
        ) from exc

    sheet_client = gspread.service_account(filename=credentials_path)
    spreadsheet = sheet_client.open_by_key(spreadsheet_id)
    today_sheet = spreadsheet.worksheet(TODAY_SHEET)
    target_config = load_publish_target_config(publish_targets_path)

    items = collect_draft_items(today_sheet, limit)
    category_filters = [part.strip() for part in (category_filter or "").split(",") if part.strip()]
    selected: list[tuple[DraftItem, Any]] = []
    for item in items:
        targets = resolve_publish_targets(item.category, target_config, "", "", "", None, DEFAULT_ADS_CONFIG_FILE)
        for target in targets:
            if target.platform != "tistory":
                continue
            if category_filters and not any(part in item.category for part in category_filters):
                continue
            selected.append((item, target))

    logging.info("티스토리 브라우저 임시저장 대상 글 수: %s", len(selected))
    if not selected:
        return

    profile_path = resolve_profile_path(profile_dir)
    profile_path.mkdir(parents=True, exist_ok=True)
    logging.info("티스토리 브라우저 프로필 위치: %s", profile_path)

    with sync_playwright() as playwright:
        context = launch_browser_context(playwright, profile_path, headless)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            for item, target in selected:
                logging.info("티스토리 임시저장 시작: [%s] %s -> %s", item.category, item.title, target.url)
                try:
                    detail_url = wordpress_detail_link(item, target_config)
                    if detail_url:
                        logging.info("티스토리 자세히 보기 링크: %s", detail_url)
                    result = save_tistory_draft_with_browser(
                        page,
                        item,
                        target.tistory_blog_name,
                        target.url,
                        target.ads_config_path,
                        target_config,
                        detail_url,
                    )
                    mark_draft_saved(today_sheet, item, result)
                    try:
                        record_tistory_completion(spreadsheet, item, result)
                    except Exception as exc:
                        # 기록에 실패해도 글은 이미 올라갔다. 여기서 멈추지 않는다.
                        logging.warning("완료시트에 기록하지 못했습니다: %s", exc)
                    logging.info("티스토리 임시저장 완료: %s", result.link)
                except Exception as exc:
                    logging.exception("티스토리 임시저장 실패, 다음 항목으로 넘어갑니다: %s / %s", item.keyword, exc)
        finally:
            context.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tistory tokenless browser draft saver")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--spreadsheet-id", default=None)
    parser.add_argument("--credentials", default=None)
    parser.add_argument("--targets", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--category", default="최신이슈,정책지원,대출,신장정신,꿈해몽", help="Only process categories containing this text. Empty string = all Tistory targets.")
    parser.add_argument("--profile-dir", default=None)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    load_dotenv(args.env)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    credentials = args.credentials or os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
    spreadsheet_id = args.spreadsheet_id or os.getenv("SPREADSHEET_ID", SPREADSHEET_ID)
    targets = args.targets or os.getenv("PUBLISH_TARGETS_FILE", DEFAULT_PUBLISH_TARGETS_FILE)
    limit = args.limit or int(os.getenv("MAX_PUBLISH_ROWS", "3"))
    profile_dir = args.profile_dir or os.getenv("TISTORY_BROWSER_PROFILE_DIR", DEFAULT_PROFILE_DIR)
    headless = args.headless or bool_env("TISTORY_BROWSER_HEADLESS", False)

    run_pipeline(credentials, spreadsheet_id, targets, limit, args.category, profile_dir, headless)


if __name__ == "__main__":
    main()
