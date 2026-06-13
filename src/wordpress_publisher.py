"""워드프레스 REST API 발행.

검수 완료된 마크다운 초안을 워드프레스에 올린다. 안전을 위해 기본 상태는
'draft'(비공개)이며, 워드프레스 관리자에서 최종 확인 후 직접 공개한다.

인증: Application Passwords (워드프레스 5.6+ 기본 기능).
관리자 > 사용자 > 프로필 > 애플리케이션 비밀번호에서 발급.
"""
from __future__ import annotations

import re
from pathlib import Path

import markdown as md
import requests

from .config import Site

# 발행 시 제거할 검수용 부록 마커 (content_brief.render_markdown 와 일치)
_REVIEW_MARKER = "## ⚠️ 발행 전 검수 항목"


def parse_draft(path: Path) -> tuple[dict[str, str], str]:
    """마크다운 파일을 (프런트매터 dict, 본문) 으로 분리한다."""
    text = path.read_text(encoding="utf-8")
    front: dict[str, str] = {}
    body = text

    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            raw_front, body = parts[1], parts[2]
            for line in raw_front.strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    front[k.strip()] = v.strip()

    # 검수용 부록 제거 (발행 본문에서 빼낸다)
    idx = body.find(_REVIEW_MARKER)
    if idx != -1:
        # 마커 직전의 '---' 구분선까지 함께 잘라낸다
        cut = body.rfind("\n---", 0, idx)
        body = body[: cut if cut != -1 else idx]

    return front, body.strip()


def markdown_to_html(body: str) -> str:
    return md.markdown(body, extensions=["tables", "fenced_code", "sane_lists"])


def _derive_title(front: dict[str, str], body: str) -> str:
    if front.get("title"):
        return front["title"]
    m = re.search(r"^#\s+(.+)$", body, flags=re.MULTILINE)
    if m:
        return m.group(1).strip()
    return front.get("target_keyword", "제목 없음")


def publish_file(
    path: Path,
    site: Site,
    status: str = "draft",
) -> dict:
    """초안 파일을 워드프레스에 발행한다.

    status: "draft"(기본, 권장) | "publish" | "pending"
    """
    if not site.configured:
        raise RuntimeError(
            f"사이트 '{site.key}'({site.name})의 .env 설정이 비어 있습니다. "
            f"WP_{site.env_prefix}_URL / _USER / _APP_PASSWORD 를 확인하세요."
        )

    front, body = parse_draft(path)
    title = _derive_title(front, body)
    html = markdown_to_html(body)

    payload: dict = {
        "title": title,
        "content": html,
        "status": status,
        "slug": front.get("slug", ""),
    }
    excerpt = front.get("meta_description")
    if excerpt:
        payload["excerpt"] = excerpt

    endpoint = f"{site.url.rstrip('/')}/wp-json/wp/v2/posts"
    resp = requests.post(
        endpoint,
        json=payload,
        auth=(site.user, site.app_password),
        timeout=30,
    )

    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"발행 실패 ({resp.status_code}): {resp.text[:500]}"
        )

    data = resp.json()
    return {
        "id": data.get("id"),
        "status": data.get("status"),
        "link": data.get("link"),
        "edit_link": f"{site.url.rstrip('/')}/wp-admin/post.php?post={data.get('id')}&action=edit",
    }
