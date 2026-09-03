"""자막을 읽고 숏츠로 쓸 만한 구간을 Claude 가 골라준다.

숏츠 제작 파이프라인의 0단계(구간 찾기)다.
    [0] 자막 → 훅 될 구간 추천                 ← 이 파일
    [1] 링크 + 시작/끝 → 구간 추출             (youtube_clipper.py)
    [2] 9:16 세로 변환 + 훅 문구               (vertical.py)

여태 가장 오래 걸리던 일은 "영상을 다 보고 어디를 자를지 찾는 것"이었다.
유튜브 자막(자동 생성 포함)을 받아 Claude 에게 넘기고, 훅이 될 만한
20~60초 구간과 그 이유·제목·화면 문구를 제안받아 그 일을 대신한다.

⚠️ 제안은 자막(=말)만 보고 낸 추정이다. 화면에서 무슨 일이 벌어지는지는
   모른다. 최종 판단은 사람이 미리보기로 확인하고 한다.
"""
from __future__ import annotations

import html
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from . import config
from .youtube_clipper import (
    ClipError,
    Segment,
    VideoRef,
    _auth_args,
    _js_runtime_args,
    _run,
    format_human,
    parse_youtube_url,
    require_tool,
)

# 숏츠로 쓸 만한 길이 범위 (초)
MIN_CLIP_SECONDS = 15
MAX_CLIP_SECONDS = 90

# 자막을 이 간격으로 묶어 프롬프트에 넣는다. 너무 잘면 토큰만 늘고,
# 너무 뭉치면 모델이 시작 시각을 정밀하게 잡지 못한다.
BUCKET_SECONDS = 15


# ─────────────────────────────────────────────────────────────
# 자막 가져오기 / 파싱
# ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Cue:
    """자막 한 줄."""

    start: float
    end: float
    text: str


_CUE_RE = re.compile(
    r"(\d{1,2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[.,]\d{3})"
)
_TAG_RE = re.compile(r"<[^>]*>")


def _vtt_timestamp(text: str) -> float:
    h, m, s = text.replace(",", ".").split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def parse_vtt(content: str) -> list[Cue]:
    """VTT 자막을 파싱한다.

    유튜브 자동 자막은 같은 문장을 여러 번 되풀이하며 한 단어씩 늘려
    보여준다(롤링 자막). 그대로 두면 프롬프트가 몇 배로 부풀고 모델도
    헷갈리므로, 앞 줄이 그대로 반복되면 새로 늘어난 부분만 남긴다.
    """
    lines = content.splitlines()
    cues: list[Cue] = []
    previous = ""
    i = 0

    while i < len(lines):
        match = _CUE_RE.search(lines[i])
        if not match:
            i += 1
            continue

        start = _vtt_timestamp(match.group(1))
        end = _vtt_timestamp(match.group(2))
        i += 1

        parts: list[str] = []
        while i < len(lines) and not _CUE_RE.search(lines[i]):
            if not lines[i].strip():
                if parts:
                    break          # 빈 줄 = 이 자막 블록의 끝
                i += 1             # 블록 앞머리의 빈 줄(자동 자막)은 건너뛴다
                continue
            parts.append(lines[i])
            i += 1

        text = html.unescape(_TAG_RE.sub("", " ".join(parts)))
        text = re.sub(r"\s+", " ", text).strip()
        if not text or text == previous:
            continue

        addition = text
        if previous and text.startswith(previous):
            addition = text[len(previous):].strip()
            if not addition:
                previous = text
                continue

        cues.append(Cue(start, end, addition))
        previous = text

    return cues


