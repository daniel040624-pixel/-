"""doctor: 설치·설정 점검 / selftest: 키 없이 편집→예약 흐름 전체를 시험 실행."""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .common import ROOT, StudioError, find_font, log, read_json

OK, WARN, FAIL = "✅", "⚠️ ", "❌"


def doctor(cfg: dict) -> int:
    rows: list[tuple[str, str, str]] = []

    def add(state: str, item: str, hint: str = "") -> None:
        rows.append((state, item, hint))

    add(OK if sys.version_info >= (3, 11) else FAIL, f"Python {sys.version.split()[0]}", "3.11 이상 필요")
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        filters = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        add(OK if " ass " in filters else FAIL, "ffmpeg (자막 합성 libass)", "libass 포함 빌드 필요")
    else:
        add(FAIL, "ffmpeg", "sudo apt install ffmpeg / brew install ffmpeg")
    try:
        add(OK, f"한글 폰트: {Path(find_font(cfg)).name}")
    except StudioError as e:
        add(FAIL, "한글 폰트", str(e))

    packages = [("anthropic", "AI 기획·제목·하이라이트"), ("PIL", "썸네일"), ("googleapiclient", "유튜브 업로드"),
                ("google_auth_oauthlib", "유튜브 로그인"), ("faster_whisper", "자동 자막"), ("edge_tts", "B 모드 AI 음성")]
    for mod, why in packages:
        try:
            importlib.import_module(mod)
            add(OK, f"패키지 {mod}")
        except ImportError:
            add(FAIL if mod in ("PIL", "googleapiclient", "google_auth_oauthlib") else WARN,
                f"패키지 {mod} ({why})", "pip install -r requirements.txt")

    add(OK if os.environ.get("ANTHROPIC_API_KEY") else WARN, "ANTHROPIC_API_KEY",
        "없으면 계획표 제목을 그대로 쓰고, 하이라이트 쇼츠·plan 자동 생성은 꺼집니다")
    add(OK if os.environ.get("YOUTUBE_API_KEY") else WARN, "YOUTUBE_API_KEY", "benchmark 용 (없으면 OAuth 로그인 사용)")
    up = cfg["upload"]
    add(OK if (ROOT / up["client_secrets"]).exists() else FAIL, up["client_secrets"],
        "README 'YouTube 연결' — 업로드에 필수")
    add(OK if (ROOT / up["token_file"]).exists() else WARN, up["token_file"],
        "첫 업로드 때 브라우저 로그인으로 자동 생성")

    br = cfg["brand"]
    if cfg["channel"]["name"] == "생각의 온도" or br.get("handle") == "@생각의온도":
        add(WARN, "config.toml 채널 이름·핸들이 예시값", "[channel] name, [brand] handle 을 내 채널로 수정")
    from . import plan
    today = plan.today(cfg)
    rows_plan = plan.load()
    future = [r for r in rows_plan if r["date"] >= today.isoformat()]
    add(OK if plan.entries_on(today) else WARN, f"오늘({today}) 촬영 계획",
        "python -m studio plan --days 7" if not plan.entries_on(today) else "")
    add(OK if len(future) >= 7 else WARN, f"남은 계획 {len(future)}일", "7일 미만이면 python -m studio plan")
    channel = ROOT / "plan" / "channel.json"
    if channel.exists():
        add(OK, f"구독자 {read_json(channel)['subscribers']:,}명 기준 단계 적용 중")
    else:
        add(WARN, "현재 단계 미확인 (1단계로 동작)", "python -m studio stage")

    width = max(len(r[1]) for r in rows) + 2
    for state, item, hint in rows:
        print(f"{state} {item:<{width}} {hint if state != OK else ''}")
    fails = sum(r[0] == FAIL for r in rows)
    print(f"\n{'준비 완료!' if not fails else f'{fails}개 항목을 해결하면 업로드까지 자동으로 동작합니다.'}"
          " — 키 없이 흐름만 시험하려면: python -m studio selftest")
    return 1 if fails else 0


def _sample(path: Path, w: int, h: int, seconds: int, period: int, srt_lines: list[str]) -> None:
    """말(톤)과 침묵이 반복되는 시험 영상 + 같은 이름의 자막 파일."""
    speak = period - 2
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={w}x{h}:r=30:d={seconds}",
         "-f", "lavfi", "-i", f"aevalsrc='if(lt(mod(t,{period}),{speak}),0.3*sin(2*PI*200*t),0)':s=48000:d={seconds}",
         "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(path)], check=True)
    blocks = []
    for i, text in enumerate(srt_lines):
        a = i * period
        blocks.append(f"{i + 1}\n00:00:{a:02d},000 --> 00:00:{a + speak - 1:02d},500\n{text}\n")
    path.with_suffix(".srt").write_text("\n".join(blocks), encoding="utf-8")


