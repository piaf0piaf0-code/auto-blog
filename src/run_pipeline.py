"""auto-blog CLI 진입점.

사용 예:
    python -m src.run_pipeline keywords --niche "전세자금대출" --count 30
    python -m src.run_pipeline brief --keyword "신혼부부 전세자금대출 소득기준" --site bodybalance
    python -m src.run_pipeline publish --file drafts/xxx.md --site bodybalance
    python -m src.run_pipeline sites
    python -m src.run_pipeline clip --url "https://youtu.be/XXXXXXXXXXX" --start 1:30 --end 2:10
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config


def cmd_keywords(args: argparse.Namespace) -> int:
    from . import keyword_planner

    print(f"[키워드 발굴] 분야='{args.niche}', {args.count}개 생성 중...\n")
    plan = keyword_planner.find_keywords(args.niche, args.count)
    print(keyword_planner.format_plan(plan))

    if args.out:
        Path(args.out).write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        print(f"\n저장됨: {args.out}")
    return 0


def cmd_brief(args: argparse.Namespace) -> int:
    from . import content_brief

    niche = None
    if args.site:
        niche = config.get_site(args.site).niche

    print(f"[초안 생성] 키워드='{args.keyword}' 생성 중 (수십 초 소요)...\n")
    brief = content_brief.generate_brief(args.keyword, site_niche=niche)
    path = content_brief.save_draft(brief)

    print(f"제목   : {brief.title_tag}")
    print(f"메타   : {brief.meta_description}")
    print(f"YMYL   : {'예' if brief.is_ymyl else '아니오'}")
    print(f"확인필요: {len(brief.fact_check_items)}개 항목")
    print(f"\n초안 저장됨 → {path}")
    print("다음: 이 파일을 검수·사실확인한 뒤 publish 명령으로 발행하세요.")
    print("  python -m src.run_pipeline publish --file "
          f"{path} --site <사이트키>")
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    from . import wordpress_publisher

    path = Path(args.file)
    if not path.exists():
        print(f"파일이 없습니다: {path}", file=sys.stderr)
        return 1

    site = config.get_site(args.site)
    status = "publish" if args.live else "draft"

    if args.live:
        print("⚠️  --live: 공개(publish) 상태로 발행합니다. 검수를 마쳤는지 확인하세요.")
    else:
        print("draft(비공개)로 업로드합니다. 워드프레스에서 확인 후 공개하세요.")

    print(f"[발행] {path.name} → {site.name} ({status})...")
    result = wordpress_publisher.publish_file(path, site, status=status)
    print(f"\n완료. 글 ID={result['id']}, 상태={result['status']}")
    print(f"편집: {result['edit_link']}")
    if result.get("link"):
        print(f"링크: {result['link']}")
    return 0


def cmd_clip(args: argparse.Namespace) -> int:
    """유튜브 링크에서 원하는 시간대를 잘라낸다 (숏츠 소재 확보)."""
    from . import youtube_clipper as yc

    ref = yc.parse_youtube_url(args.url)

    if args.info:
        info = yc.probe(
            ref.url,
            cookies=args.cookies,
            cookies_from_browser=args.cookies_from_browser,
        )
        dur = info.get("duration")
        print(f"제목     : {info.get('title')}")
        print(f"채널     : {info.get('uploader')}")
        print(f"길이     : {yc.format_human(dur) if dur else '알 수 없음'}")
        print(f"라이선스 : {info.get('license') or '표준 유튜브 라이선스(재사용 시 저작권 확인 필요)'}")
        subs = info.get("subtitles") or info.get("auto_subtitles")
        print(f"자막     : {', '.join(subs[:8]) if subs else '없음'}")
        chapters = info.get("chapters") or []
        if chapters:
            print("\n챕터 (구간 고를 때 참고):")
            for c in chapters:
                s, e = c.get("start_time", 0), c.get("end_time", 0)
                print(f"  {yc.format_human(s):>8} ~ {yc.format_human(e):<8} {c.get('title', '')}")
        print("\n구간 추출:")
        print(f"  python -m src.run_pipeline clip --url {ref.url} --start 1:30 --end 2:10")
        return 0

    # ── 구간 목록 만들기 ──
    segments: list[yc.Segment] = []
    if args.range:
        for r in args.range:
            start, end = yc.parse_range(r)
            segments.append(yc.Segment(start, end))
    else:
        start = args.start
        if start is None:
            if ref.start_hint is None:
                print(
                    "시작 시간이 없습니다. --start 를 주거나, t= 가 붙은 링크를 쓰세요.\n"
                    "  예) --start 1:30 --end 2:10   또는   --range 1:30-2:10",
                    file=sys.stderr,
                )
                return 1
            start = ref.start_hint
            print(f"링크의 t= 값을 시작 시간으로 사용합니다: {yc.format_human(start)}")
        segments.append(yc.Segment.of(start, end=args.end, duration=args.duration))

    print(f"[구간 추출] {ref.url}")
    print(f"  구간 {len(segments)}개 · {'음성만(mp3)' if args.audio_only else f'최대 {args.quality}p'}"
          f" · {'빠른 컷(키프레임)' if args.fast else '정밀 컷'}")

    paths = yc.extract_segments(
        ref.url,
        segments,
        out_dir=Path(args.out),
        name=args.name,
        quality=args.quality,
        audio_only=args.audio_only,
        precise=not args.fast,
        cookies=args.cookies,
        cookies_from_browser=args.cookies_from_browser,
        keep_source=args.keep_source,
        dry_run=args.dry_run,
    )

    print(f"\n완료: {len(paths)}개 파일 → {args.out}/")
    return 0


def cmd_sites(_: argparse.Namespace) -> int:
    print("등록된 워드프레스 사이트:\n")
    for key, site in config.SITES.items():
        mark = "✅ 설정됨" if site.configured else "⬜ .env 미설정"
        print(f"  {key:<12} {mark}  {site.name}  [{site.niche}]")
    print("\n티스토리(finwiz 등)·네이버는 REST 발행 불가 → 초안까지만 자동화, 발행은 수동.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="auto-blog", description="블로그 수익화 자동화 도구")
    sub = p.add_subparsers(dest="command", required=True)

    pk = sub.add_parser("keywords", help="키워드 기회 발굴")
    pk.add_argument("--niche", required=True, help="분야/주제 (예: 전세자금대출)")
    pk.add_argument("--count", type=int, default=20, help="생성할 키워드 수")
    pk.add_argument("--out", help="결과 JSON 저장 경로(선택)")
    pk.set_defaults(func=cmd_keywords)

    pb = sub.add_parser("brief", help="콘텐츠 초안 생성")
    pb.add_argument("--keyword", required=True, help="대상 키워드")
    pb.add_argument("--site", help="사이트 키(분야 컨텍스트용, 선택)")
    pb.set_defaults(func=cmd_brief)

    pp = sub.add_parser("publish", help="초안을 워드프레스에 발행")
    pp.add_argument("--file", required=True, help="발행할 마크다운 파일")
    pp.add_argument("--site", required=True, help="대상 사이트 키")
    pp.add_argument(
        "--live",
        action="store_true",
        help="공개(publish) 상태로 발행. 기본은 draft(비공개).",
    )
    pp.set_defaults(func=cmd_publish)

    pc = sub.add_parser(
        "clip",
        help="유튜브 링크에서 원하는 시간대만 추출 (숏츠 소재)",
        description="유튜브 링크와 시작/끝 시간을 주면 그 구간만 잘라 저장한다.",
    )
    pc.add_argument("--url", required=True, help="유튜브 링크 또는 영상 ID")
    pc.add_argument("--start", help="시작 시간 (예: 90 / 1:30 / 00:01:30 / 1m30s)")
    pc.add_argument("--end", help="끝 시간 (--duration 과 택일)")
    pc.add_argument("--duration", help="시작부터의 길이 (예: 45 / 0:45)")
    pc.add_argument(
        "--range",
        action="append",
        metavar="시작-끝",
        help="구간을 한 번에 지정 (예: 1:30-2:10). 여러 번 쓰면 여러 클립을 뽑는다.",
    )
    pc.add_argument("--out", default=str(Path("outputs/clips")), help="저장 폴더")
    pc.add_argument("--name", help="저장할 파일 이름(확장자 제외)")
    pc.add_argument("--quality", type=int, default=1080, help="최대 세로 해상도 (기본 1080)")
    pc.add_argument("--audio-only", action="store_true", help="음성만 mp3 로 추출")
    pc.add_argument(
        "--fast",
        action="store_true",
        help="재인코딩 없이 빠르게 컷. 키프레임 단위라 시작점이 최대 몇 초 밀릴 수 있다.",
    )
    pc.add_argument(
        "--keep-source",
        action="store_true",
        help="원본 전체를 outputs/sources/ 에 남긴다 (다시 자를 때 재사용).",
    )
    pc.add_argument("--cookies", help="쿠키 파일 경로 (연령제한·로그인 필요 영상)")
    pc.add_argument("--cookies-from-browser", help="브라우저에서 쿠키 사용 (예: chrome)")
    pc.add_argument("--info", action="store_true", help="다운로드 없이 제목·길이·챕터만 확인")
    pc.add_argument("--dry-run", action="store_true", help="실행할 명령만 출력")
    pc.set_defaults(func=cmd_clip)

    ps = sub.add_parser("sites", help="등록된 사이트 목록")
    ps.set_defaults(func=cmd_sites)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as e:  # noqa: BLE001 - CLI 최상위에서 사용자용 메시지로 변환
        print(f"\n오류: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
