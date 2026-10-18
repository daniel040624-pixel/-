"""찍은 영상 → 템플릿대로 자동 편집.

공백 삭제(점프컷) → 받아쓰기 자막 → 화면비 맞춤 → 제목·진행바·CTA·워터마크 → 음량 표준화.
롱폼이면 하이라이트 쇼츠까지 잘라내고, 업로드용 제목·설명·태그를 item.json 에 남긴다.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tomllib
from datetime import date
from pathlib import Path

from . import ai
from .common import ROOT, StudioError, find_font, log, run, write_json
from .edit import split_caption, write_srt
from .render import _ass_color, _ass_time, _esc, _rgb, _wrap

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}


# ─── 템플릿 ───

def load_template(name: str) -> dict:
    path = ROOT / "templates" / f"{name}.toml"
    if not path.exists():
        names = ", ".join(p.stem for p in (ROOT / "templates").glob("*.toml"))
        raise StudioError(f"템플릿 '{name}' 이 없습니다. 사용 가능: {names}")
    with open(path, "rb") as f:
        return tomllib.load(f)


def guess_template(src: Path, entry: dict | None) -> str:
    """파일 이름(solo_/dialogue_, _long) → 오늘 계획 → 화면 방향 순으로 템플릿을 고른다."""
    stem = src.stem.lower()
    fmt = "dialogue" if re.search(r"dialogue|대화|talk", stem) else "solo" if re.search(r"solo|혼자", stem) else None
    kind = "long" if re.search(r"long|롱", stem) else "short" if re.search(r"short|쇼츠", stem) else None
    if entry:
        fmt = fmt or entry.get("format")
        kind = kind or entry.get("kind")
    if not kind:
        w, h, _ = probe(src)
        kind = "short" if h > w or (fmt or "solo") == "solo" else "long"
    return f"{fmt or 'solo'}_{kind}"


# ─── ffmpeg 유틸 ───

def probe(path: Path) -> tuple[int, int, float]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:stream_side_data=rotation:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    st = data["streams"][0]
    w, h = st["width"], st["height"]
    rot = next((abs(int(sd.get("rotation", 0))) for sd in st.get("side_data_list", []) if "rotation" in sd), 0)
    if rot in (90, 270):  # 휴대폰 세로 영상
        w, h = h, w
    return w, h, float(data["format"]["duration"])


def speech_segments(src: Path, total: float, s: dict) -> list[tuple[float, float]]:
    """silencedetect 로 말하는 구간만 남긴다."""
    if not s.get("cut", True):
        return [(0.0, total)]
    err = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(src), "-vn", "-af",
         f"silencedetect=noise={s.get('threshold_db', -35)}dB:d={s.get('min_silence', 0.5)}", "-f", "null", "-"],
        capture_output=True, text=True).stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    pad = s.get("pad", 0.12)
    keep, t = [], 0.0
    for i, st in enumerate(starts):
        en = ends[i] if i < len(ends) else total
        if st - t > 0.05:
            keep.append((max(0.0, t - pad), min(total, st + pad)))
        t = en
    if total - t > 0.05:
        keep.append((max(0.0, t - pad), total))
    merged: list[list[float]] = []
    for a, b in keep:  # 패딩으로 겹치는 구간 합치기
        if merged and a <= merged[-1][1] + 0.02:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged if b - a > 0.15] or [(0.0, total)]


def cut_video(src: Path, segs: list[tuple[float, float]], out: Path, limit: float = 0) -> float:
    if limit:
        kept, acc = [], 0.0
        for a, b in segs:
            if acc >= limit:
                break
            b = min(b, a + limit - acc)
            kept.append((a, b))
            acc += b - a
        segs = kept
    parts = []
    for i, (a, b) in enumerate(segs):
        parts.append(f"[0:v]trim={a:.3f}:{b:.3f},setpts=PTS-STARTPTS[v{i}];"
                     f"[0:a]atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS[a{i}];")
    graph = "".join(parts) + "".join(f"[v{i}][a{i}]" for i in range(len(segs))) + \
        f"concat=n={len(segs)}:v=1:a=1[v][a]"
    script = out.with_suffix(".filter.txt")
    script.write_text(graph, encoding="utf-8")
    run(["ffmpeg", "-y", "-i", src, "-filter_complex_script", script, "-map", "[v]", "-map", "[a]",
         "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", out])
    return sum(b - a for a, b in segs)


# ─── 받아쓰기 ───

def transcribe(video: Path, model: str) -> list[dict]:
    """[{start, end, text, words:[{start,end,word}]}] — faster-whisper 가 없으면 빈 목록."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        log("cut", "faster-whisper 가 없어 자막 없이 진행합니다 (pip install faster-whisper).")
        return []
    log("cut", f"받아쓰는 중 (whisper {model})...")
    segs, _ = WhisperModel(model, compute_type="int8").transcribe(
        str(video), language="ko", word_timestamps=True, vad_filter=True)
    return [{"start": s.start, "end": s.end, "text": s.text.strip(),
             "words": [{"start": w.start, "end": w.end, "word": w.word} for w in (s.words or [])]}
            for s in segs]


