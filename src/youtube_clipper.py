"""유튜브 링크에서 원하는 시간대만 잘라내는 모듈 (숏츠 소재 확보용).

숏츠 제작 파이프라인의 1단계다.
    [1] 링크 + 시작/끝 시간 → 구간 영상 추출   ← 이 파일
    [2] 9:16 세로 변환 + 자막                  (예정)
    [3] 업로드                                 (예정)

외부 의존:
    - yt-dlp : `pip install -r requirements.txt` 로 설치된다.
    - ffmpeg / ffprobe : 시스템에 직접 설치해야 한다.
        macOS   : brew install ffmpeg
        Ubuntu  : sudo apt install ffmpeg
        Windows : winget install Gyan.FFmpeg

설계 메모:
    - 구간이 1개면 `--download-sections` 로 그 구간만 받는다(빠름/저트래픽).
    - 구간이 2개 이상이면 원본을 한 번만 받아 캐시하고 로컬에서 여러 번 자른다.
      영상 하나에서 클립 여러 개를 뽑는 숏츠 작업에서 훨씬 빠르다.
    - 컷은 기본이 '정밀'이다. 숏츠는 첫 1~2초가 이탈률을 결정하므로
      키프레임에 붙어 시작점이 밀리는 것을 허용하지 않는다(경계만 재인코딩).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse


class ClipError(RuntimeError):
    """사용자에게 그대로 보여줄 수 있는 오류."""


# 기본 저장 위치. .gitignore 에 outputs/ 가 이미 들어 있다.
DEFAULT_OUT_DIR = Path("outputs/clips")
DEFAULT_SOURCE_DIR = Path("outputs/sources")

# 숏츠 규격: 세로(9:16), 최대 3분. 이 값을 넘으면 경고만 하고 진행한다.
SHORTS_MAX_SECONDS = 180


# ─────────────────────────────────────────────────────────────
# 시간 파싱
# ─────────────────────────────────────────────────────────────

# 1h2m3s / 90s / 2m 형태
_UNIT_RE = re.compile(
    r"^(?:(?P<h>\d+(?:\.\d+)?)h)?"
    r"(?:(?P<m>\d+(?:\.\d+)?)m)?"
    r"(?:(?P<s>\d+(?:\.\d+)?)s?)?$",
    re.IGNORECASE,
)


def parse_timecode(text: str | float | int) -> float:
    """사람이 쓰는 시간 표기를 초(float)로 바꾼다.

    허용 형식:
        90 / 90.5      → 초
        1:30           → 분:초
        01:02:03.5     → 시:분:초
        1h2m3s / 90s / 2m30s
    """
    if isinstance(text, (int, float)):
        seconds = float(text)
        if seconds < 0:
            raise ClipError("시간은 0 이상이어야 합니다.")
        return seconds

    raw = str(text).strip().replace(" ", "")
    if not raw:
        raise ClipError("시간 값이 비어 있습니다.")

    if ":" in raw:
        parts = raw.split(":")
        if len(parts) > 3:
            raise ClipError(f"시간 형식을 이해할 수 없습니다: '{text}'")
        try:
            nums = [float(p) if p else 0.0 for p in parts]
        except ValueError as e:
            raise ClipError(f"시간 형식을 이해할 수 없습니다: '{text}'") from e
        seconds = 0.0
        for n in nums:                      # [시,분,초] 또는 [분,초]
            seconds = seconds * 60 + n
        return seconds

    m = _UNIT_RE.fullmatch(raw)
    if not m or not any(m.groupdict().values()):
        raise ClipError(
            f"시간 형식을 이해할 수 없습니다: '{text}'\n"
            "  예) 90  /  1:30  /  01:02:03  /  1m30s"
        )
    g = m.groupdict()
    return (
        float(g["h"] or 0) * 3600
        + float(g["m"] or 0) * 60
        + float(g["s"] or 0)
    )


def format_timecode(seconds: float) -> str:
    """ffmpeg/yt-dlp 에 넘길 HH:MM:SS.mmm 문자열."""
    if seconds < 0:
        raise ClipError("시간은 0 이상이어야 합니다.")
    h, rest = divmod(float(seconds), 3600)
    m, s = divmod(rest, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


def format_human(seconds: float) -> str:
    """사람에게 보여줄 짧은 표기 (1:23:45 / 2:10)."""
    total = int(round(float(seconds)))
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def stamp(seconds: float) -> str:
    """파일명에 넣을 시간 표기 (00m01s30 → 00-01-30)."""
    total = float(seconds)
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    frac = round(s % 1, 3)
    base = f"{int(h):02d}-{int(m):02d}-{int(s):02d}"
    return f"{base}.{int(frac * 1000):03d}" if frac else base


_RANGE_SPLIT_RE = re.compile(r"\s*(?:~|\.\.|->|—|–|-)\s*")


def parse_range(text: str) -> tuple[float, float]:
    """'1:30-2:10', '90~120', '1:30..1:50' 을 (시작, 끝) 초로 바꾼다."""
    raw = str(text).strip()
    parts = [p for p in _RANGE_SPLIT_RE.split(raw) if p]
    if len(parts) != 2:
        raise ClipError(
            f"구간 형식을 이해할 수 없습니다: '{text}'\n"
            "  예) 1:30-2:10  /  90-120  /  00:01:30~00:02:10"
        )
    return parse_timecode(parts[0]), parse_timecode(parts[1])


@dataclass(frozen=True)
class Segment:
    """잘라낼 구간 하나."""

    start: float
    end: float

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ClipError(
                f"끝 시간({format_human(self.end)})이 "
                f"시작 시간({format_human(self.start)})보다 뒤여야 합니다."
            )

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def label(self) -> str:
        return f"{format_human(self.start)}~{format_human(self.end)}"

    @classmethod
    def of(cls, start, end=None, duration=None) -> "Segment":
        """시작 + (끝 | 길이) 조합으로 만든다."""
        s = parse_timecode(start)
        if end is not None and duration is not None:
            raise ClipError("--end 와 --duration 은 함께 쓸 수 없습니다.")
        if end is not None:
            return cls(s, parse_timecode(end))
        if duration is not None:
            return cls(s, s + parse_timecode(duration))
        raise ClipError("--end 또는 --duration 중 하나는 있어야 합니다.")


# ─────────────────────────────────────────────────────────────
# URL 파싱
# ─────────────────────────────────────────────────────────────

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
# /shorts/<id>, /embed/<id>, /live/<id>, /v/<id>
_PATH_ID_RE = re.compile(r"^/(?:shorts|embed|live|v)/([A-Za-z0-9_-]{11})")

_YOUTUBE_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "music.youtube.com", "youtube-nocookie.com",
    "www.youtube-nocookie.com", "youtu.be", "www.youtu.be",
}


@dataclass(frozen=True)
class VideoRef:
    """정규화된 유튜브 영상 참조."""

    video_id: str
    url: str
    start_hint: float | None = None   # 링크에 붙어 있던 t=/start= 값


def parse_youtube_url(raw: str) -> VideoRef:
    """유튜브 링크(또는 영상 ID)에서 영상 ID와 t= 시작 시간을 뽑는다."""
    text = str(raw).strip().strip("<>").strip()
    if not text:
        raise ClipError("유튜브 링크가 비어 있습니다.")

    if _VIDEO_ID_RE.match(text):
        return VideoRef(text, f"https://www.youtube.com/watch?v={text}")

    candidate = text if "//" in text else f"https://{text}"
    parsed = urlparse(candidate)
    host = (parsed.netloc or "").lower().split(":")[0]
    if host not in _YOUTUBE_HOSTS:
        raise ClipError(
            f"유튜브 링크가 아닙니다: {raw}\n"
            "  예) https://www.youtube.com/watch?v=XXXXXXXXXXX"
        )

    query = parse_qs(parsed.query)
    video_id: str | None = None

    if host.endswith("youtu.be"):
        tail = parsed.path.lstrip("/").split("/")[0]
        if _VIDEO_ID_RE.match(tail):
            video_id = tail
    else:
        m = _PATH_ID_RE.match(parsed.path)
        if m:
            video_id = m.group(1)
        elif query.get("v") and _VIDEO_ID_RE.match(query["v"][0]):
            video_id = query["v"][0]

    if not video_id:
        raise ClipError(f"링크에서 영상 ID를 찾지 못했습니다: {raw}")

    start_hint: float | None = None
    for key in ("t", "start"):
        if query.get(key):
            try:
                start_hint = parse_timecode(query[key][0])
            except ClipError:
                start_hint = None
            if start_hint is not None:
                break
    if start_hint is None and parsed.fragment.startswith("t="):
        try:
            start_hint = parse_timecode(parsed.fragment[2:])
        except ClipError:
            start_hint = None

    return VideoRef(
        video_id=video_id,
        url=f"https://www.youtube.com/watch?v={video_id}",
        start_hint=start_hint,
    )


# ─────────────────────────────────────────────────────────────
# 외부 도구
# ─────────────────────────────────────────────────────────────

_INSTALL_HINT = {
    "yt-dlp": "pip install -r requirements.txt  (또는 pip install -U yt-dlp)",
    "ffmpeg": "macOS: brew install ffmpeg / Ubuntu: sudo apt install ffmpeg",
    "ffprobe": "ffmpeg 를 설치하면 함께 들어온다.",
}


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise ClipError(
            f"'{name}' 을(를) 찾을 수 없습니다. 설치 후 다시 실행하세요.\n"
            f"  → {_INSTALL_HINT.get(name, '')}"
        )
    return path


def _run(cmd: list[str], *, quiet: bool = False) -> subprocess.CompletedProcess:
    """외부 명령 실행. 실패하면 stderr 끝부분을 담아 ClipError 로 올린다."""
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE if quiet else None,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-6:])
        raise ClipError(f"{Path(cmd[0]).name} 실행 실패 (코드 {proc.returncode})\n{tail}")
    return proc


def _auth_args(cookies: str | None, cookies_from_browser: str | None) -> list[str]:
    """로그인/연령제한 영상용 쿠키 옵션."""
    args: list[str] = []
    if cookies:
        args += ["--cookies", cookies]
    if cookies_from_browser:
        args += ["--cookies-from-browser", cookies_from_browser]
    return args


def probe(
    url: str,
    *,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
) -> dict:
    """영상 메타데이터(제목/길이/챕터/라이선스)를 가져온다. 다운로드는 하지 않는다."""
    ref = parse_youtube_url(url)
    cmd = [
        require_tool("yt-dlp"),
        "--no-playlist", "--no-warnings", "--skip-download",
        "--dump-single-json",
        *_auth_args(cookies, cookies_from_browser),
        ref.url,
    ]
    proc = _run(cmd, quiet=True)
    try:
        info = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise ClipError("영상 정보를 해석하지 못했습니다.") from e
    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "duration": info.get("duration"),
        "license": info.get("license"),
        "webpage_url": info.get("webpage_url") or ref.url,
        "chapters": info.get("chapters") or [],
        "subtitles": sorted(info.get("subtitles") or {}),
        "auto_subtitles": sorted(info.get("automatic_captions") or {}),
    }


# ─────────────────────────────────────────────────────────────
# 다운로드 / 컷
# ─────────────────────────────────────────────────────────────

def _format_selector(quality: int, audio_only: bool) -> str:
    """숏츠 편집에 무난한 포맷 선택자.

    편집 호환성을 위해 avc1(H.264) + mp4a(AAC) 를 우선한다.
    """
    if audio_only:
        return "bestaudio/best"
    q = int(quality)
    return (
        f"bv*[height<={q}][vcodec^=avc1]+ba[acodec^=mp4a]/"
        f"bv*[height<={q}]+ba/b[height<={q}]/b"
    )


def _sanitize(name: str, limit: int = 60) -> str:
    """파일명에 쓸 수 있게 정리한다(한글은 유지)."""
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]+', "", str(name)).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned[:limit].strip() or "clip")


def build_section_command(
    ref: VideoRef,
    segment: Segment,
    *,
    out_template: str,
    print_file: Path,
    quality: int = 1080,
    audio_only: bool = False,
    precise: bool = True,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
    yt_dlp: str = "yt-dlp",
) -> list[str]:
    """구간만 내려받는 yt-dlp 명령을 만든다 (테스트 가능하도록 분리)."""
    cmd = [
        yt_dlp,
        "--no-playlist",
        "--newline",
        "--retries", "5",
        "--fragment-retries", "5",
        "--concurrent-fragments", "4",
        "-f", _format_selector(quality, audio_only),
        "--download-sections",
        f"*{format_timecode(segment.start)}-{format_timecode(segment.end)}",
    ]
    if precise:
        # 키프레임에 붙어 시작점이 밀리는 것을 막는다(경계만 재인코딩).
        cmd.append("--force-keyframes-at-cuts")
    if audio_only:
        cmd += ["-x", "--audio-format", "mp3", "--audio-quality", "0"]
    else:
        cmd += ["--merge-output-format", "mp4"]
    cmd += [
        "-o", out_template,
        "--print-to-file", "after_move:filepath", str(print_file),
        "--no-simulate",
        *_auth_args(cookies, cookies_from_browser),
        ref.url,
    ]
    return cmd


def build_cut_command(
    source: Path,
    segment: Segment,
    dest: Path,
    *,
    audio_only: bool = False,
    precise: bool = True,
    ffmpeg: str = "ffmpeg",
) -> list[str]:
    """이미 받아둔 원본에서 구간을 잘라내는 ffmpeg 명령을 만든다."""
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-stats", "-y",
        "-ss", format_timecode(segment.start),
        "-i", str(source),
        "-t", format_timecode(segment.duration),
    ]
    if audio_only:
        cmd += ["-vn", "-c:a", "libmp3lame", "-q:a", "2"]
    elif precise:
        cmd += [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
        ]
    else:
        # 빠르지만 키프레임 단위로만 잘린다.
        cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    cmd.append(str(dest))
    return cmd


def _resolve_output(print_file: Path, out_dir: Path, prefix: str) -> Path:
    """yt-dlp 가 실제로 만든 파일 경로를 찾는다."""
    if print_file.exists():
        lines = [l.strip() for l in print_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        if lines:
            path = Path(lines[-1])
            if path.exists():
                return path
    matches = sorted(out_dir.glob(f"{prefix}*"), key=lambda p: p.stat().st_mtime)
    if matches:
        return matches[-1]
    raise ClipError("다운로드는 끝났지만 결과 파일을 찾지 못했습니다.")


def build_source_command(
    ref: VideoRef,
    *,
    source_dir: Path,
    print_file: Path,
    quality: int = 1080,
    audio_only: bool = False,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
    yt_dlp: str = "yt-dlp",
) -> list[str]:
    """원본 전체를 내려받는 yt-dlp 명령을 만든다."""
    cmd = [
        yt_dlp,
        "--no-playlist", "--newline",
        "--retries", "5", "--fragment-retries", "5",
        "--concurrent-fragments", "4",
        "-f", _format_selector(quality, audio_only),
    ]
    if audio_only:
        cmd += ["-x", "--audio-format", "mp3", "--audio-quality", "0"]
    else:
        cmd += ["--merge-output-format", "mp4"]
    cmd += [
        "-o", str(Path(source_dir) / f"{ref.video_id}.%(ext)s"),
        "--print-to-file", "after_move:filepath", str(print_file),
        "--no-simulate",
        *_auth_args(cookies, cookies_from_browser),
        ref.url,
    ]
    return cmd


AUDIO_EXTS = {".mp3", ".m4a", ".opus", ".aac", ".wav"}
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov"}


def find_cached_source(
    ref: VideoRef,
    source_dir: Path = DEFAULT_SOURCE_DIR,
    *,
    audio_only: bool = False,
) -> Path | None:
    """이미 받아둔 원본이 있으면 그 경로를 준다.

    음성만 받아둔 mp3 를 영상 작업에 쓰면 안 되므로 확장자로 걸러낸다.
    """
    wanted = AUDIO_EXTS if audio_only else VIDEO_EXTS
    cached = [
        p for p in sorted(Path(source_dir).glob(f"{ref.video_id}.*"))
        if p.suffix.lower() in wanted
    ]
    return cached[0] if cached else None


def download_source(
    ref: VideoRef,
    *,
    source_dir: Path = DEFAULT_SOURCE_DIR,
    quality: int = 1080,
    audio_only: bool = False,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
    reuse: bool = True,
) -> Path:
    """원본 전체를 한 번만 내려받아 캐시한다 (구간 여러 개 뽑을 때 사용)."""
    source_dir = Path(source_dir)
    source_dir.mkdir(parents=True, exist_ok=True)

    if reuse:
        cached = find_cached_source(ref, source_dir, audio_only=audio_only)
        if cached:
            print(f"  원본 캐시 사용: {cached}")
            return cached

    yt_dlp = require_tool("yt-dlp")
    require_tool("ffmpeg")
    with tempfile.TemporaryDirectory() as tmp:
        print_file = Path(tmp) / "path.txt"
        cmd = build_source_command(
            ref,
            source_dir=source_dir,
            print_file=print_file,
            quality=quality,
            audio_only=audio_only,
            cookies=cookies,
            cookies_from_browser=cookies_from_browser,
            yt_dlp=yt_dlp,
        )
        print(f"  원본 다운로드: {ref.url}")
        _run(cmd)
        return _resolve_output(print_file, source_dir, ref.video_id)


def extract_segment(
    url: str,
    segment: Segment,
    *,
    out_dir: Path = DEFAULT_OUT_DIR,
    name: str | None = None,
    quality: int = 1080,
    audio_only: bool = False,
    precise: bool = True,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
    source: Path | None = None,
    dry_run: bool = False,
) -> Path:
    """구간 하나를 잘라 파일로 저장하고 그 경로를 돌려준다.

    source 가 주어지면 그 파일에서 자르고, 없으면 그 구간만 새로 내려받는다.
    """
    ref = parse_youtube_url(url)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = "mp3" if audio_only else "mp4"
    prefix = f"{ref.video_id}_{stamp(segment.start)}_{stamp(segment.end)}"

    # ── 이미 받아둔 원본에서 자르는 경로 ──
    if source is not None:
        if name:
            base = _sanitize(name)
        else:
            stem = _sanitize(Path(source).stem, 40)
            # 캐시 원본 파일명은 영상 ID 라서 그대로 붙이면 ID 가 두 번 들어간다.
            base = prefix if stem == ref.video_id else f"{prefix}_{stem}"
        dest = out_dir / f"{base}.{ext}"
        cmd = build_cut_command(
            Path(source), segment, dest,
            audio_only=audio_only, precise=precise,
            ffmpeg=require_tool("ffmpeg") if not dry_run else "ffmpeg",
        )
        if dry_run:
            print("  $ " + " ".join(cmd))
            return dest
        _run(cmd)
        return dest

    # ── 구간만 내려받는 경로 ──
    if name:
        template = str(out_dir / f"{_sanitize(name)}.%(ext)s")
        find_prefix = _sanitize(name)
    else:
        template = str(out_dir / f"{prefix}_%(title).50B.%(ext)s")
        find_prefix = prefix

    yt_dlp = "yt-dlp" if dry_run else require_tool("yt-dlp")
    if not dry_run:
        require_tool("ffmpeg")

    with tempfile.TemporaryDirectory() as tmp:
        print_file = Path(tmp) / "path.txt"
        cmd = build_section_command(
            ref, segment,
            out_template=template,
            print_file=print_file,
            quality=quality,
            audio_only=audio_only,
            precise=precise,
            cookies=cookies,
            cookies_from_browser=cookies_from_browser,
            yt_dlp=yt_dlp,
        )
        if dry_run:
            print("  $ " + " ".join(cmd))
            return out_dir / f"{find_prefix}.{ext}"
        _run(cmd)
        return _resolve_output(print_file, out_dir, find_prefix)


def extract_segments(
    url: str,
    segments: list[Segment],
    *,
    out_dir: Path = DEFAULT_OUT_DIR,
    name: str | None = None,
    quality: int = 1080,
    audio_only: bool = False,
    precise: bool = True,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
    keep_source: bool = False,
    dry_run: bool = False,
) -> list[Path]:
    """구간 여러 개를 잘라낸다.

    구간이 2개 이상이면 원본을 한 번만 받아 로컬에서 자른다(훨씬 빠르다).
    keep_source=True 면 구간이 1개여도 원본을 받아 캐시한다.
    """
    if not segments:
        raise ClipError("추출할 구간이 없습니다.")

    ref = parse_youtube_url(url)
    # 구간이 2개 이상이면 원본을 한 번만 받아 로컬에서 자르는 쪽이 빠르다.
    use_source = keep_source or len(segments) > 1

    source: Path | None = None
    if use_source:
        if dry_run:
            source = Path(DEFAULT_SOURCE_DIR) / f"{ref.video_id}.mp4"
            print(f"원본 1회 다운로드 → {source} (이후 로컬에서 {len(segments)}번 컷)")
            print("  $ " + " ".join(build_source_command(
                ref,
                source_dir=DEFAULT_SOURCE_DIR,
                print_file=Path("<임시파일>"),
                quality=quality,
                audio_only=audio_only,
                cookies=cookies,
                cookies_from_browser=cookies_from_browser,
            )))
        else:
            source = download_source(
                ref,
                quality=quality,
                audio_only=audio_only,
                cookies=cookies,
                cookies_from_browser=cookies_from_browser,
            )

    results: list[Path] = []
    for i, seg in enumerate(segments, 1):
        label = f"[{i}/{len(segments)}] {seg.label} ({seg.duration:.1f}초)"
        print(f"\n{label}")
        if seg.duration > SHORTS_MAX_SECONDS:
            print(f"  ⚠️  {SHORTS_MAX_SECONDS}초(숏츠 최대 길이)를 넘습니다.")
        clip_name = name if (name and len(segments) == 1) else (
            f"{_sanitize(name)}_{i:02d}" if name else None
        )
        path = extract_segment(
            ref.url, seg,
            out_dir=out_dir,
            name=clip_name,
            quality=quality,
            audio_only=audio_only,
            precise=precise,
            cookies=cookies,
            cookies_from_browser=cookies_from_browser,
            source=source,
            dry_run=dry_run,
        )
        print(f"  ✅ {path}")
        results.append(path)

    if source and not keep_source and not dry_run:
        source.unlink(missing_ok=True)
        print(f"\n원본 캐시 삭제: {source.name} (남기려면 --keep-source)")

    return results
