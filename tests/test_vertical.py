"""vertical 모듈의 순수 로직 테스트 (ffmpeg 실행 없이 명령만 검사)."""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import vertical as v  # noqa: E402
from src.youtube_clipper import ClipError  # noqa: E402


class TestFilter(unittest.TestCase):
    def test_all_modes_target_1080x1920(self):
        for mode in v.MODES:
            with self.subTest(mode=mode):
                f = v.build_vertical_filter(mode)
                self.assertIn("1080:1920", f)
                self.assertIn("setsar=1", f)

    def test_blur_keeps_whole_frame(self):
        f = v.build_vertical_filter("blur")
        self.assertIn("force_original_aspect_ratio=decrease", f)  # 원본이 안 잘린다
        self.assertIn("gblur", f)
        self.assertIn("overlay", f)

    def test_crop_fills_frame(self):
        f = v.build_vertical_filter("crop")
        self.assertIn("force_original_aspect_ratio=increase", f)
        self.assertIn("crop=1080:1920", f)
        self.assertNotIn("gblur", f)

    def test_pad_uses_solid_color(self):
        self.assertIn("color=black", v.build_vertical_filter("pad"))

    def test_unknown_mode(self):
        with self.assertRaises(ClipError):
            v.build_vertical_filter("무지개")

    def test_text_needs_font(self):
        with self.assertRaises(ClipError):
            v.build_vertical_filter("blur", textfiles=[Path("a.txt")], font=None)

    def test_one_drawtext_per_line(self):
        files = [Path("/tmp/line0.txt"), Path("/tmp/line1.txt")]
        f = v.build_vertical_filter("blur", textfiles=files, font="/font.ttf")
        self.assertEqual(f.count("drawtext"), 2)          # 줄마다 따로 → 가운데 정렬
        self.assertIn("x=(w-text_w)/2", f)
        ys = [int(m) for m in re.findall(r":y=(\d+)", f)]
        self.assertEqual(len(ys), 2)
        self.assertGreater(ys[1], ys[0])                  # 둘째 줄이 아래에


class TestPathEscaping(unittest.TestCase):
    def test_windows_path(self):
        out = v.escape_filter_path(r"C:\Windows\Fonts\malgun.ttf")
        self.assertEqual(out, r"'C\:/Windows/Fonts/malgun.ttf'")

    def test_posix_path(self):
        self.assertEqual(v.escape_filter_path("/usr/share/f.ttf"), "'/usr/share/f.ttf'")


class TestWrapText(unittest.TestCase):
    def test_wraps_by_length(self):
        out = v.wrap_text("이 장면 하나로 조회수가 터졌습니다", per_line=16)
        self.assertEqual(out.split("\n"), ["이 장면 하나로 조회수가", "터졌습니다"])

    def test_single_short_line(self):
        self.assertEqual(v.wrap_text("짧은 문구"), "짧은 문구")

    def test_empty(self):
        self.assertEqual(v.wrap_text("   "), "")

    def test_caps_at_four_lines(self):
        long_text = " ".join(["가나다라마바사"] * 12)
        self.assertLessEqual(len(v.wrap_text(long_text).split("\n")), 4)


class TestCommand(unittest.TestCase):
    def test_command_shape(self):
        cmd = v.build_vertical_command(Path("in.mp4"), Path("out.mp4"), mode="pad")
        self.assertIn("-vf", cmd)
        self.assertIn("libx264", cmd)
        self.assertIn("+faststart", cmd)
        self.assertEqual(cmd[-1], "out.mp4")

    def test_missing_source(self):
        with self.assertRaises(ClipError):
            v.make_vertical("없는파일.mp4")


if __name__ == "__main__":
    unittest.main()