def selftest(cfg: dict, keep: bool = False) -> int:
    """API 키 없이: 쇼츠·롱폼 편집 → 하이라이트 쇼츠 → 시간표 예약까지 실제로 돌리고 결과를 확인한다."""
    from . import footage, plan, publish, schedule

    out_rel = "daily/_selftest"
    out = ROOT / out_rel
    shutil.rmtree(out, ignore_errors=True)
    cfg = {**cfg, "footage": {**cfg["footage"], "output": out_rel}}
    results: list[tuple[bool, str]] = []

    def check(ok: bool, msg: str) -> None:
        results.append((ok, msg))
        log("selftest", f"{OK if ok else FAIL} {msg}")

    today = plan.today(cfg).isoformat()
    with tempfile.TemporaryDirectory() as tmp:
        short_src = Path(tmp) / f"{today}_solo_short.mp4"
        long_src = Path(tmp) / f"{today}_dialogue_long.mp4"
        log("selftest", "시험 영상 만드는 중...")
        _sample(short_src, 1280, 720, 20, 5, ["오늘 스스로에게 몇 번 질문하셨나요?", "답보다 질문이 중요해지는 시대예요.",
                                              "오늘 10분만 AI 없이 생각해 보세요.", "그게 생각의 근육을 키우는 첫걸음입니다."])
        _sample(long_src, 1920, 1080, 48, 8, [f"{i + 1}번째 대화입니다. 남의 답으로 사는 게 왜 편할까요?" for i in range(6)])

        d1 = footage.process(cfg, short_src, entry=plan.entry_for(cfg, short_src))
        item = read_json(d1 / "item.json")
        w, h, dur = footage.probe(d1 / "final.mp4")
        check((w, h) == (1080, 1920), f"쇼츠 세로 변환 {w}x{h}")
        check(dur < 20, f"공백 삭제 20초 → {dur:.1f}초")
        check((d1 / "final.srt").stat().st_size > 0, "자막 생성")

        real_describe = footage.describe
        if not __import__("studio.ai", fromlist=["available"]).available():
            def fake(cfg_, tpl, segs, entry, n):  # 키가 없으면 하이라이트 선택만 가짜로 대신한다
                meta = real_describe(cfg_, tpl, [], entry, 0)
                meta["clips"] = [{"title": "남의 답이 편한 이유?", "start_index": 1, "end_index": 2},
                                 {"title": "내 질문 찾기", "start_index": 3, "end_index": 4}][:n]
                return meta
            footage.describe = fake
        try:
            d2 = footage.process(cfg, long_src, template="dialogue_long", entry=plan.entry_for(cfg, long_src))
        finally:
            footage.describe = real_describe
        item2 = read_json(d2 / "item.json")
        check(footage.probe(d2 / "final.mp4")[:2] == (1920, 1080), "롱폼 1920x1080")
        check((d2 / "thumbnail.jpg").exists(), "롱폼 썸네일")
        clips = [f for f in item2["files"] if f["path"].startswith("clip")]
        check(len(clips) >= 1 and all(footage.probe(d2 / c["path"])[:2] == (1080, 1920) for c in clips),
              f"하이라이트 쇼츠 {len(clips)}개 (세로)")

    # 업로드: 유튜브 대신 가짜 클라이언트로 예약 시간 배정만 확인
    real = (publish.youtube_client, publish.upload_video, publish.upload_captions)
    uploaded: list[tuple[str, object]] = []

    class FakeYT:
        def thumbnails(self):
            return self

        def set(self, **_):
            return self

        def execute(self):
            return {}

    publish.youtube_client = lambda _cfg: FakeYT()
    publish.upload_video = lambda yt, c, path, title, desc, tags, synthetic, publish_at=None: (
        uploaded.append((path.name, publish_at)) or f"test{len(uploaded)}")
    publish.upload_captions = lambda *a, **k: None
    try:
        schedule.publish(cfg, d1, "now")
        schedule.publish(cfg, d2, "auto")
    except ImportError:
        check(False, "업로드 시험에 google-api-python-client 필요")
    finally:
        publish.youtube_client, publish.upload_video, publish.upload_captions = real
    if uploaded:
        check(uploaded[0][1] is None, "쇼츠 즉시 업로드")
        times = [t for _, t in uploaded[1:]]
        check(all(times) and times == sorted(times), "롱폼 → 클립 순서대로 예약 시간 배정")
        for name, t in uploaded:
            log("selftest", f"   {name:<10} {t.strftime('%m/%d(%a) %H:%M') if t else '즉시'}")
        check((d2 / "clip1_caption.txt").exists(), "릴스·틱톡용 캡션 파일")

    passed = sum(ok for ok, _ in results)
    print(f"\n시험 결과: {passed}/{len(results)} 통과" + (f" — 결과물: {out}" if keep else ""))
    if not keep:
        shutil.rmtree(out, ignore_errors=True)
    return 0 if passed == len(results) else 1
