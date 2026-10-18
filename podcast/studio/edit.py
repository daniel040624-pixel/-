"""3단계: 대사 음성을 이어 붙이고, 음악·음량을 다듬고, 자막과 타임라인을 만든다."""
from __future__ import annotations

import re
from pathlib import Path

from .common import Episode, StudioError, duration, log, run, write_json
from .voice import jobs

SR = ["-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le"]


def _wav(src: Path, dst: Path) -> Path:
    if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
        run(["ffmpeg", "-y", "-i", src, *SR, dst])
    return dst


def _silence(ep: Episode, sec: float) -> Path:
    p = ep.build / "lines" / f"silence_{int(sec * 1000)}.wav"
    if not p.exists():
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", f"{sec}", *SR, p])
    return p


def split_caption(text: str, limit: int = 32) -> list[str]:
    """화면 자막용으로 문장/길이 기준으로 자른다."""
    sentences = [s.strip() for s in re.split(r"(?<=[.?!…])\s+", text) if s.strip()]
    chunks: list[str] = []
    for s in sentences:
        cur = ""
        for word in s.split():
            if cur and len(cur) + 1 + len(word) > limit:
                chunks.append(cur)
                cur = word
            else:
                cur = f"{cur} {word}".strip()
        if cur:
            chunks.append(cur)
    return chunks or [text]


def _concat_voice(ep: Episode, script: dict) -> tuple[Path, list[dict]]:
    vc = ep.cfg["voice"]
    files = iter(jobs(ep, script))
    parts: list[Path] = []
    timeline: list[dict] = []
    t = 0.0
    intro = ep.asset(ep.cfg["audio"].get("intro", ""))
    if intro:
        parts.append(_wav(intro, ep.build / "intro.wav"))
        t += duration(parts[-1])

    for si, sec in enumerate(script["sections"]):
        if si:
            parts.append(_silence(ep, vc.get("section_gap", 1.2)))
            t += vc.get("section_gap", 1.2)
        for li, line in enumerate(sec["lines"]):
            if li:
                parts.append(_silence(ep, vc.get("line_gap", 0.35)))
                t += vc.get("line_gap", 0.35)
            mp3, _, _ = next(files)
            if not mp3.exists():
                raise StudioError("음성 파일이 없습니다. voice 단계를 먼저 실행하세요.")
            wav = _wav(mp3, mp3.with_suffix(".wav"))
            d = duration(wav)
            timeline.append({"section": si, "line": li, "speaker": line["speaker"],
                             "text": line["text"], "start": round(t, 3), "end": round(t + d, 3)})
            parts.append(wav)
            t += d

    outro = ep.asset(ep.cfg["audio"].get("outro", ""))
    if outro:
        parts.append(_silence(ep, 0.6))
        parts.append(_wav(outro, ep.build / "outro.wav"))

    lst = ep.path("concat.txt")
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts), encoding="utf-8")
    voice = ep.path("voice.wav")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", voice])
    return voice, timeline


def _from_recording(ep: Episode) -> tuple[Path, list[dict]]:
    """직접 녹음한 파일을 쓰는 모드. faster-whisper 가 있으면 자막도 자동 생성."""
    src = ep.dir / ep.ep["recording"]
    voice = ep.path("voice.wav")
    run(["ffmpeg", "-y", "-i", src, "-af", "highpass=f=80,afftdn=nf=-25", *SR, voice])
    me = ep.hosts[0]["id"]
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        log("edit", "faster-whisper 가 없어 자막 없이 진행합니다 (pip install faster-whisper).")
        return voice, []
    log("edit", "녹음 음성을 받아쓰는 중 (faster-whisper)...")
    segs, _ = WhisperModel("small", compute_type="int8").transcribe(str(voice), language="ko")
    return voice, [{"section": 0, "line": i, "speaker": me, "text": s.text.strip(),
                    "start": round(s.start, 3), "end": round(s.end, 3)} for i, s in enumerate(segs)]


def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def captions(timeline: list[dict]) -> list[dict]:
    """대사 하나를 자막 여러 줄로 나누고 길이에 비례해 시간을 배분한다."""
    out = []
    for item in timeline:
        chunks = split_caption(item["text"])
        total = sum(len(c) for c in chunks)
        t = item["start"]
        span = item["end"] - item["start"]
        for c in chunks:
            d = span * len(c) / total
            out.append({"speaker": item["speaker"], "text": c, "start": t, "end": t + d})
            t += d
    return out


def write_srt(path: Path, caps: list[dict], names: dict[str, str] | None = None, offset: float = 0.0) -> None:
    rows = []
    for i, c in enumerate(caps, 1):
        who = f"{names[c['speaker']]}: " if names else ""
        rows.append(f"{i}\n{_srt_time(c['start'] - offset)} --> {_srt_time(c['end'] - offset)}\n{who}{c['text']}\n")
    path.write_text("\n".join(rows), encoding="utf-8")


def master(ep: Episode, script: dict | None) -> dict:
    if ep.ep.get("recording"):
        voice, timeline = _from_recording(ep)
    else:
        voice, timeline = _concat_voice(ep, script)

    au = ep.cfg["audio"]
    out = ep.path("episode.mp3")
    norm = f"loudnorm=I={au.get('loudness', -16)}:TP=-1.5:LRA=11"
    bgm = ep.asset(au.get("bgm", ""))
    if bgm:
        # 목소리가 나올 때 배경음악이 자동으로 작아지는(덕킹) 믹스
        fc = (f"[1:a]volume={au.get('bgm_volume', 0.08)},aformat=channel_layouts=stereo[m];"
              f"[0:a]aformat=channel_layouts=stereo,asplit[v][sc];"
              f"[m][sc]sidechaincompress=threshold=0.02:ratio=6:attack=20:release=400[duck];"
              f"[v][duck]amix=inputs=2:duration=first:normalize=0,{norm}[a]")
        run(["ffmpeg", "-y", "-i", voice, "-stream_loop", "-1", "-i", bgm, "-filter_complex", fc,
             "-map", "[a]", "-ar", "48000", "-b:a", "192k", out])
    else:
        run(["ffmpeg", "-y", "-i", voice, "-af", norm, "-ar", "48000", "-ac", "2", "-b:a", "192k", out])

    total = duration(out)
    caps = captions(timeline)
    names = {h["id"]: h["name"] for h in ep.hosts}
    write_srt(ep.path("subtitles.srt"), caps)                       # 유튜브 자막 업로드용
    write_srt(ep.path("transcript.srt"), caps, names)                # 화자 표시 대본
    chapters = []
    if script:
        for si, sec in enumerate(script["sections"]):
            first = next((x for x in timeline if x["section"] == si), None)
            if first:
                chapters.append({"title": sec["title"], "start": 0.0 if si == 0 else first["start"]})
    data = {"duration": total, "timeline": timeline, "captions": caps, "chapters": chapters}
    write_json(ep.path("timeline.json"), data)
    log("edit", f"완료: {out.name} ({total / 60:.1f}분), 자막 {len(caps)}줄")
    return data
