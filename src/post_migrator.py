"""A 워드프레스 글 → B 워드프레스 '업그레이드 발행' 파이프라인.

절차:
  1. A(원본) 사이트에서 글을 REST API 로 가져온다.
  2. 본문에 수동 삽입된 A 의 애드센스 코드를 전부 제거한다.
  3. Claude 로 오늘 날짜 기준 검색 트렌드에 맞게 본문·제목·메타를 업그레이드한다.
     (본문 중간에 [AD] 마커 3개를 심게 한다)
  4. [AD] 마커 자리에 B 도메인으로 승인받은 애드센스 코드를 삽입한다.
  5. 썸네일 이미지를 제작해 B 미디어 라이브러리에 올리고 대표이미지로 지정한다.
  6. B 에 발행한다. 기본은 draft(비공개) — 검수 후 공개.

중복 콘텐츠 주의: 이관 후 A 원본은 비공개 전환(--retire-source)하거나
A→B 301 리다이렉트를 걸어야 SEO 불이익이 없다.
"""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from urllib.parse import urlparse

import anthropic
import requests
from pydantic import BaseModel, Field

from . import config, thumbnail
from .config import Site
from .wordpress_publisher import markdown_to_html

MIGRATED_DIR = Path("drafts/migrated")
_TIMEOUT = 30
_AD_MARKER = "[AD]"

# ─────────────────────────────────────────────────────────────
# 1) 원본 글 가져오기
# ─────────────────────────────────────────────────────────────

def _api(site: Site, path: str) -> str:
    return f"{site.url.rstrip('/')}/wp-json/wp/v2/{path.lstrip('/')}"


def _auth(site: Site) -> tuple[str, str]:
    return (site.user, site.app_password)


def _require_configured(site: Site) -> None:
    if not site.configured:
        raise RuntimeError(
            f"사이트 '{site.key}'({site.name})의 .env 설정이 비어 있습니다. "
            f"WP_{site.env_prefix}_URL / _USER / _APP_PASSWORD 를 확인하세요."
        )


def _simplify_post(data: dict) -> dict:
    return {
        "id": data["id"],
        "title": data.get("title", {}).get("rendered", ""),
        "content": data.get("content", {}).get("rendered", ""),
        "excerpt": re.sub(r"<[^>]+>", "", data.get("excerpt", {}).get("rendered", "")).strip(),
        "slug": data.get("slug", ""),
        "link": data.get("link", ""),
        "date": data.get("date", ""),
        "status": data.get("status", ""),
    }


def fetch_post(
    site: Site,
    post_id: int | None = None,
    post_url: str | None = None,
    search: str | None = None,
) -> dict:
    """글 ID, URL(슬러그), 검색어 중 하나로 원본 글 1개를 가져온다."""
    _require_configured(site)

    if post_id:
        resp = requests.get(_api(site, f"posts/{post_id}"),
                            auth=_auth(site), timeout=_TIMEOUT)
        if resp.status_code != 200:
            raise RuntimeError(f"글 조회 실패 ({resp.status_code}): {resp.text[:300]}")
        return _simplify_post(resp.json())

    params: dict = {"per_page": 5}
    if post_url:
        slug = [s for s in urlparse(post_url).path.split("/") if s][-1]
        params["slug"] = slug
    elif search:
        params["search"] = search
    else:
        raise ValueError("post_id, post_url, search 중 하나는 필요합니다.")

    resp = requests.get(_api(site, "posts"), params=params,
                        auth=_auth(site), timeout=_TIMEOUT)
    if resp.status_code != 200:
        raise RuntimeError(f"글 조회 실패 ({resp.status_code}): {resp.text[:300]}")
    items = resp.json()
    if not items:
        raise RuntimeError("조건에 맞는 글을 찾지 못했습니다. --list 로 글 목록을 확인하세요.")
    return _simplify_post(items[0])


def list_recent_posts(site: Site, count: int = 20) -> list[dict]:
    """이관할 글을 고르기 위한 최근 글 목록."""
    _require_configured(site)
    resp = requests.get(
        _api(site, "posts"),
        params={"per_page": count, "orderby": "date", "order": "desc"},
        auth=_auth(site),
        timeout=_TIMEOUT,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"목록 조회 실패 ({resp.status_code}): {resp.text[:300]}")
    return [_simplify_post(p) for p in resp.json()]


# ─────────────────────────────────────────────────────────────
# 2) A 의 수동 애드센스 코드 제거
# ─────────────────────────────────────────────────────────────

