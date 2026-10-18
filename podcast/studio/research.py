"""데이터로 배우기.

benchmark: 비슷한 주제의 구독자 1만+ 채널을 YouTube Data API 로 수집해 성장 패턴을 분석.
listen:    내 채널의 최근 영상 성과와 댓글을 모아 다음 기획(B 트랙)에 반영.
"""
from __future__ import annotations

import csv
import json
import os
import re
import statistics as st
from datetime import datetime, timezone

from . import ai
from .common import ROOT, StudioError, log, write_json

OUT = ROOT / "research"
BANDS = [(10_000, 100_000, "1만~10만"), (100_000, 1_000_000, "10만~100만"), (1_000_000, 10 ** 12, "100만+")]


def _client(cfg: dict):
    key = os.environ.get("YOUTUBE_API_KEY")
    if key:
        from googleapiclient.discovery import build
        return build("youtube", "v3", developerKey=key)
    from .publish import youtube_client
    return youtube_client(cfg)


def seconds(iso: str) -> int:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def _chunks(xs: list, n: int = 50):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def _videos(yt, ids: list[str]) -> list[dict]:
    out = []
    for batch in _chunks(ids):
        for v in yt.videos().list(part="snippet,statistics,contentDetails", id=",".join(batch)).execute()["items"]:
            s = v["statistics"]
            out.append({
                "id": v["id"], "channel": v["snippet"]["channelTitle"], "title": v["snippet"]["title"],
                "published": v["snippet"]["publishedAt"], "seconds": seconds(v["contentDetails"].get("duration")),
                "views": int(s.get("viewCount", 0)), "likes": int(s.get("likeCount", 0)),
                "comments": int(s.get("commentCount", 0)),
            })
    return out


def _recent_uploads(yt, uploads_playlist: str, n: int = 50) -> list[str]:
    resp = yt.playlistItems().list(part="contentDetails", playlistId=uploads_playlist, maxResults=n).execute()
    return [i["contentDetails"]["videoId"] for i in resp.get("items", [])]


def title_features(titles: list[str]) -> dict:
    if not titles:
        return {}
    n = len(titles)
    return {
        "질문형(?)": round(sum("?" in t for t in titles) / n * 100),
        "숫자 포함": round(sum(bool(re.search(r"\d", t)) for t in titles) / n * 100),
        "1인칭(나/내/제가)": round(sum(bool(re.search(r"(^|\s)(나|내|제가|저는)", t)) for t in titles) / n * 100),
        "따옴표 인용": round(sum(bool(re.search(r"[\"'“”‘’]", t)) for t in titles) / n * 100),
        "평균 글자 수": round(st.mean(len(t) for t in titles)),
    }


