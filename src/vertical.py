"""가로 영상을 숏츠용 세로(9:16) 영상으로 바꾼다.

숏츠 제작 파이프라인의 2단계다.
    [1] 링크 + 시작/끝 시간 → 구간 추출        (youtube_clipper.py)
    [2] 9:16 세로 변환 + 훅 문구               ← 이 파일
    [3] 업로드                                 (예정)

유튜브는 정사각형보다 세로로 길고 3분 이하인 영상만 숏츠로 인식한다.
가로 16:9 영상을 그대로 올리면 숏츠가 되지 않는다.

변환 방식 세 가지:
    blur : 영상을 가로 폭에 맞추고 위아래를 흐린 배경으로 채운다(기본).
           원본이 잘리지 않아 대부분의 영상에 무난하다.
    crop : 가운데를 세로로 잘라낸다. 인물·자막이 중앙에 있을 때 화면을
           꽉 채울 수 있지만 좌우가 잘린다.
    pad  : 위아래를 단색으로 채운다. 화면 정보가 중요한 영상에 쓴다.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

from .youtube_clipper import ClipError, require_tool

# 숏츠 권장 해상도
TARGET_W = 1080
TARGET_H = 1920

MODES = ("blur", "crop", "pad")
MODE_LABELS = {
    "blur": "흐린 배경 (원본이 안 잘림)",
    "crop": "가운데 꽉 채우기 (좌우 잘림)",
    "pad": "위아래 검은 여백",
}

# 훅 문구를 넣을 세로 위치. 숏츠 화면 위/아래 끝은 유튜브 UI(제목·버튼)가
# 덮으므로 안쪽으로 충분히 들여 넣는다.
TEXT_Y = {"top": 260, "middle": (TARGET_H - 120) // 2, "bottom": TARGET_H - 620}

# 한글이 나오는 폰트 후보 (OS 별)
FONT_CANDIDATES = {
    "Windows": [
        r"C:\Windows\Fonts\malgunbd.ttf",   # 맑은 고딕 굵게
        r"C:\Windows\Fonts\malgun.ttf",
        r"C:\Windows\Fonts\NanumGothicBold.ttf",
        r"C:\Windows\Fonts\gulim.ttc",
    ],
    "Darwin": [
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/Library/Fonts/NanumGothic.ttf",
    ],
    "Linux": [
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ],
}


def find_korean_font() -> str | None:
    """한글이 깨지지 않는 폰트 경로를 찾는다. 없으면 None."""
    for path in FONT_CANDIDATES.get(platform.system(), []):
        if Path(path).exists():
            return path
    # 마지막 수단: fontconfig 에 물어본다 (리눅스/맥)
    if shutil.which("fc-match"):
        try:
            out = subprocess.run(
                ["fc-match", "-f", "%{file}", ":lang=ko"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            if out and Path(out).exists():
                return out
        except Exception:
            pass
    return None


def escape_filter_path(path: str | Path) -> str:
    """ffmpeg 필터 인자에 들어갈 경로를 안전하게 만든다.

    윈도우 경로의 `C:\\` 는 필터 문법에서 인자 구분자(:)와 충돌하므로
    슬래시로 바꾸고 콜론을 이스케이프한다.
    """
    text = str(path).replace("\\", "/").replace(":", r"\:")
    return f"'{text}'"


def wrap_text(text: str, per_line: int = 16) -> str:
    """훅 문구를 화면 폭에 맞게 줄바꿈한다(drawtext 는 자동 줄바꿈이 없다)."""
    words = str(text).strip().split()
    if not words:
        return ""
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= per_line or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return "\n".join(lines[:4])       # 4줄까지만


def build_vertical_filter(
    mode: str = "blur",
    *,
    textfiles: list[Path] | None = None,
    font: str | None = None,
    position: str = "top",
    font_size: int = 72,
) -> str:
    """세로 변환 필터 문자열을 만든다."""
    if mode not in MODES:
        raise ClipError(f"알 수 없는 변환 방식 '{mode}'. 사용 가능: {', '.join(MODES)}")

    size = f"{TARGET_W}:{TARGET_H}"
    if mode == "blur":
        # 배경은 어차피 흐릿하므로 작게 줄여 블러한 뒤 확대한다.
        # 1080x1920 에서 바로 블러하는 것보다 몇 배 빠르고 결과는 거의 같다.
        small = f"{TARGET_W // 8}:{TARGET_H // 8}"
        chain = (
            f"split=2[bg][fg];"
            f"[bg]scale={small}:force_original_aspect_ratio=increase,"
            f"crop={small},gblur=sigma=6,scale={size}[bgb];"
            f"[fg]scale={size}:force_original_aspect_ratio=decrease[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
    elif mode == "crop":
        chain = (
            f"scale={size}:force_original_aspect_ratio=increase,"
            f"crop={size},setsar=1"
        )
    else:  # pad
        chain = (
            f"scale={size}:force_original_aspect_ratio=decrease,"
            f"pad={size}:-1:-1:color=black,setsar=1"
        )

    if textfiles:
        if not font:
            raise ClipError("문구를 넣으려면 한글 폰트가 필요합니다.")
        base_y = TEXT_Y.get(position, TEXT_Y["top"])
        line_height = font_size + 22
        # 줄마다 따로 그린다 → 각 줄이 가운데 정렬된다
        for i, textfile in enumerate(textfiles):
            chain += (
                f",drawtext=fontfile={escape_filter_path(font)}"
                f":textfile={escape_filter_path(textfile)}"
                f":fontcolor=white:fontsize={font_size}"
                f":borderw=6:bordercolor=black@0.85"
                f":x=(w-text_w)/2:y={base_y + i * line_height}"
            )
    return chain


def build_vertical_command(
    source: Path,
    dest: Path,
    *,
    mode: str = "blur",
    textfiles: list[Path] | None = None,
    font: str | None = None,
    position: str = "top",
    ffmpeg: str = "ffmpeg",
) -> list[str]:
    """세로 변환 ffmpeg 명령을 만든다."""
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-stats", "-y",
        "-i", str(source),
        "-vf", build_vertical_filter(mode, textfiles=textfiles, font=font, position=position),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(dest),
    ]


def make_vertical(
    source: Path | str,
    dest: Path | str | None = None,
    *,
    mode: str = "blur",
    text: str | None = None,
    position: str = "top",
    replace: bool = False,
) -> Path:
    """가로 영상을 세로(1080x1920) 영상으로 바꾼다.

    replace=True 면 변환 뒤 원본(가로 클립)을 지운다.
    """
    source = Path(source)
    if not source.exists():
        raise ClipError(f"파일이 없습니다: {source}")
    dest = Path(dest) if dest else source.with_name(f"{source.stem}_세로{source.suffix}")

    ffmpeg = require_tool("ffmpeg")
    font = find_korean_font() if text else None
    if text and not font:
        raise ClipError(
            "한글을 표시할 폰트를 찾지 못해 문구를 넣을 수 없습니다.\n"
            "  → 문구 없이 변환하거나, 나눔고딕을 설치한 뒤 다시 시도하세요."
        )

    with tempfile.TemporaryDirectory() as tmp:
        textfiles: list[Path] = []
        if text and str(text).strip():
            for i, line in enumerate(wrap_text(text).split("\n")):
                path = Path(tmp) / f"line{i}.txt"
                path.write_text(line, encoding="utf-8")
                textfiles.append(path)

        cmd = build_vertical_command(
            source, dest, mode=mode, textfiles=textfiles,
            font=font, position=position, ffmpeg=ffmpeg,
        )
        proc = subprocess.run(cmd, stderr=subprocess.PIPE, text=True,
                              encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            tail = "\n".join((proc.stderr or "").strip().splitlines()[-6:])
            raise ClipError(f"세로 변환 실패\n{tail}")

    if replace and dest.exists() and dest != source:
        source.unlink(missing_ok=True)
    return dest