def load_srt(path: Path) -> list[dict]:
    def sec(ts: str) -> float:
        h, m, rest = ts.replace(",", ".").split(":")
        return int(h) * 3600 + int(m) * 60 + float(rest)

    out = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").strip()):
        lines = block.strip().splitlines()
        if len(lines) >= 3 and "-->" in lines[1]:
            a, b = (x.strip() for x in lines[1].split("-->"))
            out.append({"start": sec(a), "end": sec(b), "text": " ".join(lines[2:]), "words": []})
    return out


def remap(segments: list[dict], keep: list[tuple[float, float]]) -> list[dict]:
    """원본 기준 자막 시간을 공백 삭제 후 시간으로 옮긴다."""
    def f(t: float) -> float | None:
        acc = 0.0
        for a, b in keep:
            if a <= t <= b:
                return acc + t - a
            acc += b - a
        return None

    out = []
    for s in segments:
        a, b = f(s["start"]), f(s["end"])
        if a is not None and b is not None and b > a:
            out.append({**s, "start": a, "end": b, "words": []})
    return out


def captions(segments: list[dict], max_chars: int) -> list[dict]:
    caps = []
    for s in segments:
        words = s.get("words") or []
        if words:  # 단어 시간 기준으로 끊어 말과 자막이 정확히 맞게
            cur, start = "", words[0]["start"]
            for w in words:
                token = w["word"].strip()
                if cur and len(cur) + 1 + len(token) > max_chars:
                    caps.append({"start": start, "end": w["start"], "text": cur})
                    cur, start = token, w["start"]
                else:
                    cur = f"{cur} {token}".strip()
            if cur:
                caps.append({"start": start, "end": words[-1]["end"], "text": cur})
        else:
            chunks = split_caption(s["text"], max_chars)
            total = sum(len(c) for c in chunks) or 1
            t = s["start"]
            for c in chunks:
                d = (s["end"] - s["start"]) * len(c) / total
                caps.append({"start": t, "end": t + d, "text": c})
                t += d
    return caps


# ─── 화면 구성 ───

