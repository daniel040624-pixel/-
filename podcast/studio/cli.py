"""명령줄 진입점: python -m studio <명령> ..."""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

from . import edit, footage, plan, publish, render, research, schedule, script, voice
from .common import ROOT, StudioError, load_config, load_episode, log, read_json

STAGES = ["script", "voice", "edit", "render", "upload"]

TEMPLATE = '''topic = "{topic}"

# 이 주제에 대한 '나의 생각'. 대본은 이 관점을 중심으로 쓰입니다.
my_view = """
"""

# 꼭 다뤘으면 하는 논점·사례·경험 (선택)
points = []

# 듣는 사람이 가져갔으면 하는 것
takeaway_goal = ""

minutes = 12
tone = "진지하지만 따뜻하게, 가끔 유머"

# 직접 녹음한 음성을 쓰려면: recording = "raw.m4a"
'''


def cmd_new(args) -> None:
    slug = args.slug or re.sub(r"[^\w가-힣]+", "-", args.topic).strip("-")[:40]
    d = ROOT / "episodes" / f"{date.today():%Y-%m-%d}-{slug}"
    d.mkdir(parents=True, exist_ok=False)
    (d / "episode.toml").write_text(TEMPLATE.format(topic=args.topic.replace('"', "'")), encoding="utf-8")
    print(f"새 에피소드: {d}\n→ episode.toml 에 '나의 생각'을 적은 뒤:  python -m studio run {d.relative_to(Path.cwd()) if d.is_relative_to(Path.cwd()) else d}")


def _script(ep):
    p = ep.path("script.json")
    return read_json(p) if p.exists() else None