def fetch_subtitles(
    url: str,
    *,
    languages: tuple[str, ...] = ("ko", "en"),
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
) -> tuple[list[Cue], str]:
    """유튜브에서 자막을 받아 파싱한다. (자막목록, 사용한 언어) 를 돌려준다.

    사람이 단 자막이 있으면 그쪽이 정확하고, 없으면 자동 생성 자막을 쓴다.
    """
    ref = parse_youtube_url(url)
    yt_dlp = require_tool("yt-dlp")
    sub_langs = ",".join([f"{lang}.*" for lang in languages] + list(languages))

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        cmd = [
            yt_dlp,
            "--no-playlist", "--skip-download", "--no-warnings",
            "--write-subs", "--write-auto-subs",
            "--sub-langs", sub_langs,
            "--sub-format", "vtt/best",
            "--convert-subs", "vtt",
            "-o", str(out / "%(id)s.%(ext)s"),
            *_js_runtime_args(),
            *_auth_args(cookies, cookies_from_browser),
            ref.url,
        ]
        _run(cmd, quiet=True)

        files = sorted(out.glob("*.vtt"))
        if not files:
            raise ClipError(
                "이 영상에는 자막이 없습니다.\n"
                "  → 자막이 없는 영상은 AI 추천을 쓸 수 없습니다. "
                "직접 시작·끝 시간을 정해주세요."
            )

        # 원하는 언어 순서대로 고른다 (ko 를 en 보다 우선)
        chosen = files[0]
        for lang in languages:
            match = [f for f in files if f".{lang}" in f.name]
            if match:
                chosen = match[0]
                break

        cues = parse_vtt(chosen.read_text(encoding="utf-8", errors="replace"))

    if not cues:
        raise ClipError("자막을 읽었지만 내용이 비어 있습니다.")

    language = chosen.name.split(".")[-2] if "." in chosen.name else "?"
    return cues, language


