"""브라우저에서 클릭만으로 유튜브 구간을 잘라내는 로컬 웹 UI.

실행:
    Windows : 실행.bat 더블클릭
    직접 실행: python -m src.shorts_ui

동작:
    링크 붙여넣기 → 영상이 화면에 뜬다 → 원하는 지점에서
    [여기가 시작] / [여기가 끝] 을 누른다 → [클립 만들기].
    타임코드를 손으로 적을 필요가 없다.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

import gradio as gr

from . import clip_finder, config
from . import vertical as vt
from . import youtube_clipper as yc

OUT_DIR = Path("outputs/clips")

# ─────────────────────────────────────────────────────────────
# 브라우저 쪽 스크립트
#   유튜브 IFrame Player API 로 재생 위치를 읽어 입력칸에 채워 넣는다.
# ─────────────────────────────────────────────────────────────
HEAD = """
<script src="https://www.youtube.com/iframe_api"></script>
<script>
// API 로드 완료를 기다리는 약속(Promise)
window.__ytReady = new Promise(function (resolve) {
  window.onYouTubeIframeAPIReady = function () { resolve(true); };
  // 이미 로드된 경우 대비
  if (window.YT && window.YT.Player) { resolve(true); }
});

// 초 → "M:SS.s" (입력칸에 채울 표기)
window.__fmtTime = function (t) {
  if (!isFinite(t) || t < 0) t = 0;
  var m = Math.floor(t / 60);
  var s = t - m * 60;
  var h = Math.floor(m / 60);
  m = m - h * 60;
  var ss = (s < 10 ? "0" : "") + s.toFixed(1);
  return h > 0 ? h + ":" + (m < 10 ? "0" : "") + m + ":" + ss : m + ":" + ss;
};

// Gradio 입력칸에 값을 넣고 변경을 알린다(Svelte 가 알아채도록 input 이벤트 발생)
window.__setBox = function (boxId, text) {
  var el = document.querySelector("#" + boxId + " textarea, #" + boxId + " input");
  if (!el) return;
  var proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement : HTMLInputElement;
  var setter = Object.getOwnPropertyDescriptor(proto.prototype, "value").set;
  setter.call(el, text);
  el.dispatchEvent(new Event("input", { bubbles: true }));
};

// 영상 불러오기
window.__loadPlayer = function (videoId, startAt) {
  if (!videoId) return;
  window.__ytReady.then(function () {
    var host = document.getElementById("yt-player-host");
    if (!host) return;
    host.innerHTML = '<div id="yt-player"></div>';
    window.__player = new YT.Player("yt-player", {
      width: "100%",
      height: "420",
      videoId: videoId,
      playerVars: { rel: 0, start: Math.floor(startAt || 0) }
    });
  });
};

window.__currentTime = function () {
  if (window.__player && window.__player.getCurrentTime) {
    return window.__player.getCurrentTime();
  }
  return null;
};

// [여기가 시작] / [여기가 끝]
window.__mark = function (which) {
  var t = window.__currentTime();
  if (t === null) { alert("먼저 링크를 넣고 [영상 불러오기] 를 눌러주세요."); return; }
  window.__setBox(which === "start" ? "box_start" : "box_end", window.__fmtTime(t));
};

// 현재 위치부터 N초 (숏츠 길이 맞추기)
window.__markSpan = function (seconds) {
  var t = window.__currentTime();
  if (t === null) { alert("먼저 링크를 넣고 [영상 불러오기] 를 눌러주세요."); return; }
  window.__setBox("box_start", window.__fmtTime(t));
  window.__setBox("box_end", window.__fmtTime(t + seconds));
};
</script>
<style>
  #yt-player-host { min-height: 240px; border-radius: 10px; overflow: hidden; }
  #yt-player-host .placeholder {
    display: flex; align-items: center; justify-content: center;
    height: 240px; background: #f3f4f6; color: #6b7280; border-radius: 10px;
    font-size: 15px;
  }
