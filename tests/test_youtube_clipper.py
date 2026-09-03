"""youtube_clipper 의 순수 로직 테스트 (네트워크·외부 도구 불필요).

실행: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import youtube_clipper as yc  # noqa: E402

VID = "NSYfVe36jw0"   # 테스트용 영상 ID (실제 통신은 하지 않는다)


class TestTimecode(unittest.TestCase):
    def test_formats(self):
        cases = {
            "90": 90.0,
            "90.5": 90.5,
            "1:30": 90.0,
            "0:01:30": 90.0,
            "01:02:03": 3723.0,
            "00:01:30.5": 90.5,
            "1m30s": 90.0,
            "1h2m3s": 3723.0,
            "45s": 45.0,
            "2m": 120.0,
            90: 90.0,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertAlmostEqual(yc.parse_timecode(text), expected)

    def test_invalid(self):
        for bad in ["", "어제", "1:2:3:4", "abc"]:
            with self.subTest(bad=bad), self.assertRaises(yc.ClipError):
                yc.parse_timecode(bad)

    def test_format_timecode(self):
        self.assertEqual(yc.format_timecode(90), "00:01:30.000")
        self.assertEqual(yc.format_timecode(3723.5), "01:02:03.500")

    def test_format_human(self):
        self.assertEqual(yc.format_human(90), "1:30")
        self.assertEqual(yc.format_human(3723), "1:02:03")

    def test_stamp(self):
        self.assertEqual(yc.stamp(90), "00-01-30")
        self.assertEqual(yc.stamp(3723.5), "01-02-03.500")


class TestRange(unittest.TestCase):
    def test_separators(self):
        for text in ["1:30-2:10", "1:30~2:10", "1:30..2:10", "90-130", "1:30 - 2:10"]:
            with self.subTest(text=text):
                start, end = yc.parse_range(text)
                self.assertAlmostEqual(start, 90.0)
                self.assertAlmostEqual(end, 130.0)

    def test_invalid(self):
        with self.assertRaises(yc.ClipError):
            yc.parse_range("1:30")


class TestSegment(unittest.TestCase):
    def test_end_before_start(self):
        with self.assertRaises(yc.ClipError):
            yc.Segment(120, 60)

    def test_of_duration(self):
        seg = yc.Segment.of("1:30", duration="45")
        self.assertAlmostEqual(seg.start, 90.0)
        self.assertAlmostEqual(seg.end, 135.0)
        self.assertAlmostEqual(seg.duration, 45.0)

    def test_of_requires_one(self):
        with self.assertRaises(yc.ClipError):
            yc.Segment.of("1:30")
        with self.assertRaises(yc.ClipError):
            yc.Segment.of("1:30", end="2:00", duration="30")

    def test_label(self):
        self.assertEqual(yc.Segment(90, 130).label, "1:30~2:10")


class TestUrlParsing(unittest.TestCase):
    def test_variants(self):
        urls = [
            f"https://www.youtube.com/watch?v={VID}",
            f"https://youtu.be/{VID}",
            f"https://m.youtube.com/watch?v={VID}",
            f"https://www.youtube.com/shorts/{VID}",
            f"https://www.youtube.com/embed/{VID}",
            f"https://www.youtube.com/live/{VID}",
            f"https://www.youtube.com/watch?v={VID}&list=PL123&index=2",
            f"www.youtube.com/watch?v={VID}",
            VID,
        ]
        for url in urls:
            with self.subTest(url=url):
                ref = yc.parse_youtube_url(url)
                self.assertEqual(ref.video_id, VID)
                self.assertEqual(ref.url, f"https://www.youtube.com/watch?v={VID}")

    def test_start_hint(self):
        cases = {
            f"https://youtu.be/{VID}?t=90": 90.0,
            f"https://www.youtube.com/watch?v={VID}&t=1m30s": 90.0,
            f"https://www.youtube.com/watch?v={VID}&t=90s": 90.0,
            f"https://www.youtube.com/embed/{VID}?start=45": 45.0,
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertAlmostEqual(yc.parse_youtube_url(url).start_hint, expected)

    def test_no_start_hint(self):
        self.assertIsNone(yc.parse_youtube_url(f"https://youtu.be/{VID}").start_hint)

    def test_rejects_non_youtube(self):
        for bad in ["https://vimeo.com/12345", "", "https://www.youtube.com/watch?v=short"]:
            with self.subTest(bad=bad), self.assertRaises(yc.ClipError):
                yc.parse_youtube_url(bad)


class TestCommands(unittest.TestCase):
    def setUp(self):
        self.ref = yc.parse_youtube_url(f"https://www.youtube.com/watch?v={VID}")
        self.seg = yc.Segment(90, 130)

    def _section_cmd(self, **kw):
        return yc.build_section_command(
            self.ref, self.seg,
            out_template="outputs/clips/%(title)s.%(ext)s",
            print_file=Path("/tmp/path.txt"),
            **kw,
        )

    def test_section_range_and_precision(self):
        cmd = self._section_cmd()
        self.assertIn("--download-sections", cmd)
        self.assertIn("*00:01:30.000-00:02:10.000", cmd)
        self.assertIn("--force-keyframes-at-cuts", cmd)   # 기본은 정밀 컷
        self.assertIn("mp4", cmd)
        self.assertEqual(cmd[-1], self.ref.url)

    def test_section_fast_mode(self):
        self.assertNotIn("--force-keyframes-at-cuts", self._section_cmd(precise=False))

    def test_section_audio_only(self):
        cmd = self._section_cmd(audio_only=True)
        self.assertIn("-x", cmd)
        self.assertIn("mp3", cmd)

    def test_quality_in_format_selector(self):
        cmd = self._section_cmd(quality=720)
        self.assertIn("height<=720", cmd[cmd.index("-f") + 1])

    def test_cookies_passthrough(self):
        cmd = self._section_cmd(cookies="c.txt", cookies_from_browser="chrome")
        self.assertIn("--cookies", cmd)
        self.assertIn("c.txt", cmd)
        self.assertIn("--cookies-from-browser", cmd)

    def test_cut_command_precise(self):
        cmd = yc.build_cut_command(Path("src.mp4"), self.seg, Path("out.mp4"))
        self.assertEqual(cmd[cmd.index("-ss") + 1], "00:01:30.000")
        self.assertEqual(cmd[cmd.index("-t") + 1], "00:00:40.000")   # 길이(끝-시작)
        self.assertIn("libx264", cmd)
        self.assertEqual(cmd[-1], "out.mp4")

    def test_cut_command_fast_copies_stream(self):
        cmd = yc.build_cut_command(Path("src.mp4"), self.seg, Path("out.mp4"), precise=False)
        self.assertIn("copy", cmd)
        self.assertNotIn("libx264", cmd)


class TestFilenameSanitize(unittest.TestCase):
    def test_keeps_korean_drops_illegal(self):
        self.assertEqual(yc._sanitize('가/나:다*라?'), "가나다라")

    def test_empty_falls_back(self):
        self.assertEqual(yc._sanitize("///"), "clip")


class TestOutputNaming(unittest.TestCase):
    """이름 규칙: <영상ID>_<시작>_<끝>[_제목].mp4"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "clips"
        self.addCleanup(self.tmp.cleanup)

    def test_local_cut_name_has_no_duplicate_id(self):
        source = Path(self.tmp.name) / f"{VID}.mp4"
        source.touch()
        path = yc.extract_segment(
            f"https://youtu.be/{VID}", yc.Segment(90, 130),
            out_dir=self.out, source=source, dry_run=True,
        )
        self.assertEqual(path.name, f"{VID}_00-01-30_00-02-10.mp4")

    def test_custom_name(self):
        source = Path(self.tmp.name) / f"{VID}.mp4"
        source.touch()
        path = yc.extract_segment(
            f"https://youtu.be/{VID}", yc.Segment(90, 130),
            out_dir=self.out, source=source, name="숏츠_1", dry_run=True,
        )
        self.assertEqual(path.name, "숏츠_1.mp4")

    def test_multi_segment_plan_downloads_source_once(self):
        segments = [yc.Segment(90, 130), yc.Segment(300, 345)]
        paths = yc.extract_segments(
            f"https://youtu.be/{VID}", segments, out_dir=self.out, dry_run=True,
        )
        self.assertEqual(len(paths), 2)
        self.assertEqual(len({p.name for p in paths}), 2)