def benchmark(cfg: dict, keywords: list[str], min_subs: int = 10_000, use_ai: bool = False) -> str:
    yt = _client(cfg)
    OUT.mkdir(exist_ok=True)
    ids: set[str] = set()
    for kw in keywords:  # 키워드당 검색 2회 = 할당량 200
        for kind in ("channel", "video"):
            resp = yt.search().list(part="snippet", q=kw, type=kind, maxResults=50, regionCode="KR",
                                    relevanceLanguage="ko").execute()
            ids |= {i["snippet"]["channelId"] for i in resp.get("items", [])}
    log("research", f"후보 채널 {len(ids)}개 수집, 구독자 {min_subs:,}+ 필터링 중...")

    channels = []
    for batch in _chunks(sorted(ids)):
        for c in yt.channels().list(part="snippet,statistics,contentDetails", id=",".join(batch)).execute()["items"]:
            s = c["statistics"]
            if s.get("hiddenSubscriberCount") or int(s.get("subscriberCount", 0)) < min_subs:
                continue
            channels.append(c)
    if not channels:
        raise StudioError("조건에 맞는 채널이 없습니다. 키워드를 넓히거나 --min-subs 를 낮춰 보세요.")

    now = datetime.now(timezone.utc)
    rows, all_videos = [], []
    for c in channels:
        vids = _videos(yt, _recent_uploads(yt, c["contentDetails"]["relatedPlaylists"]["uploads"]))
        if not vids:
            continue
        subs = int(c["statistics"]["subscriberCount"])
        created = datetime.fromisoformat(c["snippet"]["publishedAt"].replace("Z", "+00:00"))
        dates = sorted(datetime.fromisoformat(v["published"].replace("Z", "+00:00")) for v in vids)
        weeks = max(1.0, (dates[-1] - dates[0]).days / 7)
        shorts = [v for v in vids if 0 < v["seconds"] <= 180]
        longs = [v for v in vids if v["seconds"] > 180]
        med = st.median(v["views"] for v in vids)
        for v in vids:
            v["subs"] = subs
            v["relative"] = round(v["views"] / med, 2) if med else 0
            v["is_short"] = 0 < v["seconds"] <= 180
        all_videos += vids
        rows.append({
            "채널": c["snippet"]["title"], "구독자": subs, "영상 수": int(c["statistics"].get("videoCount", 0)),
            "개설 후 일수": (now - created).days,
            "하루 평균 구독자 증가": round(subs / max(1, (now - created).days), 1),
            "주간 업로드": round(len(vids) / weeks, 1),
            "쇼츠 비율%": round(len(shorts) / len(vids) * 100),
            "쇼츠 조회 중앙값": int(st.median(v["views"] for v in shorts)) if shorts else 0,
            "롱폼 조회 중앙값": int(st.median(v["views"] for v in longs)) if longs else 0,
            "롱폼 평균 길이(분)": round(st.mean(v["seconds"] for v in longs) / 60, 1) if longs else 0,
            "조회/구독 %": round(med / subs * 100, 1),
            "참여율%": round(sum(v["likes"] + v["comments"] for v in vids) / max(1, sum(v["views"] for v in vids)) * 100, 2),
            "인기 제목": " / ".join(v["title"] for v in sorted(vids, key=lambda v: -v["views"])[:3]),
            "url": f"https://youtube.com/channel/{c['id']}",
        })
    rows.sort(key=lambda r: -r["하루 평균 구독자 증가"])

    with open(OUT / "benchmark.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with open(OUT / "benchmark_videos.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(all_videos[0]))
        w.writeheader()
        w.writerows(all_videos)

    def med(rs, k):
        vals = [r[k] for r in rs]
        return st.median(vals) if vals else "-"

    md = [f"# 벤치마크 리포트 ({datetime.now():%Y-%m-%d})", "",
          f"키워드: {', '.join(keywords)} · 구독자 {min_subs:,}+ 채널 {len(rows)}개 · 채널당 최근 영상 최대 50개", "",
          "## 구독자 구간별 중앙값", "",
          "| 구간 | 채널 수 | 주간 업로드 | 쇼츠 비율% | 롱폼 길이(분) | 조회/구독% | 참여율% | 하루 구독 증가 |",
          "|---|---|---|---|---|---|---|---|"]
    for lo, hi, name in BANDS:
        rs = [r for r in rows if lo <= r["구독자"] < hi]
        if rs:
            md.append(f"| {name} | {len(rs)} | {med(rs, '주간 업로드')} | {med(rs, '쇼츠 비율%')} | "
                      f"{med(rs, '롱폼 평균 길이(분)')} | {med(rs, '조회/구독 %')} | {med(rs, '참여율%')} | "
                      f"{med(rs, '하루 평균 구독자 증가')} |")
    top = [v["title"] for v in all_videos if v["relative"] >= 2]
    bottom = [v["title"] for v in all_videos if 0 < v["relative"] <= 0.5]
    md += ["", "## 제목 패턴: 채널 평소 대비 2배 이상 터진 영상 vs 절반 이하 영상", "",
           "| 특징 | 터진 영상 | 안 된 영상 |", "|---|---|---|"]
    tf, bf = title_features(top), title_features(bottom)
    for k in tf:
        md.append(f"| {k} | {tf[k]} | {bf.get(k, '-')} |")
    for label, flag in (("쇼츠", True), ("롱폼", False)):
        vs = sorted((v for v in all_videos if v["is_short"] == flag), key=lambda v: -v["relative"])[:15]
        md += ["", f"## 평소 대비 가장 크게 터진 {label} 15개", ""] + \
              [f"- ×{v['relative']} · {v['channel']} · {v['title']} ({v['views']:,}회)" for v in vs]
    md += ["", "## 하루 구독자 증가가 빠른 채널 TOP 15", "",
           "| 채널 | 구독자 | 하루 증가 | 주간 업로드 | 쇼츠% | 인기 제목 |", "|---|---|---|---|---|---|"]
    md += [f"| [{r['채널']}]({r['url']}) | {r['구독자']:,} | {r['하루 평균 구독자 증가']} | {r['주간 업로드']} | "
           f"{r['쇼츠 비율%']} | {r['인기 제목'][:60]} |" for r in rows[:15]]
    report = "\n".join(md) + "\n"
    (OUT / "benchmark.md").write_text(report, encoding="utf-8")
    log("research", f"리포트: research/benchmark.md (채널 {len(rows)}개, 영상 {len(all_videos)}개)")

    if use_ai and ai.available():
        guide = (ROOT / "strategy" / "growth-playbook.md").read_text(encoding="utf-8")
        data = ai.ask_json(cfg, "당신은 유튜브 성장 분석가입니다. 데이터에 근거해서만 말하고, 근거가 약하면 그렇다고 말하세요.",
                           f"# 현재 플레이북\n{guide}\n\n# 새 벤치마크 데이터\n{report}\n\n"
                           "이 데이터로 플레이북의 각 단계(0~100, 100~1천, 1천~1만, 1만~10만)에서 "
                           "바꾸거나 강화할 점을 구체적으로 정리하세요.",
                           ai.obj({"insights_markdown": ai.STR}))
        (OUT / "benchmark_insights.md").write_text(data["insights_markdown"], encoding="utf-8")
        log("research", "AI 해석: research/benchmark_insights.md")
    return report


