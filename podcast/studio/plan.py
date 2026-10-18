"""매일 촬영 계획표 (plan/calendar.csv): 오늘 찍을 카드 보기 · 다음 계획 자동 생성."""
from __future__ import annotations

import csv
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import ai
from .common import ROOT, StudioError, log

CAL = ROOT / "plan" / "calendar.csv"
FIELDS = ["date", "weekday", "track", "format", "kind", "pillar", "topic", "hook", "points",
          "closing_question", "title", "hashtags", "status"]
WEEKDAYS = "월화수목금토일"
TRACK = {"A": "A · 채널의 메시지", "B": "B · 시청자가 원하는 메시지"}
TEMPLATE_TIP = {
    "solo_short": "세로 · 혼자 · 30~60초 · 첫 마디가 훅",
    "dialogue_short": "가로 · 둘이서 · 1분 이내 한 장면",
    "dialogue_long": "가로 · 둘이서 · 15~30분 대화 (쇼츠 3개 자동 추출)",
    "solo_long": "가로 · 혼자 · 5~12분 에세이 (쇼츠 2개 자동 추출)",
}


def load() -> list[dict]:
    if not CAL.exists():
        return []
    with open(CAL, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def save(rows: list[dict]) -> None:
    CAL.parent.mkdir(exist_ok=True)
    with open(CAL, "w", encoding="utf-8-sig", newline="") as f:  # BOM: 엑셀에서 한글 깨짐 방지
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r["date"], r["kind"] != "short")):
            w.writerow({k: r.get(k, "") for k in FIELDS})


def today(cfg: dict) -> date:
    return datetime.now(ZoneInfo(cfg["brand"].get("timezone", "Asia/Seoul"))).date()


def entries_on(day: date) -> list[dict]:
    return [r for r in load() if r["date"] == day.isoformat()]


def entry_for(cfg: dict, src: Path) -> dict | None:
    """영상 파일 → 계획표의 해당 날짜·형식 항목 (파일명에 2026-10-19 같은 날짜가 있으면 그 날짜)."""
    m = re.search(r"(\d{4})-?(\d{2})-?(\d{2})", src.stem)
    day = date(int(m[1]), int(m[2]), int(m[3])) if m else today(cfg)
    rows = entries_on(day)
    stem = src.stem.lower()
    fmt = "dialogue" if re.search(r"dialogue|대화|talk", stem) else "solo" if re.search(r"solo|혼자", stem) else None
    kind = "long" if re.search(r"long|롱", stem) else None
    rows = [r for r in rows if (not fmt or r["format"] == fmt) and (not kind or r["kind"] == kind)] or rows
    return rows[0] if rows else None


def card(r: dict) -> str:
    tpl = f"{r['format']}_{r['kind']}"
    points = "\n".join(f"   {i}. {p.strip()}" for i, p in enumerate(r["points"].split("|"), 1) if p.strip())
    return (f"━━━ {r['date']} ({r['weekday']}) · {TRACK.get(r['track'], r['track'])} · {r['pillar']} ━━━\n"
            f"🎬 형식: {tpl} — {TEMPLATE_TIP.get(tpl, '')}\n"
            f"📌 주제: {r['topic']}\n"
            f"🪝 첫 마디(훅): \"{r['hook']}\"\n"
            f"🗣 말할 것:\n{points}\n"
            f"❓ 마무리 질문: \"{r['closing_question']}\"\n"
            f"🏷 제목: {r['title']}\n"
            f"📁 다 찍으면 inbox/ 에 넣기: {r['date']}_{tpl}.mp4\n")


def show(cfg: dict, day: date | None = None, days: int = 1) -> str:
    day = day or today(cfg)
    out = []
    for i in range(days):
        rows = entries_on(day + timedelta(days=i))
        out += [card(r) for r in rows] or [f"{day + timedelta(days=i)}: 계획 없음 — 'python -m studio plan' 으로 생성하세요.\n"]
    return "\n".join(out)


def generate(cfg: dict, days: int, start: date | None = None) -> list[dict]:
    """브랜드 가이드 · 현재 성장 단계 · 시청자 댓글을 반영해 다음 계획을 만든다 (Claude)."""
    if not ai.available():
        raise StudioError("계획 자동 생성에는 ANTHROPIC_API_KEY 가 필요합니다. plan/calendar.csv 를 직접 채워도 됩니다.")
    from .schedule import current_stage

    rows = load()
    last = max((date.fromisoformat(r["date"]) for r in rows), default=today(cfg) - timedelta(days=1))
    start = start or max(last + timedelta(days=1), today(cfg))
    stage = current_stage(cfg)
    guide = (ROOT / "strategy" / "brand-guide.md").read_text(encoding="utf-8")
    playbook = (ROOT / "strategy" / "growth-playbook.md").read_text(encoding="utf-8")
    comments_path = ROOT / "research" / "comments.json"
    comments = json.loads(comments_path.read_text(encoding="utf-8"))[:80] if comments_path.exists() else []
    perf_path = ROOT / "research" / "my_videos.json"
    perf = json.loads(perf_path.read_text(encoding="utf-8"))[:30] if perf_path.exists() else []
    used = [r["topic"] for r in rows][-120:]

    schema = ai.obj({"entries": {"type": "array", "items": ai.obj({
        "date": ai.STR, "track": {"type": "string", "enum": ["A", "B"]},
        "format": {"type": "string", "enum": ["solo", "dialogue"]},
        "kind": {"type": "string", "enum": ["short", "long"]},
        "pillar": ai.STR, "topic": ai.STR, "hook": ai.STR, "points": ai.STRS,
        "closing_question": ai.STR, "title": ai.STR, "hashtags": ai.STRS,
    })}})
    system = ("당신은 이 채널의 콘텐츠 기획자입니다. 브랜드 가이드와 성장 플레이북을 그대로 따르세요.\n\n"
              f"# 브랜드 가이드\n{guide}\n\n# 성장 플레이북\n{playbook}")
    user = (f"현재 단계: {stage['name']} (구독자 {stage['subscribers']}명) — 쇼츠 하루 {stage['shorts_per_day']}개, "
            f"롱폼 주 {stage['long_per_week']}개 ({stage['long_slot']}).\n"
            f"{start.isoformat()}부터 {days}일치 촬영 계획을 브랜드 가이드의 '주간 리듬'대로 만들어 주세요. "
            "롱폼 날은 롱폼 1개만, 쇼츠 2개 날은 형식을 섞으세요.\n"
            "각 항목: hook 은 실제로 카메라 앞에서 첫 마디로 말할 문장, points 는 말할 내용 3개, "
            "hashtags 는 # 없이 4개.\n"
            f"이미 다룬 주제(반복 금지): {used}\n"
            f"시청자 댓글(B 트랙 소재로 활용): {json.dumps(comments, ensure_ascii=False)}\n"
            f"최근 내 영상 성과(잘 된 패턴을 이어가기): {json.dumps(perf, ensure_ascii=False)}")
    log("plan", f"{start}부터 {days}일 계획 생성 중 ({stage['name']})...")
    data = ai.ask_json(cfg, system, user, schema)
    new = []
    for e in data["entries"]:
        d = date.fromisoformat(e["date"])
        new.append({**e, "weekday": WEEKDAYS[d.weekday()], "points": "|".join(e["points"]),
                    "hashtags": " ".join(e["hashtags"]), "status": "계획"})
    save(rows + new)
    log("plan", f"{len(new)}개 항목을 {CAL.relative_to(ROOT)} 에 추가했습니다.")
    return new