</style>
"""

PLAYER_PLACEHOLDER = (
    '<div id="yt-player-host">'
    '<div class="placeholder">① 위에 유튜브 링크를 붙여넣고 [영상 불러오기] 를 누르세요</div>'
    "</div>"
)


# ─────────────────────────────────────────────────────────────
# 콜백 (순수 로직은 테스트 가능하도록 분리)
# ─────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────
# 오류 안내
#   yt-dlp/ffmpeg 원문 대신 무엇을 하면 되는지 한국어로 알려준다.
# ─────────────────────────────────────────────────────────────
ERROR_HINTS: list[tuple[str, str]] = [
    ("not a bot",
     "유튜브가 사람 확인을 요구합니다. **[고급 설정] → 로그인 쿠키 사용**을 "
     "`chrome`(쓰는 브라우저)으로 바꾸고 다시 눌러보세요."),
    ("Sign in to confirm",
     "유튜브 로그인이 필요한 영상입니다. **[고급 설정] → 로그인 쿠키 사용**을 켜주세요."),
    ("age-restricted",
     "연령 제한 영상입니다. **[고급 설정] → 로그인 쿠키 사용**을 켜주세요 "
     "(해당 브라우저에 유튜브 로그인이 되어 있어야 합니다)."),
    ("Private video", "비공개 영상이라 받을 수 없습니다."),
    ("members-only", "채널 멤버십 전용 영상이라 받을 수 없습니다."),
    ("Video unavailable",
     "삭제되었거나 이 지역에서 볼 수 없는 영상입니다. 링크를 다시 확인해주세요."),
    ("Unable to connect",
     "인터넷 연결이 막혀 있습니다. 회사망·학교망·VPN 을 쓰고 있다면 해제하고 다시 시도하세요."),
    ("proxy",
     "인터넷 연결이 막혀 있습니다. 회사망·학교망·VPN 을 쓰고 있다면 해제하고 다시 시도하세요."),
    ("HTTP Error 403",
     "유튜브가 바뀌어 다운로더가 낡았을 수 있습니다. 창을 닫았다가 **실행.bat** 을 "
     "다시 더블클릭하면 자동으로 최신 버전을 받아옵니다."),
    ("'ffmpeg' 을(를) 찾을 수 없습니다",
     "영상 처리기(ffmpeg)가 설치되어 있지 않습니다. 창을 닫고 **실행.bat** 을 "
     "다시 더블클릭하세요. 그래도 같은 문구가 나오면 화면 위의 "
     "**[🔧 내 PC 점검]** 을 눌러 결과를 알려주세요."),
    ("ffmpeg 실행 실패",
     "영상을 자르다가 실패했습니다. 구간이 너무 길거나(수십 분), 저장 공간이 "
     "부족하거나, 원본이 손상된 경우입니다. 구간을 3분 이내로 줄여 다시 "
     "시도해보세요. 아래 [자세한 오류 내용] 을 열면 원인이 나옵니다."),
    ("세로 변환 실패",
     "세로 변환에 실패했습니다. 구간을 짧게 줄이거나, [숏츠 모양] 에서 "
     "**위아래 검은 여백** 으로 바꿔 다시 시도해보세요."),
    ("Postprocessing",
     "내려받은 뒤 영상을 합치는 단계에서 실패했습니다. 구간을 짧게 줄여 "
     "다시 시도해보세요."),
    ("ANTHROPIC_API_KEY",
     "Claude API 키가 없습니다. 아래 **[AI 설정]** 을 열고 키를 넣은 뒤 "
     "[저장] 을 누르세요. 키는 https://console.anthropic.com 에서 발급합니다."),
    ("credit balance",
     "Claude API 잔액이 부족합니다. console.anthropic.com 에서 결제 수단을 등록해주세요."),
    ("authentication_error",
     "Claude API 키가 올바르지 않습니다. [AI 설정] 에서 키를 다시 넣어주세요."),
    ("폰트",
     "한글 폰트를 찾지 못해 문구를 넣을 수 없습니다. **영상에 넣을 문구**를 비우고 "
     "다시 시도하거나, 나눔고딕을 설치해주세요."),
    ("No space left",
     "저장 공간이 부족합니다. 디스크를 비우고 다시 시도하세요."),
]


def explain_error(raw: str) -> str:
    """오류 원문에서 대응 방법을 찾아 안내문으로 바꾼다. 원문은 접어서 함께 보여준다."""
    text = str(raw)
    hint = next(
        (msg for key, msg in ERROR_HINTS if key.lower() in text.lower()),
        "영상을 가져오지 못했습니다. 링크를 확인하고 다시 시도해주세요.",
    )
    return (
        f"❌ {hint}\n\n"
        "<details><summary>자세한 오류 내용</summary>\n\n"
        f"```\n{text.strip()}\n```\n</details>"
    )



def describe_video(info: dict) -> str:
    """영상 정보를 화면용 마크다운으로."""
    dur = info.get("duration")
    lines = [
        f"**{info.get('title') or '(제목 없음)'}**",
        f"채널: {info.get('uploader') or '-'} · "
        f"길이: {yc.format_human(dur) if dur else '-'} · "
        f"라이선스: {info.get('license') or '표준 유튜브 라이선스'}",
    ]
    if not info.get("license"):
        lines.append(
            "> ⚠️ 표준 라이선스 영상입니다. 원본을 그대로 숏츠로 올리면 "
            "저작권 클레임 대상이 될 수 있습니다."
        )
    return "\n\n".join(lines)


def chapter_choices(info: dict) -> list[str]:
    """챕터를 '시작~끝  제목' 형태의 선택지로."""
    out = []
    for c in info.get("chapters") or []:
        start = yc.format_human(c.get("start_time", 0))
        end = yc.format_human(c.get("end_time", 0))
        out.append(f"{start}~{end}  {c.get('title', '')}".strip())
    return out


def parse_chapter_choice(choice: str) -> tuple[str, str]:
    """선택한 챕터에서 시작·끝 시간 문자열을 뽑는다."""
    head = str(choice).split("  ")[0]
    start, end = yc.parse_range(head)
    return yc.format_human(start), yc.format_human(end)


def duration_label(start: str, end: str) -> str:
    """시작·끝 입력에 따라 길이와 숏츠 적합 여부를 알려준다."""
    if not start or not end:
        return "시작과 끝을 정해주세요."
    try:
        seg = yc.Segment(yc.parse_timecode(start), yc.parse_timecode(end))
    except yc.ClipError as e:
        return f"⚠️ {e}"
    secs = seg.duration
    if secs > yc.SHORTS_MAX_SECONDS:
        note = f"숏츠 최대 {yc.SHORTS_MAX_SECONDS // 60}분을 넘습니다"
    elif secs < 5:
        note = "너무 짧습니다"
    else:
        note = "숏츠 길이로 적당합니다"
    return f"길이 **{secs:.1f}초** — {note}"



ENV_PATH = Path(".env")


def save_api_key(key: str) -> str:
    """입력받은 Claude API 키를 .env 에 저장한다 (.env 는 git 에 올라가지 않는다)."""
    key = (key or "").strip()
    if not key:
        return "키를 입력해주세요."
    if not key.startswith("sk-"):
        return "⚠️ 키 형식이 올바르지 않습니다. `sk-ant-` 로 시작하는 키를 넣어주세요."

    lines = []
    if ENV_PATH.exists():
        lines = [
            l for l in ENV_PATH.read_text(encoding="utf-8").splitlines()
            if not l.startswith("ANTHROPIC_API_KEY=")
        ]
    lines.append(f"ANTHROPIC_API_KEY={key}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ["ANTHROPIC_API_KEY"] = key
    return "✅ 저장했습니다. 이제 [AI가 골라주기] 를 쓸 수 있습니다."


def api_key_ready() -> bool:
    try:
        config.require_api_key()
        return True
    except RuntimeError:
        return False


def suggestion_label(index: int, row: dict) -> str:
    """추천 한 줄을 선택지 문구로."""
    seg = yc.Segment(row["start"], row["end"])
    return (
        f"{index}. {seg.label} ({seg.duration:.0f}초) · 적합도 {row['score']}/10"
        f" — {row['title']}"
    )


def system_report() -> str:
    """지금 이 PC 에서 무엇이 준비됐는지 실제로 실행해 확인한다.

    "왜 안 되는지" 를 추측하지 않기 위한 진단 화면이다.
    """
    lines: list[str] = []

    lines.append(f"- 파이썬 {sys.version.split()[0]}  `{sys.executable}`")

    # yt-dlp
    path = shutil.which("yt-dlp")
    if path:
        try:
            version = subprocess.run([path, "--version"], capture_output=True,
                                     text=True, timeout=20).stdout.strip()
            lines.append(f"- ✅ yt-dlp {version}")
        except Exception as e:  # noqa: BLE001
            lines.append(f"- ⚠️ yt-dlp 를 실행하지 못했습니다: {e}")
    else:
        lines.append("- ❌ yt-dlp 없음 → 실행.bat 을 다시 더블클릭하세요")

    # ffmpeg: 찾기만 하지 말고 실제로 실행해본다
    ffmpeg = yc.find_ffmpeg()
    if not ffmpeg:
        lines.append("- ❌ ffmpeg 를 찾지 못했습니다")
        try:
            import imageio_ffmpeg  # noqa: F401
            lines.append("  - imageio-ffmpeg 는 설치돼 있는데 실행 파일이 없습니다")
        except Exception as e:  # noqa: BLE001
            lines.append(f"  - imageio-ffmpeg 를 불러오지 못했습니다: {e}")
    else:
        try:
            first = subprocess.run([ffmpeg, "-version"], capture_output=True,
                                   text=True, timeout=20).stdout.splitlines()[0]
            lines.append(f"- ✅ ffmpeg  `{ffmpeg}`")
            lines.append(f"  - {first}")
        except Exception as e:  # noqa: BLE001
            lines.append(f"- ⚠️ ffmpeg 는 찾았지만 실행되지 않습니다 (`{ffmpeg}`): {e}")

    # 자바스크립트 런타임 (유튜브 추출에 쓰인다. 없으면 일부 화질이 빠질 수 있다)
    runtime = yc.find_js_runtime()
    if runtime:
        lines.append(f"- ✅ 자바스크립트 런타임: {runtime}")
    else:
        lines.append(
            "- ⚠️ 자바스크립트 런타임 없음 — 추출은 되지만 일부 화질이 빠질 수 "
            "있습니다. 명령 프롬프트에서 `winget install DenoLand.Deno` 로 "
            "설치하면 좋아집니다(선택)."
        )

    # 저장 공간
    try:
        free = shutil.disk_usage(OUT_DIR.resolve().anchor or ".").free / 1024**3
        mark = "✅" if free >= 2 else "⚠️"
        lines.append(f"- {mark} 저장 공간 여유 {free:.1f}GB")
    except Exception:  # noqa: BLE001
        pass

    lines.append(f"- 저장 위치 `{OUT_DIR.resolve()}`")
    return "\n".join(lines)


def startup_banner() -> str:
    """맨 위에 준비 상태를 한 줄로 보여준다."""
    ready = yc.find_ffmpeg() is not None and shutil.which("yt-dlp") is not None
    if ready:
        return ""
    return (
        "> ❌ **준비가 덜 됐습니다.** 영상 처리기(ffmpeg) 또는 다운로더(yt-dlp)를 "
        "찾지 못했습니다. 창을 닫고 **실행.bat** 을 다시 더블클릭하세요. "
        "그래도 같으면 아래 **[🔧 내 PC 점검]** 결과를 알려주세요."
    )


def _open_folder(path: Path) -> None:
    """탐색기/파인더로 결과 폴더 열기."""
    path = Path(path).resolve()
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception:
        pass


def build_app() -> gr.Blocks:
    with gr.Blocks(title="유튜브 구간 추출기") as app:
        info_state = gr.State({})

        gr.Markdown(
            "# ✂️ 유튜브 구간 추출기\n"
            "링크를 넣고 → 영상을 보다가 → **[여기가 시작] / [여기가 끝]** 을 누르고 → "
            "**[클립 만들기]**. 시간은 직접 입력해도 됩니다."
        )

        gr.Markdown(startup_banner())     # 준비가 안 됐을 때만 보인다
        with gr.Accordion("🔧 내 PC 점검 (안 될 때 열어보세요)", open=False):
            gr.Markdown(
                "무엇이 준비됐는지 실제로 실행해서 확인합니다. "
                "문제가 생기면 이 결과를 그대로 알려주세요."
            )
            check_btn = gr.Button("점검 실행")
            check_md = gr.Markdown("")

        gr.Markdown("### ① 영상 불러오기")
        with gr.Row():
            url_box = gr.Textbox(
                label="유튜브 링크",
                placeholder="https://www.youtube.com/watch?v=...",
                scale=5,
                autofocus=True,
            )
            load_btn = gr.Button("영상 불러오기", variant="secondary", scale=1)

        info_md = gr.Markdown("")
        player = gr.HTML(PLAYER_PLACEHOLDER)

        chapters = gr.Dropdown(
            label="챕터에서 고르기", choices=[], visible=False, interactive=True
        )

        gr.Markdown("### ② 구간 정하기")
        with gr.Row():
            suggest_btn = gr.Button(
                "🤖 AI가 골라주기 — 자막을 읽고 훅이 될 구간을 제안합니다",
                variant="secondary",
            )
        suggest_md = gr.Markdown("")
        suggest_radio = gr.Radio(label="추천 구간 (고르면 시간이 채워집니다)",
                                 choices=[], visible=False, interactive=True)
        plan_state = gr.State([])

        gr.Markdown("**직접 정하기** — 영상을 보다가 누르세요")
        with gr.Row():
            mark_start = gr.Button("⏱ 여기가 시작")
            mark_end = gr.Button("⏹ 여기가 끝")
            span15 = gr.Button("여기부터 15초")
            span30 = gr.Button("여기부터 30초")
            span60 = gr.Button("여기부터 60초")

        with gr.Row():
            start_box = gr.Textbox(label="시작", elem_id="box_start", placeholder="1:30")
            end_box = gr.Textbox(label="끝", elem_id="box_end", placeholder="2:10")
        length_md = gr.Markdown("시작과 끝을 정해주세요.", elem_id="length_md")

        gr.Markdown("### ③ 숏츠 모양")
        with gr.Row():
            vertical = gr.Checkbox(
                label="세로(9:16)로 변환 — 유튜브가 숏츠로 인식하려면 필요합니다",
                value=True,
                scale=2,
            )
            vmode = gr.Radio(
                label="변환 방식",
                choices=[(label, key) for key, label in vt.MODE_LABELS.items()],
                value="blur",
                scale=3,
            )
        with gr.Row():
            hook_text = gr.Textbox(
                label="영상에 넣을 문구 (선택)",
                placeholder="예: 이 장면 하나로 조회수가 터졌습니다",
                scale=3,
            )
            text_pos = gr.Radio(
                label="문구 위치",
                choices=[("위", "top"), ("가운데", "middle"), ("아래", "bottom")],
                value="top",
                scale=2,
            )

        with gr.Accordion("AI 설정 (구간 추천에 필요)", open=False):
            gr.Markdown(
                "Claude API 키가 있어야 [AI가 골라주기] 를 쓸 수 있습니다. "
                "https://console.anthropic.com 에서 발급받아 붙여넣으세요. "
                "키는 이 컴퓨터의 `.env` 파일에만 저장됩니다."
            )
            with gr.Row():
                api_key_box = gr.Textbox(
                    label="Claude API 키", type="password",
                    placeholder="sk-ant-...", scale=4,
                )
                save_key_btn = gr.Button("저장", scale=1)
            key_status_md = gr.Markdown("")

        with gr.Accordion("고급 설정", open=False):
            with gr.Row():
                quality = gr.Dropdown(
                    label="화질(최대)", choices=["1080", "720", "480"], value="1080"
                )
                audio_only = gr.Checkbox(label="음성만 추출 (mp3)", value=False)
                fast = gr.Checkbox(
                    label="빠르게 자르기 (시작점이 몇 초 밀릴 수 있음)", value=False
                )
            cookies_browser = gr.Dropdown(
                label="로그인 쿠키 사용 (연령제한·봇 확인 영상일 때)",
                choices=["사용 안 함", "chrome", "edge", "firefox", "whale"],
                value="사용 안 함",
            )

        gr.Markdown("### ④ 만들기")
        make_btn = gr.Button("✂️ 클립 만들기", variant="primary", size="lg")
        status_md = gr.Markdown("")
        result_video = gr.Video(label="결과 미리보기", visible=False)
        result_file = gr.File(label="파일 저장하기", visible=False)
        open_btn = gr.Button("📂 저장 폴더 열기", visible=False)

        # ── 영상 불러오기 ──
        def on_load(url):
            if not str(url).strip():
                return ("링크를 먼저 넣어주세요.", {}, gr.update(visible=False), "", "")
            try:
                ref = yc.parse_youtube_url(url)
                info = yc.probe(ref.url)
            except yc.ClipError as e:
                return (explain_error(e), {}, gr.update(visible=False), "", "")

            choices = chapter_choices(info)
            start_default = yc.format_human(ref.start_hint) if ref.start_hint else ""
            return (
                describe_video(info),
                info,
                gr.update(choices=choices, visible=bool(choices), value=None),
                start_default,
                "",
            )

        load_btn.click(
            on_load,
            inputs=url_box,
            outputs=[info_md, info_state, chapters, start_box, end_box],
        ).then(
            None,
            inputs=url_box,
            outputs=None,
            js="(u) => { try { var id = u.match(/(?:v=|be\\/|shorts\\/|embed\\/|live\\/)([A-Za-z0-9_-]{11})/); "
               "if (!id) { id = u.trim().match(/^([A-Za-z0-9_-]{11})$/); } "
               "if (id) { window.__loadPlayer(id[1], 0); } } catch(e) {} }",
        )
        url_box.submit(
            on_load,
            inputs=url_box,
            outputs=[info_md, info_state, chapters, start_box, end_box],
        ).then(
            None,
            inputs=url_box,
            outputs=None,
            js="(u) => { try { var id = u.match(/(?:v=|be\\/|shorts\\/|embed\\/|live\\/)([A-Za-z0-9_-]{11})/); "
               "if (!id) { id = u.trim().match(/^([A-Za-z0-9_-]{11})$/); } "
               "if (id) { window.__loadPlayer(id[1], 0); } } catch(e) {} }",
        )

        # ── 재생 위치를 입력칸에 채우기 (브라우저에서만 동작) ──
        mark_start.click(None, js="() => window.__mark('start')")
        mark_end.click(None, js="() => window.__mark('end')")
        span15.click(None, js="() => window.__markSpan(15)")
        span30.click(None, js="() => window.__markSpan(30)")
        span60.click(None, js="() => window.__markSpan(60)")

        # ── 챕터 선택 → 시작/끝 채우기 ──
        def on_chapter(choice):
            if not choice:
                return gr.update(), gr.update()
            start, end = parse_chapter_choice(choice)
            return start, end

        chapters.change(on_chapter, inputs=chapters, outputs=[start_box, end_box])

        # ── 길이 안내 ──
        for box in (start_box, end_box):
            box.change(duration_label, inputs=[start_box, end_box], outputs=length_md)


        # ── AI 구간 추천 ──
        def on_suggest(url, cookies_browser, info):
            hidden = gr.update(visible=False)
            if not str(url).strip():
                yield "⚠️ 링크를 먼저 넣어주세요.", hidden, []
                return
            if not api_key_ready():
                yield (
                    "⚠️ Claude API 키가 없습니다. 아래 **[AI 설정]** 을 열고 "
                    "키를 넣어주세요.",
                    hidden, [],
                )
                return

            yield "⏳ 자막을 받아 읽는 중입니다... (30초~1분 걸립니다)", hidden, []
            try:
                plan, language = clip_finder.suggest_clips(
                    url,
                    count=5,
                    duration=(info or {}).get("duration"),
                    cookies_from_browser=(
                        None if cookies_browser == "사용 안 함" else cookies_browser
                    ),
                )
            except Exception as e:  # noqa: BLE001 - 사용자에게 안내로 바꿔 보여준다
                yield explain_error(e), hidden, []
                return

            rows = [
                {
                    "start": s.start_seconds, "end": s.end_seconds,
                    "title": s.title, "hook": s.hook_text,
                    "score": s.score, "reason": s.reason,
                }
                for s in plan.suggestions
            ]
            choices = [(suggestion_label(i, r), i - 1) for i, r in enumerate(rows, 1)]

            lines = [f"**영상 요약** — {plan.video_summary}", ""]
            for i, r in enumerate(rows, 1):
                lines.append(f"{i}. {r['reason']}")
            lines += [
                "",
                f"> ⚠️ 자막({language})만 읽고 낸 추천입니다. 화면에 무엇이 나오는지는 "
                "모릅니다. 만든 뒤 미리보기로 꼭 확인하세요.",
            ]
            yield (
                "\n".join(lines),
                gr.update(choices=choices, visible=True, value=None),
                rows,
            )

        def on_pick(choice, rows):
            if choice is None or not rows:
                return gr.update(), gr.update(), gr.update()
            row = rows[int(choice)]
            return (
                yc.format_human(row["start"]),
                yc.format_human(row["end"]),
                row["hook"],
            )

        # ── 클립 만들기 ──
        def on_make(url, start, end, quality, audio_only, fast, cookies_browser,
                    vertical, vmode, hook_text, text_pos):
            hidden = gr.update(visible=False)
            if not str(url).strip():
                yield "⚠️ 링크를 넣어주세요.", hidden, hidden, hidden
                return
            if not str(start).strip() or not str(end).strip():
                yield "⚠️ 시작과 끝 시간을 정해주세요.", hidden, hidden, hidden
                return
            try:
                segment = yc.Segment(yc.parse_timecode(start), yc.parse_timecode(end))
            except yc.ClipError as e:
                yield f"⚠️ {e}", hidden, hidden, hidden
                return

            if segment.duration > yc.SHORTS_MAX_SECONDS:
                yield (
                    f"⚠️ **{segment.duration / 60:.1f}분**짜리 구간입니다. "
                    f"숏츠는 3분까지만 올라가고, 이 길이는 변환에 몇 분씩 걸리며 "
                    f"실패하기도 쉽습니다. 그래도 진행합니다...",
                    hidden, hidden, hidden,
                )

            yield (
                f"⏳ {segment.label} 구간을 내려받아 자르는 중입니다... "
                "(길이·화질에 따라 수십 초 걸립니다)",
                hidden, hidden, hidden,
            )
            try:
                paths = yc.extract_segments(
                    url,
                    [segment],
                    out_dir=OUT_DIR,
                    quality=int(quality),
                    audio_only=bool(audio_only),
                    precise=not bool(fast),
                    cookies_from_browser=(
                        None if cookies_browser == "사용 안 함" else cookies_browser
                    ),
                )
            except yc.ClipError as e:
                yield explain_error(e), hidden, hidden, hidden
                return

            path = paths[0]

            # 숏츠는 세로여야 유튜브가 숏츠로 인식한다.
            shape = "가로 원본"
            if vertical and not audio_only:
                yield (
                    f"⏳ 세로(9:16)로 바꾸는 중입니다... "
                    f"({vt.MODE_LABELS.get(vmode, '')})",
                    hidden, hidden, hidden,
                )
                try:
                    path = vt.make_vertical(
                        path, mode=vmode, text=hook_text or None,
                        position=text_pos, replace=True,
                    )
                    shape = "1080x1920 세로"
                except yc.ClipError as e:
                    yield explain_error(e), hidden, hidden, hidden
                    return

            size_mb = path.stat().st_size / 1024 / 1024
            yield (
                f"✅ 완료 — `{path.name}`\n\n"
                f"{shape} · {segment.duration:.1f}초 · {size_mb:.1f}MB",
                gr.update(value=str(path), visible=not audio_only),
                gr.update(value=str(path), visible=True),
                gr.update(visible=True),
            )

        suggest_btn.click(
            on_suggest,
            inputs=[url_box, cookies_browser, info_state],
            outputs=[suggest_md, suggest_radio, plan_state],
        )
        suggest_radio.change(
            on_pick,
            inputs=[suggest_radio, plan_state],
            outputs=[start_box, end_box, hook_text],
        )
        save_key_btn.click(save_api_key, inputs=api_key_box, outputs=key_status_md)
        check_btn.click(lambda: system_report(), outputs=check_md)

        make_btn.click(
            on_make,
            inputs=[url_box, start_box, end_box, quality, audio_only, fast, cookies_browser,
                    vertical, vmode, hook_text, text_pos],
            outputs=[status_md, result_video, result_file, open_btn],
        )
        open_btn.click(lambda: _open_folder(OUT_DIR), inputs=None, outputs=None)

    return app


# head/theme 는 Gradio 6 부터 launch() 인자다.
LAUNCH_KWARGS = dict(head=HEAD, theme=gr.themes.Soft())


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if yc.find_ffmpeg() is None:
        print("\n" + "=" * 52)
        print("⚠️  영상 처리기(ffmpeg)를 찾지 못했습니다.")
        print("   화면 위의 [🔧 내 PC 점검] 을 눌러 결과를 알려주세요.")
        print("=" * 52)
    print("\n브라우저가 자동으로 열립니다. 안 열리면 아래 주소를 직접 여세요.")
    build_app().launch(inbrowser=True, **LAUNCH_KWARGS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
