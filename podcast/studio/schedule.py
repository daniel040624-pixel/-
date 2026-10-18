"""성장 단계 판단 · 업로드 시간표 · 즉시/예약 업로드 · 폴더 감시 자동화."""
from __future__ import annotations

import json
import shutil
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import footage, plan
from .common import ROOT, StudioError, log, read_json, write_json

WEEKDAYS = "월화수목금토일"


def tz(cfg: dict) -> ZoneInfo:
    return ZoneInfo(cfg["brand"].get("timezone", "Asia/Seoul"))


# ─── 성장 단계 ───

def subscribers(cfg: dict) -> int:
    from .publish import youtube_client
    resp = youtube_client(cfg).channels().list(part="statistics,snippet", mine=True).execute()
    if not resp.get("items"):
        raise StudioError("로그인한 계정에 유튜브 채널이 없습니다.")
    n = int(resp["items"][0]["statistics"].get("subscriberCount", 0))
    write_json(ROOT / "plan" / "channel.json", {"subscribers": n, "checked": datetime.now().isoformat(),
                                                "title": resp["items"][0]["snippet"]["title"]})
    return n


def current_stage(cfg: dict, subs: int | None = None) -> dict:
    if subs is None:
        cached = ROOT / "plan" / "channel.json"
        if cached.exists():
            subs = read_json(cached)["subscribers"]
        else:
            subs = 0
    stages = sorted(cfg["stages"], key=lambda s: s["min"])
    return {**[s for s in stages if s["min"] <= subs][-1], "subscribers": subs}


# ─── 시간표 ───

def _hm(text: str) -> tuple[int, int]:
    h, m = text.strip().split(":")
    return int(h), int(m)


def taken_slots() -> set[str]:
    taken = set()
    for p in (ROOT / "daily").glob("*/item.json"):
        for up in read_json(p).get("uploads", {}).values():
            if up.get("publish_at"):
                taken.add(up["publish_at"][:16])
    return taken


def next_slot(cfg: dict, kind: str, taken: set[str], after: datetime | None = None) -> datetime:
    """단계별 규칙에 맞는 다음 빈 업로드 시각."""
    stage = current_stage(cfg)
    zone = tz(cfg)
    now = datetime.now(zone) + timedelta(minutes=20)  # 처리 여유
    start = max(now, after) if after else now
    if kind == "long":
        days_part, hm = stage["long_slot"].rsplit(" ", 1)
        days = {WEEKDAYS.index(d.strip()) for d in days_part.split(",")}
        times = [_hm(hm)]
    else:  # short: 직접 찍은 쇼츠, clip: 롱폼에서 잘라낸 쇼츠
        slots = stage.get("clip_slots", stage["slots"]) if kind == "clip" else stage["slots"]
        days, times = set(range(7)), [_hm(t) for t in slots]
    for offset in range(0, 60):
        day = (start + timedelta(days=offset)).date()
        if day.weekday() not in days:
            continue
        for h, m in times:
            t = datetime(day.year, day.month, day.day, h, m, tzinfo=zone)
            if t >= start and t.isoformat()[:16] not in taken:
                return t
    raise StudioError("60일 안에 빈 업로드 시간이 없습니다. config.toml [[stages]] slots 를 늘리세요.")


def parse_at(cfg: dict, text: str) -> datetime:
    t = datetime.fromisoformat(text.replace("/", "-"))
    return t if t.tzinfo else t.replace(tzinfo=tz(cfg))


# ─── 업로드 ───

def _description(cfg: dict, item: dict, f: dict, main_id: str | None) -> str:
    br = cfg["brand"]
    tags = item.get("tags", [])[:4] + cfg["channel"].get("hashtags", [])[:2]
    parts = []
    if f["kind"] == "short" and main_id and f["path"] != item["files"][0]["path"]:
        parts.append(f"{br.get('cta_clip', '전체 영상')} https://youtu.be/{main_id}\n")
    parts.append(item.get("description") or item["title"])
    parts.append(f"\n🎙 {cfg['channel']['name']} — {br.get('promise', '')}")
    hashtags = " ".join("#" + t.replace(" ", "").lstrip("#") for t in dict.fromkeys(tags))
    if f["kind"] == "short":
        hashtags = "#shorts " + hashtags
    parts.append("\n" + hashtags)
    return "\n".join(parts).replace("<", "‹").replace(">", "›")[:4900]


