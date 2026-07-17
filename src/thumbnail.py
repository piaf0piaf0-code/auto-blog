"""블로그 썸네일(대표이미지) 자동 제작.

외부 이미지 API 없이 Pillow 로 1200x630 텍스트 카드를 만든다.
한글 폰트는 THUMBNAIL_FONT 환경변수 경로를 우선 사용하고, 없으면
흔한 설치 경로에서 자동 탐색한다.
"""
from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1200, 630
MARGIN = 80

# 사이트 키 해시로 골라 쓰는 배경 그라디언트 (위 색 → 아래 색)
_PALETTES: list[tuple[tuple[int, int, int], tuple[int, int, int]]] = [
    ((15, 23, 42), (30, 64, 175)),     # 네이비 → 블루
    ((6, 78, 59), (16, 185, 129)),     # 딥그린 → 에메랄드
    ((76, 29, 149), (139, 92, 246)),   # 딥퍼플 → 바이올렛
    ((124, 45, 18), (234, 88, 12)),    # 브라운 → 오렌지
    ((17, 24, 39), (55, 65, 81)),      # 차콜 → 그레이
]

# THUMBNAIL_FONT 미설정 시 탐색할 한글 폰트 경로들
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "C:/Windows/Fonts/malgunbd.ttf",
    "C:/Windows/Fonts/malgun.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
]


def _find_font_path() -> str | None:
    env = os.getenv("THUMBNAIL_FONT")
    if env and Path(env).exists():
        return env
    for p in _FONT_CANDIDATES:
        if Path(p).exists():
            return p
    return None


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _find_font_path()
    if path:
        return ImageFont.truetype(path, size)
    # 한글이 □ 로 깨질 수 있음 — 호출부에서 경고를 낸다.
    return ImageFont.load_default(size)


def font_available() -> bool:
    return _find_font_path() is not None


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    """단어 단위로 폭에 맞춰 줄바꿈. 명시적 \n 은 존중한다."""
    lines: list[str] = []
    for raw_line in text.split("\n"):
        words = raw_line.split()
        if not words:
            continue
        cur = words[0]
        for w in words[1:]:
            trial = f"{cur} {w}"
            if draw.textlength(trial, font=font) <= max_width:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


def make_thumbnail(
    title: str,
    subtitle: str,
    brand: str,
    out_path: Path,
    palette_seed: str = "",
) -> Path:
    """1200x630 텍스트 카드 썸네일을 만들어 out_path 에 저장한다."""
    top, bottom = _PALETTES[sum(map(ord, palette_seed or brand)) % len(_PALETTES)]

    img = Image.new("RGB", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(img)

    # 세로 그라디언트 배경
    for y in range(HEIGHT):
        t = y / (HEIGHT - 1)
        color = tuple(int(a + (b - a) * t) for a, b in zip(top, bottom))
        draw.line([(0, y), (WIDTH, y)], fill=color)

    # 좌측 포인트 바
    draw.rectangle([(0, 0), (14, HEIGHT)], fill=(255, 255, 255))

    title_font = _load_font(76)
    subtitle_font = _load_font(38)
    brand_font = _load_font(30)

    max_text_width = WIDTH - MARGIN * 2
    title_lines = _wrap(draw, title, title_font, max_text_width)[:3]
    subtitle_lines = _wrap(draw, subtitle, subtitle_font, max_text_width)[:2]

    line_gap = 14
    title_h = len(title_lines) * (76 + line_gap)
    subtitle_h = len(subtitle_lines) * (38 + line_gap)
    block_h = title_h + (24 + subtitle_h if subtitle_lines else 0)
    y = (HEIGHT - block_h) // 2 - 20

    for line in title_lines:
        draw.text((MARGIN, y), line, font=title_font, fill=(255, 255, 255))
        y += 76 + line_gap
    if subtitle_lines:
        y += 24
        for line in subtitle_lines:
            draw.text((MARGIN, y), line, font=subtitle_font, fill=(226, 232, 240))
            y += 38 + line_gap

    # 하단 브랜드(도메인) 표기
    draw.text((MARGIN, HEIGHT - MARGIN - 10), brand,
              font=brand_font, fill=(203, 213, 225))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return out_path
