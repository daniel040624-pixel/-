"""명령줄 진입점: python -m studio <명령> ..."""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

from . import edit, publish, render, script, voice
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

    args = ap.parse_args(argv)
    try:
        if args.cmd == "new":
            cmd_new(args)
        elif args.cmd == "rss":
            publish.rss(load_config(args.config))
        elif args.cmd == "run":
            run_pipeline(args.episode, args.start, args.until, set(args.force), args.config)
        else:
            run_pipeline(args.episode, args.cmd, args.cmd, {args.cmd} if args.force else set(), args.config)
    except StudioError as e:
        print(f"\n오류: {e}", file=sys.stderr)
        return 1
    return 0