_AD_PATTERNS = [
    # <ins class="adsbygoogle" ...> ... </ins>
    re.compile(r"<ins[^>]*adsbygoogle[^>]*>.*?</ins>", re.S | re.I),
    # 애드센스 로더 <script src=".../adsbygoogle.js"...></script>
    re.compile(r"<script[^>]*adsbygoogle\.js[^>]*>\s*</script>", re.S | re.I),
    # (adsbygoogle = window.adsbygoogle || []).push({}) 실행 스크립트
    re.compile(r"<script[^>]*>[^<]*adsbygoogle[^<]*</script>", re.S | re.I),
    # AMP 광고
    re.compile(r"<amp-ad[^>]*>.*?</amp-ad>", re.S | re.I),
]


def strip_adsense(html: str) -> tuple[str, int]:
    """본문에서 애드센스 관련 블록을 제거하고 (본문, 제거 개수) 를 돌려준다."""
    removed = 0
    for pat in _AD_PATTERNS:
        html, n = pat.subn("", html)
        removed += n
    # 광고 제거로 생긴 빈 문단 정리
    html = re.sub(r"<p>\s*</p>", "", html)
    return html.strip(), removed


# ─────────────────────────────────────────────────────────────
# 3) Claude 업그레이드
# ─────────────────────────────────────────────────────────────

class UpgradedPost(BaseModel):
    new_title: str = Field(description="새 제목(50~60자). 현재 연도/숫자/구체적 이득을 담아 "
                                       "검색결과에서 클릭하고 싶게. 과장·허위 금지.")
    slug: str = Field(description="새 URL 슬러그(영문 소문자-하이픈)")
    meta_description: str = Field(description="메타설명(120~155자, 핵심 키워드 포함)")
    body_markdown: str = Field(
        description="업그레이드된 본문(마크다운). 도입부 직후·본문 중간·결론 직전에 "
        "[AD] 마커를 정확히 3개, 각각 단독 줄로 넣는다."
    )
    thumbnail_title: str = Field(description="썸네일 큰 글씨(공백 포함 20자 이내, 필요시 \\n 줄바꿈)")
    thumbnail_subtitle: str = Field(description="썸네일 작은 글씨 한 줄(25자 이내)")
    tags: list[str] = Field(description="워드프레스 태그 3~6개")
    fact_check_items: list[str] = Field(
        description="발행 전 사람이 1차 출처로 확인해야 할 숫자·조건·날짜·정책 목록"
    )
    change_summary: list[str] = Field(description="원본 대비 무엇을 업그레이드했는지 요약 3~6개")


_SYSTEM = """당신은 한국어 SEO 콘텐츠 전문가이자 편집자다.
과거에 발행된 블로그 글을 받아, 오늘 날짜 기준으로 '업그레이드 버전'을 만든다.

규칙:
- 오늘 날짜 기준으로 낡은 내용(지난 연도 표기, 바뀌었을 법한 제도·금액·순위)을
  현재형으로 다시 쓴다. 단, 확신할 수 없는 최신 수치는 지어내지 말고
  fact_check_items 에 "확인 필요" 항목으로 남긴다.
- 지금 사람들이 많이 검색할 각도로 재구성한다: 최신 트렌드 반영, 자주 묻는
  질문(FAQ) 섹션 추가, 비교표·단계별 방법 등 스캔 가능한 구조.
- 제목은 검색결과에서 클릭을 부르는 형태(현재 연도, 숫자, 구체적 이득)로 새로
  짓되, 본문이 지키지 못할 약속(낚시)은 하지 않는다.
- 원본의 고유한 정보·경험은 버리지 말고 살려서 확장한다.
- 본문 마크다운에는 광고 위치 마커 [AD] 를 정확히 3개 넣는다:
  ① 도입부(첫 답변) 직후 ② 본문 중간 자연스러운 단락 사이 ③ 결론/FAQ 직전.
  마커는 반드시 앞뒤가 빈 줄인 단독 줄이어야 한다.
- YMYL(건강·금융·법률) 주제면 본문 끝에 면책 문구와 공식 출처 확인 권고를 넣는다.
- 이것은 '검수용 초안'이다. 사람이 fact_check_items 를 확인한 뒤 공개한다."""