def reframe(fit: str, w: int, h: int) -> str:
    if fit == "crop":
        return f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1[base]"
    if fit == "blurpad":
        return (f"[0:v]split[a][b];[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
                f"boxblur=30:4,eq=brightness=-0.18[bg];[b]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[base]")
    return (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[base]")


def write_ass(cfg: dict, tpl: dict, path: Path, caps: list[dict], title: str, total: float) -> None:
    from PIL import ImageFont
    w, h = tpl["width"], tpl["height"]
    family = ImageFont.truetype(find_font(cfg), 10).getname()[0]
    accent = cfg["video"].get("accent", "#FFD166")
    sub, ttl, ex = tpl["subtitles"], tpl["title"], tpl["extras"]
    margin_x = int(w * 0.07)
    sub_margin = int(h * (1 - sub.get("y", 0.85)))
    styles = [
        # 자막: 흰 글씨 + 두꺼운 검은 테두리
        f"Style: Sub,{family},{sub['size']},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,"
        f"{max(4, sub['size'] // 11)},1,2,{margin_x},{margin_x},{sub_margin},1",
        # 제목: 포인트 색 박스 위 검은 글씨
        f"Style: Title,{family},{ttl.get('size', 60)},&H00141414,&H00FFFFFF,{_ass_color(accent)},"
        f"{_ass_color(accent)},1,0,0,0,100,100,0,0,3,{max(10, ttl.get('size', 60) // 4)},0,8,{margin_x},{margin_x},"
        f"{int(h * 0.07)},1",
        f"Style: CTA,{family},{int(sub['size'] * 0.95)},{_ass_color(accent)},&H00FFFFFF,&H00000000,&H64000000,"
        f"1,0,0,0,100,100,0,0,1,{max(4, sub['size'] // 11)},1,5,{margin_x},{margin_x},0,1",
        f"Style: Mark,{family},{int(h * 0.022)},&H60FFFFFF,&H00FFFFFF,&H90000000,&H00000000,"
        f"0,0,0,0,100,100,0,0,1,1,0,3,{int(w * 0.03)},{int(w * 0.03)},{int(h * 0.03)},1",
    ]
    ev = []
    cta_text = cfg["brand"].get(ex.get("cta", ""), "") if ex.get("cta") else ""
    cta_start = max(0.0, total - 2.5) if cta_text else total
    for c in caps:
        if c["start"] >= cta_start:
            continue
        ev.append(f"Dialogue: 0,{_ass_time(c['start'])},{_ass_time(min(c['end'], cta_start))},Sub,,0,0,0,,{_esc(c['text'])}")
    if ttl.get("show") and title:
        from PIL import Image, ImageDraw
        d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        lines = _wrap(d, title, ImageFont.truetype(find_font(cfg), ttl.get("size", 60)), int(w * 0.8), 2)
        end = ttl.get("seconds") or total
        text = "\\N".join(_esc(line) for line in lines)
        ev.append(f"Dialogue: 1,{_ass_time(0)},{_ass_time(end)},Title,,0,0,0,,{text}")
    if cta_text:
        ev.append(f"Dialogue: 2,{_ass_time(cta_start)},{_ass_time(total)},CTA,,0,0,0,,"
                  "{\\fad(200,0)}" + _esc(cta_text))
    if ex.get("watermark") and cfg["brand"].get("handle"):
        ev.append(f"Dialogue: 0,{_ass_time(0)},{_ass_time(total)},Mark,,0,0,0,,{_esc(cfg['brand']['handle'])}")
    path.write_text(
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {w}\nPlayResY: {h}\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n" + "\n".join(styles) +
        "\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n" +
        "\n".join(ev) + "\n", encoding="utf-8")


def render(cfg: dict, tpl: dict, src: Path, caps: list[dict], title: str, out: Path,
           start: float = 0.0, length: float | None = None) -> Path:
    """잘라둔 영상(src) → 템플릿 화면 구성 + 음향 처리된 최종 영상."""
    w, h = tpl["width"], tpl["height"]
    total = length if length else probe(src)[2] - start
    caps = [{**c, "start": c["start"] - start, "end": c["end"] - start}
            for c in caps if c["end"] > start and c["start"] < start + total]
    ass = out.with_suffix(".ass")
    write_ass(cfg, tpl, ass, caps, title, total)
    write_srt(out.with_suffix(".srt"), caps)
    accent = cfg["video"].get("accent", "#FFD166").lstrip("#")
    chain = reframe(tpl.get("fit", "crop"), w, h) + ";[base]"
    if tpl["extras"].get("progress_bar"):
        chain += f"drawbox=x=0:y=ih-14:w=iw*t/{total:.3f}:h=14:color=0x{accent}@0.95:t=fill,"
    fontsdir = Path(find_font(cfg)).parent.as_posix()
    chain += f"ass=filename={ass.name}:fontsdir='{fontsdir}',format=yuv420p[v]"
    # 목소리 정리: 저역 잡음 제거 → 가벼운 노이즈 감소 → 컴프레서 → SNS 표준 음량(-14 LUFS)
    audio = "[0:a]highpass=f=80,afftdn=nf=-25,acompressor=threshold=-20dB:ratio=3:attack=5:release=120," \
            "loudnorm=I=-14:TP=-1.5:LRA=9[a]"
    trim = ["-ss", f"{start:.3f}", "-t", f"{total:.3f}"] if (start or length) else []
    run(["ffmpeg", "-y", *trim, "-i", src.name, "-filter_complex", f"{chain};{audio}",
         "-map", "[v]", "-map", "[a]", "-r", "30", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", out.name], cwd=src.parent)
    ass.unlink(missing_ok=True)
    return out


def thumbnail(cfg: dict, video: Path, text: str, out: Path) -> Path:
    from PIL import Image, ImageDraw, ImageEnhance, ImageFont
    frame = out.with_suffix(".frame.png")
    run(["ffmpeg", "-y", "-ss", f"{probe(video)[2] * 0.3:.2f}", "-i", video, "-frames:v", "1",
         "-vf", "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720", frame])
    img = ImageEnhance.Brightness(Image.open(frame).convert("RGB")).enhance(0.55)
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(find_font(cfg), 104)
    lines = _wrap(d, text, f, 1140, 3)
    y = 720 - 80 - len(lines) * 128
    accent = _rgb(cfg["video"].get("accent", "#FFD166"))
    for i, line in enumerate(lines):
        d.text((64, y), line, font=f, fill=accent if i == len(lines) - 1 else (255, 255, 255),
               stroke_width=6, stroke_fill=(0, 0, 0))
        y += 128
    img.save(out, quality=92)
    frame.unlink(missing_ok=True)
    return out


# ─── 제목·설명·하이라이트 (Claude) ───

def brand_context() -> str:
    p = ROOT / "strategy" / "brand-guide.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def describe(cfg: dict, tpl: dict, segments: list[dict], entry: dict | None, n_clips: int) -> dict:
    fallback = {
        "title": (entry or {}).get("title") or (entry or {}).get("topic") or "",
        "description": (entry or {}).get("description", ""),
        "tags": [t.strip("# ") for t in (entry or {}).get("hashtags", "").split() if t.strip("# ")],
        "thumbnail_text": (entry or {}).get("hook", ""),
        "clips": [],
    }
    if not segments or not ai.available():
        if n_clips:
            log("cut", "하이라이트 자동 선택에는 자막(받아쓰기)과 ANTHROPIC_API_KEY 가 필요합니다.")
        return fallback
    lines = "\n".join(f"[{i}] ({s['start']:.1f}~{s['end']:.1f}s) {s['text']}" for i, s in enumerate(segments))
    schema = ai.obj({
        "title": ai.STR, "description": ai.STR, "tags": ai.STRS, "thumbnail_text": ai.STR,
        "clips": {"type": "array", "items": ai.obj({"title": ai.STR, "start_index": ai.INT, "end_index": ai.INT})},
    })
    system = ("당신은 이 유튜브 채널의 편집장입니다. 아래 브랜드 가이드의 톤앤매너를 반드시 지키세요.\n\n" + brand_context())
    clip_ask = (f"clips 에는 맥락 없이 들어도 훅이 강한 연속 구간을 {n_clips}개 고르세요"
                "(각 25~55초, 질문이나 반전으로 시작하는 구간, start_index~end_index 는 위 번호). "
                "clip title 은 쇼츠 상단에 띄울 질문형 문구(18자 이내)입니다.") if n_clips else "clips 는 빈 배열로 두세요."
    user = (f"방금 찍은 {'대화' if tpl['format'] == 'dialogue' else '혼자 말하기'} "
            f"{'쇼츠' if tpl['kind'] == 'short' else '롱폼'} 영상의 받아쓰기입니다.\n"
            f"오늘 계획: {json.dumps(entry, ensure_ascii=False) if entry else '없음'}\n\n{lines}\n\n"
            "업로드용 title(쇼츠는 40자 이내, 롱폼은 60자 이내, 클릭하고 싶지만 낚시 없이), "
            "description(3~6줄: 핵심 한 줄, 생각해볼 질문, 채널 약속), tags(8~12개), "
            "thumbnail_text(12자 이내)를 써 주세요. " + clip_ask)
    try:
        data = ai.ask_json(cfg, system, user, schema, effort="medium")
    except Exception as e:  # 메타데이터 실패로 편집 결과를 버리지 않는다
        log("cut", f"Claude 제목 생성 실패, 계획표 값 사용: {e}")
        return fallback
    if entry and entry.get("title"):
        data["title"] = entry["title"]
    return data


# ─── 전체 흐름 ───

def item_dir(cfg: dict, src: Path, entry: dict | None) -> Path:
    base = ROOT / cfg["footage"].get("output", "daily")
    day = (entry or {}).get("date") or f"{date.today():%Y-%m-%d}"
    stem = re.sub(r"\d{4}-?\d{2}-?\d{2}", "", src.stem)  # 파일명의 날짜는 폴더 앞에 이미 붙는다
    slug = re.sub(r"[^\w가-힣]+", "-", stem).strip("-_")[:30] or "video"
    d = base / f"{day}-{slug}"
    n = 2
    while d.exists() and not (d / "item.json").exists() and any(d.iterdir()):
        d = base / f"{day}-{slug}-{n}"
        n += 1
    d.mkdir(parents=True, exist_ok=True)
    return d


def process(cfg: dict, src: Path, template: str | None = None, entry: dict | None = None,
            move: bool = False) -> Path:
    src = src.resolve()
    if src.suffix.lower() not in VIDEO_EXT:
        raise StudioError(f"영상 파일이 아닙니다: {src.name}")
    name = template or guess_template(src, entry)
    tpl = load_template(name)
    d = item_dir(cfg, src, entry)
    raw = d / f"raw{src.suffix.lower()}"
    if move:
        shutil.move(str(src), raw)
    elif not raw.exists():
        shutil.copy2(src, raw)
    sidecar = src.with_suffix(".srt")
    log("cut", f"{src.name} → 템플릿 '{name}' → {d.relative_to(ROOT)}")

    _, _, total = probe(raw)
    keep = speech_segments(raw, total, tpl["silence"])
    cut = d / "cut.mp4"
    length = cut_video(raw, keep, cut, tpl.get("max_seconds", 0))
    log("cut", f"공백 삭제: {total:.1f}초 → {length:.1f}초 ({len(keep)}개 구간)")
    if tpl.get("max_seconds") and sum(b - a for a, b in keep) > tpl["max_seconds"]:
        log("cut", f"⚠ {tpl['max_seconds']}초를 넘어 뒷부분을 잘랐습니다. 더 짧게 찍거나 롱폼 템플릿을 쓰세요.")

    if sidecar.exists():
        segments = remap(load_srt(sidecar), keep)
    else:
        segments = transcribe(cut, cfg["footage"].get("whisper_model", "small"))
    n_clips = tpl["extras"].get("clips", 0) if tpl["kind"] == "long" else 0
    meta = describe(cfg, tpl, segments, entry, n_clips)
    # 화면 상단 박스·썸네일 문구: Claude 가 만든 짧은 문구 → 계획표 제목(| 앞부분) → 영상 제목
    plan_title = ((entry or {}).get("title") or "").split("|")[0].strip()
    title_on_screen = meta.get("thumbnail_text") or plan_title or meta["title"]

    caps = captions(segments, tpl["subtitles"]["max_chars"])
    final = render(cfg, tpl, cut, caps, title_on_screen, d / "final.mp4")
    files = [{"path": final.name, "kind": tpl["kind"], "title": meta["title"] or src.stem,
              "srt": final.with_suffix(".srt").name}]
    if tpl["kind"] == "long":
        thumbnail(cfg, final, title_on_screen or src.stem, d / "thumbnail.jpg")
        files[0]["thumbnail"] = "thumbnail.jpg"
        clip_tpl = load_template(tpl["extras"].get("clip_template", "dialogue_short"))
        clip_tpl["silence"] = {"cut": False}
        for i, c in enumerate(meta.get("clips", [])[:n_clips], 1):
            seg = segments[max(0, c["start_index"]):min(len(segments), c["end_index"] + 1)]
            if not seg:
                continue
            a, b = max(0.0, seg[0]["start"] - 0.2), seg[-1]["end"] + 0.4
            b = min(b, a + clip_tpl.get("max_seconds", 59))
            clip_caps = captions(seg, clip_tpl["subtitles"]["max_chars"])
            out = render(cfg, clip_tpl, cut, clip_caps, c["title"], d / f"clip{i}.mp4", start=a, length=b - a)
            files.append({"path": out.name, "kind": "short", "title": c["title"], "srt": out.with_suffix(".srt").name})
            log("cut", f"하이라이트 쇼츠 {i}: '{c['title']}' ({b - a:.0f}초)")

    item = {"template": name, "format": tpl["format"], "kind": tpl["kind"],
            "plan_date": (entry or {}).get("date"), "track": (entry or {}).get("track"),
            "title": meta["title"] or src.stem, "description": meta.get("description", ""),
            "tags": meta.get("tags", []), "files": files, "uploads": {}}
    write_json(d / "item.json", item)
    (d / "cut.filter.txt").unlink(missing_ok=True)
    log("cut", f"완료: {', '.join(f['path'] for f in files)} — 확인 후 'python -m studio publish {d.relative_to(ROOT)}'")
    return d