def listen(cfg: dict, videos: int = 20) -> None:
    """내 채널 최근 영상 성과 + 댓글 → research/my_videos.json, research/comments.json."""
    from .publish import youtube_client
    yt = youtube_client(cfg)
    me = yt.channels().list(part="contentDetails", mine=True).execute()["items"][0]
    vids = _videos(yt, _recent_uploads(yt, me["contentDetails"]["relatedPlaylists"]["uploads"], videos))
    med = st.median(v["views"] for v in vids) if vids else 0
    for v in vids:
        v["relative"] = round(v["views"] / med, 2) if med else 0
    comments = []
    for v in vids:
        try:
            resp = yt.commentThreads().list(part="snippet", videoId=v["id"], maxResults=50,
                                            order="relevance", textFormat="plainText").execute()
        except Exception:  # 댓글이 꺼진 영상
            continue
        for t in resp.get("items", []):
            c = t["snippet"]["topLevelComment"]["snippet"]
            comments.append({"video": v["title"], "text": c["textDisplay"][:300], "likes": c.get("likeCount", 0),
                             "replies": t["snippet"].get("totalReplyCount", 0)})
    comments.sort(key=lambda c: (-("?" in c["text"]), -c["likes"]))
    OUT.mkdir(exist_ok=True)
    write_json(OUT / "my_videos.json", sorted(vids, key=lambda v: -v["relative"]))
    write_json(OUT / "comments.json", comments)
    log("listen", f"영상 {len(vids)}개 성과, 댓글 {len(comments)}개 수집 → 다음 'plan' 생성에 반영됩니다.")
    for c in comments[:10]:
        log("listen", f"💬 ({c['likes']}♥) {c['text'][:80]}")