def upgrade_post(
    old_title: str,
    old_html: str,
    target_site: Site,
    focus_keyword: str | None = None,
) -> UpgradedPost:
    client = anthropic.Anthropic(api_key=config.require_api_key())
    today = _dt.date.today()

    focus = f'\n- 새 글이 노려야 할 포커스 키워드: "{focus_keyword}"' if focus_keyword else ""
    prompt = (
        f"오늘 날짜: {today.isoformat()} ({today.year}년)\n"
        f"이관 대상 사이트 분야: {target_site.niche}{focus}\n\n"
        f"아래는 과거에 발행된 원본 글이다. 위 규칙대로 업그레이드 버전을 만들어라.\n\n"
        f"[원본 제목]\n{old_title}\n\n"
        f"[원본 본문 HTML]\n{old_html[:60000]}"
    )

    response = client.messages.parse(
        model=config.MODEL,
        max_tokens=32000,
        thinking={"type": "adaptive"},
        system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_format=UpgradedPost,
    )
    upgraded = response.parsed_output
    if upgraded is None:
        raise RuntimeError("업그레이드 실패: 모델이 스키마에 맞는 출력을 내지 못했습니다.")
    return upgraded


# ─────────────────────────────────────────────────────────────
# 4) B 애드센스 삽입
# ─────────────────────────────────────────────────────────────

_LOADER_TMPL = (
    '<script async '
    'src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js'
    '?client={client}" crossorigin="anonymous"></script>\n'
)

_AD_UNIT_TMPL = (
    '<ins class="adsbygoogle"\n'
    '     style="display:block"\n'
    '     data-ad-client="{client}"\n'
    '     data-ad-slot="{slot}"\n'
    '     data-ad-format="auto"\n'
    '     data-full-width-responsive="true"></ins>\n'
    "<script>(adsbygoogle = window.adsbygoogle || []).push({{}});</script>"
)


def build_ad_unit(client_id: str, slot: str, include_loader: bool) -> str:
    unit = _AD_UNIT_TMPL.format(client=client_id, slot=slot)
    if include_loader:
        return _LOADER_TMPL.format(client=client_id) + unit
    return unit


def inject_ads(body_html: str, site: Site) -> tuple[str, int]:
    """<p>[AD]</p> 마커를 B 사이트 광고 단위로 치환. (본문, 삽입 개수) 반환.

    테마 <head> 에 애드센스 로더가 이미 있어도 문제없도록, 첫 광고에만
    로더를 함께 넣는다(중복 로더는 애드센스가 무시한다).
    """
    marker_pat = re.compile(r"<p>\s*\[AD\]\s*</p>", re.I)

    if not site.adsense_configured:
        html, n = marker_pat.subn("", body_html)
        return html, 0

    slots = site.adsense_slots
    count = 0

    def _repl(_m: re.Match) -> str:
        nonlocal count
        slot = slots[count % len(slots)]
        unit = build_ad_unit(site.adsense_client, slot, include_loader=(count == 0))
        count += 1
        return unit

    html = marker_pat.sub(_repl, body_html)
    return html, count


# ─────────────────────────────────────────────────────────────
# 5) 이미지 업로드 · 태그 · 발행
# ─────────────────────────────────────────────────────────────

def upload_media(site: Site, path: Path, title: str, alt_text: str) -> dict:
    """이미지를 B 미디어 라이브러리에 올린다. {'id', 'source_url'} 반환."""
    data = path.read_bytes()
    filename = path.name.encode("ascii", "ignore").decode() or "thumbnail.png"
    resp = requests.post(
        _api(site, "media"),
        data=data,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "image/png",
        },
        auth=_auth(site),
        timeout=60,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"이미지 업로드 실패 ({resp.status_code}): {resp.text[:300]}")
    media = resp.json()

    # 대체 텍스트/제목 지정 (실패해도 치명적이지 않음)
    requests.post(
        _api(site, f"media/{media['id']}"),
        json={"alt_text": alt_text, "title": title},
        auth=_auth(site),
        timeout=_TIMEOUT,
    )
    return {"id": media["id"], "source_url": media.get("source_url", "")}


def ensure_tags(site: Site, names: list[str]) -> list[int]:
    """태그 이름을 ID 로 변환한다. 없으면 만든다. 실패한 태그는 건너뛴다."""
    ids: list[int] = []
    for name in names:
        try:
            resp = requests.get(_api(site, "tags"), params={"search": name},
                                auth=_auth(site), timeout=_TIMEOUT)
            match = next(
                (t for t in resp.json() if t.get("name", "").lower() == name.lower()),
                None,
            ) if resp.status_code == 200 else None
            if match:
                ids.append(match["id"])
                continue
            resp = requests.post(_api(site, "tags"), json={"name": name},
                                 auth=_auth(site), timeout=_TIMEOUT)
            if resp.status_code in (200, 201):
                ids.append(resp.json()["id"])
        except requests.RequestException:
            continue
    return ids


