"""auto-blog CLI 진입점.

사용 예:
    python -m src.run_pipeline keywords --niche "전세자금대출" --count 30
    python -m src.run_pipeline brief --keyword "신혼부부 전세자금대출 소득기준" --site bodybalance
    python -m src.run_pipeline publish --file drafts/xxx.md --site bodybalance
    python -m src.run_pipeline sites
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
