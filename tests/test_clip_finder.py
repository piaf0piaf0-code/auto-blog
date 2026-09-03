"""clip_finder 의 순수 로직 테스트 (네트워크·API 호출 없음)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import clip_finder as cf  # noqa: E402
from src.youtube_clipper import ClipError  # noqa: E402

# 유튜브 자동 생성 자막의 실제 모양 (같은 문장을 한 단어씩 늘려가며 반복한다)
AUTO_VTT = """WEBVTT
Kind: captions
Language: ko

00:00:00.030 --> 00:00:03.270 align:start position:0%
 
안녕하세요<00:00:00.719><c> 오늘은</c><00:00:01.199><c> 전세자금대출</c>

00:00:03.270 --> 00:00:03.280 align:start position:0%
안녕하세요 오늘은 전세자금대출

00:00:03.280 --> 00:00:06.500 align:start position:0%
안녕하세요 오늘은 전세자금대출
조건에<00:00:04.000><c> 대해</c><00:00:04.500><c> 알아보겠습니다</c>

00:00:20.000 --> 00:00:24.000 align:start position:0%
결론부터 말하면 &quot;소득기준&quot;이 핵심입니다
"""

PLAIN_VTT = """WEBVTT

1
00:00:01.000 --> 00:00:04.000
첫 문장입니다

2
00:00:04.000 --> 00:00:08.000
둘째 문장입니다
"""


class TestParseVtt(unittest.TestCase):
    def test_removes_rolling_duplicates(self):
        cues = cf.parse_vtt(AUTO_VTT)
        texts = [c.text for c in cues]
        self.assertEqual(texts, [
            "안녕하세요 오늘은 전세자금대출",
            "조건에 대해 알아보겠습니다",
            '결론부터 말하면 "소득기준"이 핵심입니다',   # HTML 엔티티도 풀린다
        ])

    def test_strips_inline_tags(self):
        for cue in cf.parse_vtt(AUTO_VTT):
            self.assertNotIn("<", cue.text)

    def test_timestamps(self):
        cues = cf.parse_vtt(AUTO_VTT)
        self.assertAlmostEqual(cues[0].start, 0.03)
        self.assertAlmostEqual(cues[-1].start, 20.0)
        self.assertAlmostEqual(cues[-1].end, 24.0)

    def test_plain_subtitles(self):
        cues = cf.parse_vtt(PLAIN_VTT)
        self.assertEqual([c.text for c in cues], ["첫 문장입니다", "둘째 문장입니다"])

    def test_empty(self):
        self.assertEqual(cf.parse_vtt("WEBVTT\n\n"), [])


class TestTranscript(unittest.TestCase):
    def test_buckets_by_time(self):
        cues = cf.parse_vtt(AUTO_VTT)
        text = cf.build_transcript(cues, bucket=15)
        lines = text.splitlines()
        self.assertEqual(len(lines), 2)          # 0초대 묶음 + 20초대 묶음
        self.assertTrue(lines[0].startswith("[0:00]"))
        self.assertTrue(lines[1].startswith("[0:20]"))
        self.assertIn("알아보겠습니다", lines[0])

    def test_empty_input(self):
        self.assertEqual(cf.build_transcript([]), "")


def _plan(*specs):
    return cf.ClipPlan(
        video_summary="요약",
        suggestions=[
            cf.ClipSuggestion(
                start_seconds=start, end_seconds=end, title="제목",
                hook_text="문구", reason="이유", score=score,
            )
            for start, end, score in specs
        ],
    )


class TestClamping(unittest.TestCase):
    def test_extends_too_short(self):
        out = cf._clamp_suggestions(_plan((10, 13, 8)))
        self.assertGreaterEqual(out[0].end_seconds - out[0].start_seconds,
                                cf.MIN_CLIP_SECONDS)

    def test_caps_too_long(self):
        out = cf._clamp_suggestions(_plan((10, 400, 8)))
        self.assertLessEqual(out[0].end_seconds - out[0].start_seconds,
                             cf.MAX_CLIP_SECONDS)

    def test_drops_beyond_video_end(self):
        out = cf._clamp_suggestions(_plan((10, 40, 9), (500, 540, 8)), duration=100)
        self.assertEqual(len(out), 1)

    def test_negative_start(self):
        out = cf._clamp_suggestions(_plan((-5, 30, 7)))
        self.assertEqual(out[0].start_seconds, 0.0)

    def test_sorts_by_score(self):
        out = cf._clamp_suggestions(_plan((10, 40, 5), (60, 90, 9), (120, 150, 7)))
        self.assertEqual([s.score for s in out], [9, 7, 5])

    def test_keeps_valid_near_end(self):
        out = cf._clamp_suggestions(_plan((60, 200, 8)), duration=100)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].end_seconds, 100)      # 영상 끝까지만

    def test_drops_when_start_too_close_to_end(self):
        self.assertEqual(cf._clamp_suggestions(_plan((95, 130, 8)), duration=100), [])

    def test_fixes_reversed_times(self):
        out = cf._clamp_suggestions(_plan((60, 30, 8)))
        self.assertGreater(out[0].end_seconds, out[0].start_seconds)


class TestSegmentAndFormat(unittest.TestCase):
    def test_to_segment(self):
        plan = _plan((90, 130, 9))
        seg = cf.to_segment(plan.suggestions[0])
        self.assertEqual(seg.label, "1:30~2:10")
        self.assertAlmostEqual(seg.duration, 40)

    def test_format_plan_has_ready_command(self):
        text = cf.format_plan(_plan((90, 130, 9)), language="ko")
        self.assertIn("1:30~2:10", text)
        self.assertIn("--vertical", text)
        self.assertIn("자막 언어: ko", text)


if __name__ == "__main__":
    unittest.main()