def retire_source(site: Site, post_id: int) -> None:
    """원본 글을 비공개(draft)로 전환한다 (중복 콘텐츠 방지)."""
    resp = requests.post(_api(site, f"posts/{post_id}"),
                         json={"status": "draft"},
                         auth=_auth(site), timeout=_TIMEOUT)
    if resp.status_code != 200:
        raise RuntimeError(f"원본 비공개 전환 실패 ({resp.status_code}): {resp.text[:300]}")


# ─────────────────────────────────────────────────────────────
# 6) 전체 파이프라인
# ─────────────────────────────────────────────────────────────

def migrate(
    source: Site,
    target: Site,
    post_id: int | None = None,
    post_url: str | None = None,
    search: str | None = None,
    focus_keyword: str | None = None,
    status: str = "draft",
    skip_image: bool = False,
    do_retire_source: bool = False,
    log=print,
) -> dict:
    """A 글 하나를 업그레이드해 B 에 발행한다. 결과 요약 dict 반환."""
    _require_configured(source)
    _require_configured(target)

    log(f"[1/6] 원본 글 가져오는 중 ← {source.name}")
    old = fetch_post(source, post_id=post_id, post_url=post_url, search=search)
    log(f"      \"{old['title']}\" (ID={old['id']}, {old['date'][:10]})")

    log("[2/6] 원본의 애드센스 코드 제거 중")
    clean_html, removed = strip_adsense(old["content"])
    log(f"      광고 블록 {removed}개 제거")

    log(f"[3/6] Claude 업그레이드 중 (모델={config.MODEL}, 수십 초 소요)")
    upgraded = upgrade_post(old["title"], clean_html, target,
                            focus_keyword=focus_keyword)
    log(f"      새 제목: {upgraded.new_title}")

    log("[4/6] B 애드센스 삽입 중")
    body_html = markdown_to_html(upgraded.body_markdown)
    body_html, ads_in = inject_ads(body_html, target)
    if ads_in:
        log(f"      {target.name} 광고 단위 {ads_in}개 삽입 "
            f"(client={target.adsense_client})")
    else:
        log(f"      ⚠️ ADSENSE_{target.env_prefix}_CLIENT/_SLOTS 미설정 — "
            f"광고 없이 발행합니다.")

    featured_media = None
    thumb_info = ""
    if skip_image:
        log("[5/6] 썸네일 생략 (--skip-image)")
    else:
        log("[5/6] 썸네일 제작·업로드 중")
        if not thumbnail.font_available():
            log("      ⚠️ 한글 폰트를 찾지 못했습니다. 글자가 깨질 수 있으니 "
                "THUMBNAIL_FONT 를 .env 에 설정하세요.")
        MIGRATED_DIR.mkdir(parents=True, exist_ok=True)
        thumb_path = MIGRATED_DIR / f"{upgraded.slug or old['slug']}-thumb.png"
        thumbnail.make_thumbnail(
            title=upgraded.thumbnail_title,
            subtitle=upgraded.thumbnail_subtitle,
            brand=target.name,
            out_path=thumb_path,
            palette_seed=target.key,
        )
        media = upload_media(target, thumb_path,
                             title=upgraded.new_title,
                             alt_text=upgraded.thumbnail_title.replace("\n", " "))
        featured_media = media["id"]
        thumb_info = media["source_url"]
        log(f"      업로드 완료: {thumb_info}")

    log(f"[6/6] {target.name} 에 발행 중 (status={status})")
    payload: dict = {
        "title": upgraded.new_title,
        "content": body_html,
        "status": status,
        "slug": upgraded.slug,
        "excerpt": upgraded.meta_description,
    }
    if featured_media:
        payload["featured_media"] = featured_media
    tag_ids = ensure_tags(target, upgraded.tags)
    if tag_ids:
        payload["tags"] = tag_ids

    resp = requests.post(_api(target, "posts"), json=payload,
                         auth=_auth(target), timeout=_TIMEOUT)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"발행 실패 ({resp.status_code}): {resp.text[:500]}")
    new_post = resp.json()

    if do_retire_source:
        log(f"      원본 글(ID={old['id']}) 비공개 전환 중 ← {source.name}")
        retire_source(source, old["id"])

    return {
        "old": old,
        "upgraded": upgraded,
        "removed_ads": removed,
        "inserted_ads": ads_in,
        "thumbnail_url": thumb_info,
        "new_id": new_post.get("id"),
        "new_status": new_post.get("status"),
        "new_link": new_post.get("link"),
        "edit_link": f"{target.url.rstrip('/')}/wp-admin/post.php"
                     f"?post={new_post.get('id')}&action=edit",
        "source_retired": do_retire_source,
    }