class TestSourceCommand(unittest.TestCase):
    def test_no_download_sections(self):
        ref = yc.parse_youtube_url(f"https://youtu.be/{VID}")
        cmd = yc.build_source_command(
            ref, source_dir=Path("outputs/sources"), print_file=Path("/tmp/p.txt"),
        )
        self.assertNotIn("--download-sections", cmd)
        self.assertIn(f"outputs/sources/{VID}.%(ext)s", cmd)


class TestSourceCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.ref = yc.parse_youtube_url(f"https://youtu.be/{VID}")

    def test_finds_video(self):
        (self.dir / f"{VID}.mp4").touch()
        self.assertIsNotNone(yc.find_cached_source(self.ref, self.dir))

    def test_audio_cache_not_used_for_video(self):
        (self.dir / f"{VID}.mp3").touch()
        self.assertIsNone(yc.find_cached_source(self.ref, self.dir))
        self.assertIsNotNone(yc.find_cached_source(self.ref, self.dir, audio_only=True))

    def test_ignores_partial_download(self):
        (self.dir / f"{VID}.mp4.part").touch()
        self.assertIsNone(yc.find_cached_source(self.ref, self.dir))


class TestCacheIsNotDeleted(unittest.TestCase):
    """전부터 있던 원본 캐시는 사용자 자산이므로 지우면 안 된다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.cache = Path(yc.DEFAULT_SOURCE_DIR) / f"{VID}.mp4"

    def test_existing_cache_survives(self):
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        created = not self.cache.exists()
        if created:
            self.cache.write_bytes(b"fake")
        try:
            yc.extract_segments(
                f"https://youtu.be/{VID}", [yc.Segment(10, 40)],
                out_dir=self.dir / "clips", dry_run=True,
            )
            self.assertTrue(self.cache.exists(), "받아둔 원본이 삭제되면 안 된다")
        finally:
            if created:
                self.cache.unlink(missing_ok=True)


class TestFfmpegLocation(unittest.TestCase):
    """yt-dlp 에는 폴더가 아니라 실행 파일 전체 경로를 넘겨야 한다.

    폴더를 넘기면 yt-dlp 가 그 안에서 'ffmpeg.exe' 를 찾는데, pip 로 들어오는
    imageio-ffmpeg 의 파일 이름은 'ffmpeg-win-x86_64-v7.1.exe' 라서
    "ffmpeg is not installed" 로 실패한다. 실제로 사용자 PC 에서 났던 문제다.
    """

    ODD_NAME = "/fake/dir/ffmpeg-win-x86_64-v7.1.exe"

    def setUp(self):
        self.orig_which = yc.shutil.which
        self.orig_find = yc.find_ffmpeg
        yc.shutil.which = lambda name: None if name == "ffmpeg" else self.orig_which(name)
        yc.find_ffmpeg = lambda: self.ODD_NAME
        self.addCleanup(setattr, yc.shutil, "which", self.orig_which)
        self.addCleanup(setattr, yc, "find_ffmpeg", self.orig_find)

    def test_passes_full_binary_path(self):
        args = yc._ffmpeg_location_args()
        self.assertEqual(args, ["--ffmpeg-location", self.ODD_NAME])

    def test_never_passes_the_directory(self):
        self.assertNotIn("/fake/dir", yc._ffmpeg_location_args()[1:2] and [])
        self.assertNotEqual(yc._ffmpeg_location_args()[1], "/fake/dir")

    def test_section_command_carries_it(self):
        ref = yc.parse_youtube_url(f"https://youtu.be/{VID}")
        cmd = yc.build_section_command(
            ref, yc.Segment(10, 25),
            out_template="out/%(title)s.%(ext)s", print_file=Path("/tmp/p.txt"),
        )
        self.assertIn("--ffmpeg-location", cmd)
        self.assertEqual(cmd[cmd.index("--ffmpeg-location") + 1], self.ODD_NAME)

    def test_skipped_when_ffmpeg_on_path(self):
        yc.shutil.which = lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None
        self.assertEqual(yc._ffmpeg_location_args(), [])


class TestJsRuntime(unittest.TestCase):
    def setUp(self):
        self.orig = yc.shutil.which
        self.addCleanup(setattr, yc.shutil, "which", self.orig)

    def test_deno_needs_no_flag(self):
        yc.shutil.which = lambda n: "/usr/bin/deno" if n == "deno" else None
        self.assertEqual(yc._js_runtime_args(), [])      # yt-dlp 기본값

    def test_node_is_passed(self):
        yc.shutil.which = lambda n: "/usr/bin/node" if n == "node" else None
        self.assertEqual(yc._js_runtime_args(), ["--js-runtimes", "node"])

    def test_none_available(self):
        yc.shutil.which = lambda n: None
        self.assertEqual(yc._js_runtime_args(), [])


if __name__ == "__main__":
    unittest.main()