def publish(cfg: dict, d: Path, mode: str = "auto", at: str | None = None) -> dict:
    """mode: now(즉시 공개) | auto(단계별 다음 빈 시간에 예약) | at(지정 시각 예약)."""
    from .publish import upload_captions, upload_video, youtube_client

    d = d.resolve()
    item_path = d / "item.json"
    if not item_path.exists():
        raise StudioError(f"{d} 에 item.json 이 없습니다. 먼저 'python -m studio cut <영상>' 을 실행하세요.")
    item = read_json(item_path)
    yt = youtube_client(cfg)
    from googleapiclient.http import MediaFileUpload
    taken = taken_slots()
    main_id = item["uploads"].get(item["files"][0]["path"], {}).get("video_id")
    when = parse_at(cfg, at) if mode == "at" and at else None
    last = None
    if mode == "auto" and item.get("plan_date"):  # 몰아 찍은 영상은 계획표 날짜에 맞춰 예약
        last = datetime.fromisoformat(item["plan_date"]).replace(tzinfo=tz(cfg))
    for f in item["files"]:
        if item["uploads"].get(f["path"], {}).get("video_id"):
            log("publish", f"{f['path']} 이미 업로드됨 — 건너뜀")
            continue
        if mode == "now":
            publish_at = None
        else:
            slot_kind = "long" if f["kind"] == "long" else "clip" if item["kind"] == "long" else "short"
            if mode == "at" and f is item["files"][0]:
                publish_at = when
            else:  # 클립은 원본 롱폼 공개 이후의 빈 시간에
                publish_at = next_slot(cfg, slot_kind, taken, after=last or when)
        if publish_at:
            taken.add(publish_at.isoformat()[:16])
            last = publish_at

        title = f["title"][:100]
        desc = _description(cfg, item, f, main_id)
        cfg_up = {**cfg, "upload": {**cfg["upload"], "privacy": cfg["upload"].get("daily_privacy", "public")}}
        vid = upload_video(yt, cfg_up, d / f["path"], title, desc, item.get("tags", []), synthetic=False,
                           publish_at=publish_at)
        item["uploads"][f["path"]] = {"video_id": vid,
                                      "publish_at": publish_at.isoformat() if publish_at else None,
                                      "uploaded": datetime.now(tz(cfg)).isoformat()}
        write_json(item_path, item)
        if f is item["files"][0]:
            main_id = vid
        if cfg["upload"].get("upload_captions", True) and f.get("srt"):
            try:
                upload_captions(yt, cfg, vid, d / f["srt"])
            except Exception as e:
                log("publish", f"자막 업로드 실패: {e}")
        if f.get("thumbnail"):
            try:
                yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(d / f["thumbnail"]))).execute()
            except Exception as e:
                log("publish", f"썸네일 설정 실패(채널 전화번호 인증 필요): {e}")
        when_txt = f"{publish_at:%m/%d(%a) %H:%M} 공개 예약" if publish_at else "즉시 공개"
        log("publish", f"{f['path']} → https://youtu.be/{vid} ({when_txt})")
        # 인스타 릴스·틱톡에 그대로 올릴 수 있게 캡션 파일도 남긴다
        if f["kind"] == "short":
            (d / f"{Path(f['path']).stem}_caption.txt").write_text(f"{title}\n\n{desc}\n", encoding="utf-8")
    return item


# ─── 폴더 감시 ───

def watch(cfg: dict, mode: str = "auto", interval: int = 30, once: bool = False) -> None:
    """inbox/ 에 영상이 들어오면: 오늘 계획과 연결 → 자동 편집 → 업로드(예약/즉시)."""
    inbox = ROOT / cfg["footage"].get("inbox", "inbox")
    failed = inbox / "failed"
    inbox.mkdir(exist_ok=True)
    sizes: dict[Path, int] = {}
    log("watch", f"{inbox} 감시 중 (업로드: {mode}) — 영상을 넣으면 자동 처리됩니다. 종료: Ctrl+C")
    stage_checked = 0.0
    while True:
        if mode != "none" and time.time() - stage_checked > 6 * 3600:  # 6시간마다 구독자 수 → 단계별 시간표 자동 전환
            stage_checked = time.time()
            try:
                before = current_stage(cfg)["name"]
                after = current_stage(cfg, subscribers(cfg))["name"]
                log("watch", f"현재 단계: {after}" + (f" (🎉 {before}에서 올라감 — 플레이북 다음 절을 확인하세요)"
                                                    if after != before else ""))
            except Exception as e:
                log("watch", f"구독자 수 확인 실패(마지막 값으로 계속): {e}")
        for src in sorted(inbox.iterdir()):
            if src.suffix.lower() not in footage.VIDEO_EXT:
                continue
            size = src.stat().st_size
            if sizes.get(src) != size and not once:  # 복사 중인 파일은 크기가 멈출 때까지 대기
                sizes[src] = size
                continue
            sizes.pop(src, None)
            try:
                entry = plan.entry_for(cfg, src)
                d = footage.process(cfg, src, entry=entry, move=True)
                if src.with_suffix(".srt").exists():
                    shutil.move(str(src.with_suffix(".srt")), d / "source.srt")
                if mode != "none":
                    publish(cfg, d, mode)
            except Exception as e:
                failed.mkdir(exist_ok=True)
                if src.exists():
                    shutil.move(str(src), failed / src.name)
                log("watch", f"✗ {src.name} 처리 실패 → inbox/failed/ 로 이동: {e}")
                (failed / f"{src.name}.log").write_text(traceback.format_exc(), encoding="utf-8")
        if once:
            return
        time.sleep(interval)


def status(cfg: dict) -> str:
    """업로드 현황 요약."""
    rows = []
    for p in sorted((ROOT / "daily").glob("*/item.json")):
        item = read_json(p)
        for f in item["files"]:
            up = item["uploads"].get(f["path"])
            state = ("예약 " + up["publish_at"][5:16].replace("T", " ") if up and up.get("publish_at")
                     else "공개됨" if up else "대기")
            rows.append(f"{p.parent.name:<32} {f['path']:<11} {state:<18} {f['title']}")
    return "\n".join(rows) or "아직 편집된 영상이 없습니다."


def describe_stage(cfg: dict, subs: int | None) -> str:
    s = current_stage(cfg, subs)
    return json.dumps(s, ensure_ascii=False, indent=2)