def build_transcript(cues: list[Cue], bucket: float = BUCKET_SECONDS) -> str:
    """자막을 '[시각] 내용' 형태로 묶는다 (프롬프트에 넣을 형태)."""
    if not cues:
        return ""
    lines: list[str] = []
    bucket_start = cues[0].start
    words: list[str] = []

    for cue in cues:
        if cue.start - bucket_start >= bucket and words:
            lines.append(f"[{format_human(bucket_start)}] {' '.join(words)}")
            bucket_start = cue.start
            words = []
        words.append(cue.text)

    if words:
        lines.append(f"[{format_human(bucket_start)}] {' '.join(words)}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# Claude 에게 구간 추천받기
# ─────────────────────────────────────────────────────────────

class ClipSuggestion(BaseModel):
    start_seconds: float = Field(description="구간 시작 시각(초). 자막 타임스탬프 기준.")
    end_seconds: float = Field(description="구간 끝 시각(초).")
    title: str = Field(description="이 구간으로 만들 숏츠 제목 (20자 내외, 낚시성 금지)")
    hook_text: str = Field(
        description="영상 위에 큰 글씨로 태울 문구 (15자 이내, 짧고 강하게)"
    )
    reason: str = Field(description="왜 이 구간이 훅이 되는지 한 줄 근거")
    score: int = Field(ge=1, le=10, description="숏츠 적합도 (10이 가장 높음)")


class ClipPlan(BaseModel):
    video_summary: str = Field(description="영상 전체 내용 두 줄 요약")
    suggestions: list[ClipSuggestion]


_SYSTEM = """당신은 유튜브 숏츠 편집자다. 긴 영상의 자막을 읽고,
숏츠로 잘라 올렸을 때 끝까지 보게 만드는 구간을 골라낸다.

고르는 기준:
- 첫 2초가 승부다. 구간은 사람을 붙잡는 말에서 시작해야 한다. 인사말,
  "자 그럼", "다음으로" 같은 이음말에서 시작하지 않는다.
- 하나의 완결된 이야기여야 한다. 문장 중간에서 시작하거나 끊지 않는다.
- 다음 중 하나에 해당하는 구간이 좋다:
  통념을 뒤집는 말 / 구체적인 숫자·금액 / 실패담·손해 본 이야기 /
  당장 따라 할 수 있는 방법 / 감정이 실린 대목
- 설명만 이어지는 평평한 구간, 광고·협찬 안내, 인사·마무리는 피한다.
- 길이는 20~60초가 가장 좋다. 45초를 넘기려면 그만한 이유가 있어야 한다.

시각은 반드시 주어진 자막 타임스탬프에 근거해 정한다. 지어내지 않는다.
제목과 문구는 한국어로, 과장·낚시 없이 내용 그대로 쓴다."""


def _clamp_suggestions(
    plan: ClipPlan, duration: float | None = None
) -> list[ClipSuggestion]:
    """모델이 낸 시각을 실제로 쓸 수 있는 값으로 다듬는다."""
    cleaned: list[ClipSuggestion] = []
    for s in plan.suggestions:
        start = max(0.0, float(s.start_seconds))
        end = float(s.end_seconds)

        # 영상 밖을 가리키는 제안은 버린다. 끝쪽으로 끌어다 붙이면 모델이
        # 고르지도 않은 구간을 추천처럼 보여주게 된다.
        if duration and start >= duration - MIN_CLIP_SECONDS:
            continue

        if end <= start:
            end = start + MIN_CLIP_SECONDS
        if duration:
            end = min(end, duration)
        if end - start < MIN_CLIP_SECONDS:
            end = start + MIN_CLIP_SECONDS
            if duration and end > duration:
                continue
        if end - start > MAX_CLIP_SECONDS:
            end = start + MAX_CLIP_SECONDS

        cleaned.append(s.model_copy(update={"start_seconds": start, "end_seconds": end}))
    cleaned.sort(key=lambda s: s.score, reverse=True)
    return cleaned


def suggest_clips(
    url: str,
    *,
    count: int = 5,
    duration: float | None = None,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
) -> tuple[ClipPlan, str]:
    """자막을 받아 Claude 에게 숏츠 구간을 추천받는다.

    (추천 결과, 사용한 자막 언어) 를 돌려준다.
    """
    import anthropic

    cues, language = fetch_subtitles(
        url, cookies=cookies, cookies_from_browser=cookies_from_browser
    )
    transcript = build_transcript(cues)

    client = anthropic.Anthropic(api_key=config.require_api_key())
    prompt = (
        f"아래는 유튜브 영상의 자막이다. [시각] 은 그 대목이 시작하는 지점이다.\n\n"
        f"---\n{transcript}\n---\n\n"
        f"숏츠로 만들 구간 {count}개를 골라라. score 내림차순으로 정렬한다. "
        f"각 구간은 서로 겹치지 않게 한다."
    )

    response = client.messages.parse(
        model=config.MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_format=ClipPlan,
    )
    plan = response.parsed_output
    if plan is None:
        raise ClipError("추천 생성 실패: 모델이 형식에 맞는 답을 내지 못했습니다.")

    plan.suggestions = _clamp_suggestions(plan, duration)
    if not plan.suggestions:
        raise ClipError("쓸 만한 구간을 찾지 못했습니다. 직접 시간을 정해주세요.")
    return plan, language


def to_segment(suggestion: ClipSuggestion) -> Segment:
    """추천 하나를 추출용 구간으로 바꾼다."""
    return Segment(suggestion.start_seconds, suggestion.end_seconds)


def format_plan(plan: ClipPlan, language: str = "") -> str:
    """터미널 출력용 문자열."""
    lines = [f"영상 요약: {plan.video_summary}"]
    if language:
        lines.append(f"(자막 언어: {language})")
    lines.append("")
    for i, s in enumerate(plan.suggestions, 1):
        seg = to_segment(s)
        lines.append(f"{i}. {seg.label}  ({seg.duration:.0f}초)  적합도 {s.score}/10")
        lines.append(f"   제목: {s.title}")
        lines.append(f"   문구: {s.hook_text}")
        lines.append(f"   이유: {s.reason}")
        lines.append(
            f"   → python -m src.run_pipeline clip --url <링크> "
            f"--range {format_human(s.start_seconds)}-{format_human(s.end_seconds)} "
            f'--vertical --text "{s.hook_text}"'
        )
        lines.append("")
    return "\n".join(lines)