def run_pipeline(ep_dir: str, start: str, until: str, force: set[str], config: Path | None) -> None:
    ep = load_episode(ep_dir, config)
    recording = bool(ep.ep.get("recording"))
    stages = STAGES[STAGES.index(start):STAGES.index(until) + 1]
    log("studio", f"{ep.dir.name}: {' → '.join(stages)}")

    data = _script(ep)
    if "script" in stages and not recording:
        data = script.generate(ep, force="script" in force)
    if "voice" in stages and not recording:
        if data is None:
            raise StudioError("script.json 이 없습니다. script 단계를 먼저 실행하세요.")
        voice.synthesize(ep, data)
    if "edit" in stages:
        tl = edit.master(ep, data)
    elif ep.path("timeline.json").exists():
        tl = read_json(ep.path("timeline.json"))
    else:
        tl = None
    if "render" in stages:
        if tl is None:
            raise StudioError("timeline.json 이 없습니다. edit 단계를 먼저 실행하세요.")
        rendered = render.render(ep, data, tl)
    elif ep.path("render.json").exists():
        rendered = read_json(ep.path("render.json"))
    else:
        rendered = None
    if tl is not None:
        meta = publish.metadata(ep, data, tl)
    if "upload" in stages:
        if rendered is None:
            raise StudioError("영상이 없습니다. render 단계를 먼저 실행하세요.")
        publish.upload(ep, meta, rendered, force="upload" in force)
        publish.rss(ep.cfg)
    log("studio", f"끝. 결과물: {ep.build}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m studio", description="팟캐스트 영상 생성·편집·업로드 자동화")
    ap.add_argument("--config", type=Path, help="config.toml 경로 (기본: podcast/config.toml)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new", help="새 에피소드 폴더 만들기")
    p.add_argument("topic")
    p.add_argument("--slug")

    p = sub.add_parser("run", help="전체 파이프라인 한 번에 실행 (대본→음성→편집→영상→업로드)")
    p.add_argument("episode")
    p.add_argument("--from", dest="start", choices=STAGES, default="script")
    p.add_argument("--until", choices=STAGES, default="upload", help="이 단계까지만 실행 (예: render 로 업로드 전 확인)")
    p.add_argument("--force", nargs="*", choices=STAGES, default=[], help="캐시를 무시하고 다시 실행할 단계")

    for stage in STAGES:
        p = sub.add_parser(stage, help=f"{stage} 단계만 실행")
        p.add_argument("episode")
        p.add_argument("--force", action="store_true")

    sub.add_parser("rss", help="팟캐스트 RSS 피드 다시 만들기")

    # ── 매일 촬영 → 자동 편집 → 업로드 ──
    p = sub.add_parser("today", help="오늘(또는 지정일) 촬영 카드 보기")
    p.add_argument("--date", type=date.fromisoformat)
    p.add_argument("--days", type=int, default=1)

    p = sub.add_parser("plan", help="다음 촬영 계획 자동 생성 (브랜드 가이드·단계·댓글 반영, Claude)")
    p.add_argument("--days", type=int, default=7)

    p = sub.add_parser("cut", help="찍은 영상을 템플릿대로 자동 편집")
    p.add_argument("video", type=Path)
    p.add_argument("--template", help="solo_short | dialogue_short | dialogue_long | solo_long (생략 시 자동)")
    p.add_argument("--date", help="계획표에서 연결할 날짜 (기본: 파일명의 날짜 또는 오늘)")
    p.add_argument("--now", action="store_true", help="편집 후 바로 즉시 업로드")
    p.add_argument("--schedule", action="store_true", help="편집 후 다음 빈 시간으로 예약 업로드")

    p = sub.add_parser("publish", help="편집된 영상 업로드 (기본: 단계별 시간표에 맞춰 예약)")
    p.add_argument("item", type=Path, help="daily/<날짜-이름> 폴더")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--now", action="store_true", help="즉시 공개 업로드")
    g.add_argument("--at", help="지정 시각 예약 (예: 2026-10-20T19:00)")

    p = sub.add_parser("watch", help="inbox/ 감시: 영상을 넣기만 하면 편집→업로드 자동")
    p.add_argument("--mode", choices=["auto", "now", "none"], default="auto",
                   help="auto=시간표대로 예약, now=즉시 공개, none=편집만")
    p.add_argument("--interval", type=int, default=30)
    p.add_argument("--once", action="store_true", help="한 번만 처리하고 종료 (cron 용)")

    sub.add_parser("status", help="편집·예약·업로드 현황")
    p = sub.add_parser("stage", help="내 채널 구독자 수로 현재 성장 단계 확인")
    p.add_argument("--subs", type=int, help="API 대신 직접 입력")

    p = sub.add_parser("benchmark", help="비슷한 구독자 1만+ 채널 수집·분석 (YouTube API)")
    p.add_argument("keywords", nargs="+")
    p.add_argument("--min-subs", type=int, default=10000)
    p.add_argument("--ai", action="store_true", help="결과를 Claude 가 단계별 전략으로 해석")

    p = sub.add_parser("listen", help="내 영상 성과·댓글 수집 (다음 기획에 반영)")
    p.add_argument("--videos", type=int, default=20)

    args = ap.parse_args(argv)

    def cfg():
        return load_config(args.config)

    try:
        if args.cmd == "new":
            cmd_new(args)
        elif args.cmd == "rss":
            publish.rss(load_config(args.config))
        elif args.cmd == "today":
            print(plan.show(cfg(), args.date, args.days))
        elif args.cmd == "plan":
            new_rows = plan.generate(cfg(), args.days)
            print("\n".join(plan.card(r) for r in new_rows))
        elif args.cmd == "cut":
            c = cfg()
            entry = plan.entry_for(c, args.video if not args.date else Path(f"{args.date}_{args.video.name}"))
            d = footage.process(c, args.video, args.template, entry)
            if args.now or args.schedule:
                schedule.publish(c, d, "now" if args.now else "auto")
        elif args.cmd == "publish":
            mode = "now" if args.now else "at" if args.at else "auto"
            schedule.publish(cfg(), args.item, mode, args.at)
        elif args.cmd == "watch":
            schedule.watch(cfg(), args.mode, args.interval, args.once)
        elif args.cmd == "status":
            print(schedule.status(cfg()))
        elif args.cmd == "stage":
            c = cfg()
            subs = args.subs if args.subs is not None else schedule.subscribers(c)
            if args.subs is not None:
                from .common import write_json
                write_json(ROOT / "plan" / "channel.json", {"subscribers": subs, "checked": date.today().isoformat()})
            st = schedule.current_stage(c, subs)
            print(f"구독자 {subs:,}명 → {st['name']}\n쇼츠 하루 {st['shorts_per_day']}개 ({', '.join(st['slots'])}), "
                  f"롱폼 주 {st['long_per_week']}개 ({st['long_slot']})\n할 일: strategy/growth-playbook.md 의 '{st['name'].split()[0]}' 절")
        elif args.cmd == "benchmark":
            print(research.benchmark(cfg(), args.keywords, args.min_subs, args.ai))
        elif args.cmd == "listen":
            research.listen(cfg(), args.videos)
        elif args.cmd == "run":
            run_pipeline(args.episode, args.start, args.until, set(args.force), args.config)
        else:
            run_pipeline(args.episode, args.cmd, args.cmd, {args.cmd} if args.force else set(), args.config)
    except StudioError as e:
        print(f"\n오류: {e}", file=sys.stderr)
        return 1
    return 0
