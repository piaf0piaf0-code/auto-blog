"""워드프레스 속도 진단 + 관리자 느림 원인(초안 누적) 정리 도구.

사이트에 SSH 없이도 밖에서 할 수 있는 것 두 가지를 자동화한다.

1. diagnose — 서버 응답속도(TTFB)·캐시 적용 여부·활성 플러그인 수·
   쌓인 초안/휴지통 개수를 측정해 "무엇이 느린지"를 숫자로 보여준다.
2. clean-drafts — 오래된 초안을 휴지통으로 보낸다(기본 dry-run).
   자동 발행 파이프라인이 초안을 계속 쌓는 구조라, 방치하면
   관리자 화면(초안 목록·자동저장)이 점점 느려진다.

인증은 wordpress_publisher 와 동일하게 Application Passwords 를 쓴다.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import requests

from .config import Site

# 요청당 타임아웃(초). 느린 사이트 진단이 목적이므로 넉넉히 잡는다.
_TIMEOUT = 60

# 응답 헤더 중 캐시/서버 판별에 쓰는 것들
_CACHE_HEADERS = (
    "x-litespeed-cache",
    "x-cache",
    "x-cache-status",
    "cf-cache-status",
    "x-wp-super-cache",
    "cache-control",
    "age",
)


@dataclass
class Diagnosis:
    """사이트 한 곳의 진단 결과."""

    site: Site
    ttfb_samples: list[float] = field(default_factory=list)  # 초 단위
    server: str = "?"
    cache_headers: dict[str, str] = field(default_factory=dict)
    cache_active: bool | None = None
    page_bytes: int = 0
    counts: dict[str, int] = field(default_factory=dict)  # 상태별 글 개수
    active_plugins: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _auth(site: Site) -> tuple[str, str]:
    return (site.user, site.app_password)


def _rest(site: Site, path: str) -> str:
    return f"{site.url.rstrip('/')}/wp-json/wp/v2/{path.lstrip('/')}"


def _measure_ttfb(url: str, samples: int = 3) -> tuple[list[float], requests.Response | None]:
    """같은 URL을 여러 번 받아 TTFB(헤더 수신까지) 목록과 마지막 응답을 돌려준다.

    첫 요청과 이후 요청의 차이로 페이지 캐시 동작 여부도 가늠할 수 있다.
    """
    times: list[float] = []
    last: requests.Response | None = None
    for _ in range(samples):
        t0 = time.monotonic()
        # stream=True: 본문 다운로드 전, 헤더 도착 시점까지를 TTFB 로 잰다
        resp = requests.get(url, timeout=_TIMEOUT, stream=True,
                            headers={"User-Agent": "auto-blog-speedcheck/1.0"})
        ttfb = time.monotonic() - t0
        resp.content  # 본문 소진(커넥션 정리 + 크기 측정)
        times.append(ttfb)
        last = resp
    return times, last


def _count_posts(site: Site, status: str) -> int:
    """해당 상태의 글 개수를 X-WP-Total 헤더로 얻는다 (비공개 상태는 인증 필요)."""
    resp = requests.get(
        _rest(site, "posts"),
        params={"status": status, "per_page": 1, "context": "edit", "_fields": "id"},
        auth=_auth(site),
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return int(resp.headers.get("X-WP-Total", "0"))


def diagnose(site: Site) -> Diagnosis:
    """사이트 하나를 진단한다. 실패 항목은 건너뛰고 errors 에 기록한다."""
    d = Diagnosis(site=site)
    base = site.url.rstrip("/") + "/"

    # 1) 프런트 페이지 TTFB + 캐시 헤더
    try:
        d.ttfb_samples, last = _measure_ttfb(base)
        if last is not None:
            d.server = last.headers.get("server", "?")
            d.page_bytes = len(last.content)
            for h in _CACHE_HEADERS:
                if h in last.headers:
                    d.cache_headers[h] = last.headers[h]
            hits = [d.cache_headers.get(h, "").lower() for h in
                    ("x-litespeed-cache", "x-cache", "x-cache-status", "cf-cache-status")]
            d.cache_active = any("hit" in v for v in hits) or None
    except requests.RequestException as e:
        d.errors.append(f"프런트 측정 실패: {e}")

    # 2) 글 상태별 개수 (초안 누적 = 관리자 느림의 단골 원인)
    if site.configured:
        for status in ("publish", "draft", "pending", "trash"):
            try:
                d.counts[status] = _count_posts(site, status)
            except requests.RequestException as e:
                d.errors.append(f"글 개수({status}) 조회 실패: {e}")
                break
    else:
        d.errors.append(".env 미설정 → 글 개수/플러그인 조회 생략")

    # 3) 활성 플러그인 목록 (관리자 권한 필요; 실패해도 치명적이지 않음)
    if site.configured:
        try:
            resp = requests.get(
                _rest(site, "plugins"),
                params={"_fields": "name,status"},
                auth=_auth(site),
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            d.active_plugins = sorted(
                p["name"] for p in resp.json() if p.get("status") == "active"
            )
        except requests.RequestException as e:
            d.errors.append(f"플러그인 목록 조회 실패(권한 부족 가능): {e}")

    return d


def format_diagnosis(d: Diagnosis) -> str:
    """진단 결과를 사람이 읽는 리포트로 만든다. 판정 기준을 함께 표기한다."""
    lines = [f"■ {d.site.name} ({d.site.url or 'URL 미설정'})"]

    if d.ttfb_samples:
        first, rest = d.ttfb_samples[0], d.ttfb_samples[1:]
        avg_rest = sum(rest) / len(rest) if rest else first
        worst = max(d.ttfb_samples)
        if worst < 0.5:
            verdict = "양호 — 서버는 문제 아님. 느리다면 프런트(이미지·광고·JS) 쪽."
        elif worst < 1.0:
            verdict = "보통 — 캐시 적용 여부를 먼저 확인."
        else:
            verdict = "느림 — 서버 측 문제(캐시 부재/PHP 구버전/호스팅 사양)."
        lines.append(
            f"  TTFB: 첫 요청 {first:.2f}s, 이후 평균 {avg_rest:.2f}s "
            f"(샘플 {len(d.ttfb_samples)}회) → {verdict}"
        )
        if rest and first > 1.0 and avg_rest < first * 0.5:
            lines.append("  ↳ 첫 요청만 느림: 페이지 캐시가 식은 뒤 재생성되는 패턴.")
        lines.append(f"  서버: {d.server}, 페이지 크기: {d.page_bytes/1024:.0f}KB")
        if d.cache_headers:
            pairs = ", ".join(f"{k}={v}" for k, v in d.cache_headers.items())
            state = "HIT 확인됨" if d.cache_active else "HIT 미확인"
            lines.append(f"  캐시 헤더({state}): {pairs}")
        else:
            lines.append("  캐시 헤더 없음 → 페이지 캐시 미설치 가능성 큼 (최우선 처방).")

    if d.counts:
        c = d.counts
        lines.append(
            f"  글 개수: 공개 {c.get('publish', 0)} / 초안 {c.get('draft', 0)}"
            f" / 대기 {c.get('pending', 0)} / 휴지통 {c.get('trash', 0)}"
        )
        if c.get("draft", 0) >= 100:
            lines.append(
                "  ↳ 초안 100개 이상: 관리자 느림의 원인. clean-drafts 로 정리 권장."
            )

    if d.active_plugins:
        lines.append(f"  활성 플러그인 {len(d.active_plugins)}개:")
        for name in d.active_plugins:
            lines.append(f"    - {name}")
        if len(d.active_plugins) >= 20:
            lines.append("  ↳ 20개 이상: 과다. 안 쓰는 것은 비활성화가 아니라 삭제.")

    for err in d.errors:
        lines.append(f"  ⚠️  {err}")

    return "\n".join(lines)


def list_old_drafts(site: Site, days: int) -> list[dict]:
    """수정된 지 days 일이 지난 초안 목록을 (전 페이지 순회로) 가져온다."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    old: list[dict] = []
    page = 1
    while True:
        resp = requests.get(
            _rest(site, "posts"),
            params={
                "status": "draft",
                "context": "edit",
                "per_page": 100,
                "page": page,
                "orderby": "modified",
                "order": "asc",
                "_fields": "id,title,modified_gmt",
            },
            auth=_auth(site),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        for post in batch:
            modified = datetime.fromisoformat(post["modified_gmt"]).replace(
                tzinfo=timezone.utc
            )
            if modified < cutoff:
                old.append(post)
        # 오름차순 정렬이므로 마지막 항목이 기준보다 최신이면 더 볼 필요 없다
        last_mod = datetime.fromisoformat(batch[-1]["modified_gmt"]).replace(
            tzinfo=timezone.utc
        )
        if last_mod >= cutoff:
            break
        page += 1
    return old


def trash_drafts(site: Site, days: int, apply: bool = False) -> tuple[int, list[str]]:
    """오래된 초안을 휴지통으로 보낸다.

    apply=False(기본)면 대상만 보여주는 dry-run.
    휴지통 이동이므로 워드프레스 관리자에서 복구할 수 있다(영구삭제 아님).
    반환: (대상 개수, 로그 줄들)
    """
    old = list_old_drafts(site, days)
    logs: list[str] = []
    for post in old:
        title = post.get("title", {}).get("raw") or "(제목 없음)"
        line = f"  [{post['id']}] {post['modified_gmt'][:10]} — {title[:60]}"
        if apply:
            resp = requests.delete(
                _rest(site, f"posts/{post['id']}"),
                params={"force": "false"},  # 휴지통 이동(복구 가능)
                auth=_auth(site),
                timeout=_TIMEOUT,
            )
            ok = resp.status_code == 200
            line += " → 휴지통" if ok else f" → 실패({resp.status_code})"
        logs.append(line)
    return len(old), logs
