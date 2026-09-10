"""shorts_ui 의 순수 로직 테스트 (gradio 가 없으면 건너뛴다)."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

HAS_GRADIO = importlib.util.find_spec("gradio") is not None

if HAS_GRADIO:
    from src import shorts_ui as ui


@unittest.skipUnless(HAS_GRADIO, "gradio 미설치")
class TestUiHelpers(unittest.TestCase):
    INFO = {
        "title": "테스트 영상",
        "uploader": "테스트 채널",
        "duration": 605,
        "license": None,
        "chapters": [
            {"start_time": 0, "end_time": 90, "title": "인트로"},
            {"start_time": 90, "end_time": 240, "title": "본론"},
        ],
    }

    def test_describe_video(self):
        text = ui.describe_video(self.INFO)
        self.assertIn("테스트 영상", text)
        self.assertIn("10:05", text)          # 605초
        self.assertIn("저작권", text)          # 표준 라이선스 경고

    def test_describe_video_cc_has_no_warning(self):
        info = dict(self.INFO, license="Creative Commons Attribution license (reuse allowed)")
        self.assertNotIn("저작권 클레임", ui.describe_video(info))

    def test_chapter_choices(self):
        choices = ui.chapter_choices(self.INFO)
        self.assertEqual(choices[0], "0:00~1:30  인트로")
        self.assertEqual(len(choices), 2)

    def test_parse_chapter_choice(self):
        self.assertEqual(ui.parse_chapter_choice("1:30~4:00  본론"), ("1:30", "4:00"))

    def test_duration_label(self):
        self.assertIn("40.0초", ui.duration_label("1:30", "2:10"))
        self.assertIn("적당", ui.duration_label("1:30", "2:10"))
        self.assertIn("너무 짧", ui.duration_label("1:30", "1:33"))
        self.assertIn("넘습니다", ui.duration_label("0:00", "5:00"))

    def test_duration_label_invalid(self):
        self.assertIn("⚠️", ui.duration_label("2:10", "1:30"))
        self.assertIn("정해주세요", ui.duration_label("", ""))

    def test_app_builds(self):
        self.assertIsNotNone(ui.build_app())


@unittest.skipUnless(HAS_GRADIO, "gradio 미설치")
class TestErrorMessages(unittest.TestCase):
    def test_bot_check_hint(self):
        msg = ui.explain_error("ERROR: Sign in to confirm you're not a bot")
        self.assertIn("로그인 쿠키", msg)
        self.assertIn("자세한 오류", msg)      # 원문도 접어서 제공

    def test_network_hint(self):
        self.assertIn("VPN", ui.explain_error("Unable to connect to proxy"))

    def test_unavailable_hint(self):
        self.assertIn("삭제", ui.explain_error("ERROR: Video unavailable"))

    def test_ffmpeg_hint(self):
        self.assertIn("실행.bat", ui.explain_error("'ffmpeg' 을(를) 찾을 수 없습니다."))

    def test_unknown_falls_back(self):
        msg = ui.explain_error("무슨 일인지 모를 오류")
        self.assertIn("링크를 확인", msg)



@unittest.skipUnless(HAS_GRADIO, "gradio 미설치")
class TestFfmpegErrorsAreDistinct(unittest.TestCase):
    """ffmpeg 관련 오류를 '설치 안 됨' 하나로 뭉뚱그리면 원인을 못 찾는다."""

    def test_missing_ffmpeg(self):
        msg = ui.explain_error("'ffmpeg' 을(를) 찾을 수 없습니다. 설치 후 다시 실행하세요.")
        self.assertIn("설치되어 있지 않습니다", msg)

    def test_ffmpeg_run_failure_is_not_reported_as_missing(self):
        msg = ui.explain_error("ffmpeg 실행 실패 (코드 1)\nConversion failed!")
        self.assertNotIn("설치되어 있지", msg)
        self.assertIn("자르다가 실패", msg)

    def test_vertical_failure(self):
        msg = ui.explain_error("세로 변환 실패\nError while filtering")
        self.assertIn("세로 변환에 실패", msg)

    def test_postprocessing_failure(self):
        self.assertIn("합치는 단계", ui.explain_error("ERROR: Postprocessing: Error opening output"))


@unittest.skipUnless(HAS_GRADIO, "gradio 미설치")
class TestSystemReport(unittest.TestCase):
    def test_report_mentions_tools(self):
        text = ui.system_report()
        self.assertIn("파이썬", text)
        self.assertIn("yt-dlp", text)
        self.assertIn("ffmpeg", text)

    def test_banner_empty_when_ready(self):
        # 이 환경에는 둘 다 있으므로 배너가 뜨면 안 된다
        if ui.yc.find_ffmpeg() and ui.shutil.which("yt-dlp"):
            self.assertEqual(ui.startup_banner(), "")

    def test_banner_warns_when_ffmpeg_missing(self):
        original = ui.yc.find_ffmpeg
        ui.yc.find_ffmpeg = lambda: None
        try:
            self.assertIn("준비가 덜 됐습니다", ui.startup_banner())
            self.assertIn("❌ ffmpeg 를 찾지 못했습니다", ui.system_report())
        finally:
            ui.yc.find_ffmpeg = original


@unittest.skipUnless(HAS_GRADIO, "gradio 미설치")
class TestAgainstVideoLength(unittest.TestCase):
    """영상 길이를 넘는 구간은 만들기 전에 걸러야 한다.

    2:37:45 짜리 영상에서 2:37:28~3:16:11 을 잡아도 아무 경고가 없었다.
    """

    INFO = {"duration": 9465}          # 2:37:45

    def test_end_beyond_video(self):
        msg = ui.duration_label("2:37:28", "3:16:11", self.INFO)
        self.assertIn("영상 길이", msg)
        self.assertIn("2:37:45", msg)

    def test_start_beyond_video(self):
        self.assertIn("시작 시간이 영상 길이", ui.duration_label("3:00:00", "3:00:30", self.INFO))

    def test_inside_video_is_fine(self):
        msg = ui.duration_label("1:30:00", "1:30:30", self.INFO)
        self.assertIn("30.0초", msg)
        self.assertIn("적당", msg)

    def test_exactly_at_end_is_fine(self):
        self.assertIn("45.0초", ui.duration_label("2:37:00", "2:37:45", self.INFO))

    def test_without_video_info_still_works(self):
        self.assertIn("40.0초", ui.duration_label("1:30", "2:10"))
        self.assertIn("40.0초", ui.duration_label("1:30", "2:10", {}))


if __name__ == "__main__":
    unittest.main()
