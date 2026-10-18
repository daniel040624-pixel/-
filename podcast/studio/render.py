"""4단계: 오디오를 영상(가로 본편 + 세로 쇼츠)과 썸네일로 만든다."""
from __future__ import annotations

from pathlib import Path

from .common import Episode, StudioError, log, run, write_json
from .edit import write_srt

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except ImportError:  # pragma: no cover
    Image = None


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _font(ep: Episode, size: int):
    return ImageFont.truetype(ep.font, size)


def _wrap(draw, text: str, font, width: int, max_lines: int = 3) -> list[str]:
    lines, cur = [], ""
    for ch in text:  # 한국어는 글자 단위로 재며 공백에서 우선 끊는다
        trial = cur + ch
        if draw.textlength(trial, font=font) <= width:
            cur = trial
            continue
        cut = cur.rfind(" ")
        if cut > len(cur) // 2:
            lines.append(cur[:cut])
            cur = cur[cut + 1:] + ch
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return [l.strip() for l in lines][:max_lines]


def _canvas(ep: Episode, w: int, h: int):
    bg = ep.asset(ep.cfg["video"].get("background", ""))
    if bg:
        img = Image.open(bg).convert("RGB")
        scale = max(w / img.width, h / img.height)
        img = img.resize((int(img.width * scale) + 1, int(img.height * scale) + 1))
        left, top = (img.width - w) // 2, (img.height - h) // 2
        img = img.crop((left, top, left + w, top + h)).filter(ImageFilter.GaussianBlur(2))
        return Image.blend(img, Image.new("RGB", (w, h), (0, 0, 0)), 0.55)
    # 기본 배경: 짙은 남색 세로 그라데이션
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    top, bottom = (14, 18, 38), (38, 22, 58)
    for y in range(h):
        k = y / h
        d.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * k) for a, b in zip(top, bottom)))
    return img


def background(ep: Episode, title: str, w: int, h: int, out: Path, vertical: bool = False) -> Path:
    img = _canvas(ep, w, h)
    d = ImageDraw.Draw(img)
    accent = _rgb(ep.cfg["video"].get("accent", "#FFD166"))
    ch = ep.cfg["channel"]
    pad = int(w * 0.06)
    d.text((pad, int(h * 0.05)), ch["name"], font=_font(ep, int(h * (0.028 if vertical else 0.045))), fill=accent)
    size = int(w * (0.085 if vertical else 0.042))
    f = _font(ep, size)
    y = int(h * (0.12 if vertical else 0.16))
    d.rectangle([pad, y, pad + int(size * 0.18), y + size * 2], fill=accent)
    for line in _wrap(d, title, f, w - pad * 2 - size, 4 if vertical else 2):
        d.text((pad + size * 0.6, y), line, font=f, fill=(255, 255, 255))
        y += int(size * 1.3)
    names = "  ·  ".join(hh["name"] for hh in ep.hosts)
    d.text((pad, h - int(h * 0.05) - 30), names, font=_font(ep, int(h * 0.025)), fill=(200, 200, 215))
    img.save(out)
    return out


def thumbnail(ep: Episode, script: dict | None) -> Path:
    out = ep.path("thumbnail.jpg")
    w, h = 1280, 720
    img = _canvas(ep, w, h)
    d = ImageDraw.Draw(img)
    accent = _rgb(ep.cfg["video"].get("accent", "#FFD166"))
    text = (script or {}).get("thumbnail_text") or ep.ep["topic"]
    f = _font(ep, 120)
    lines = _wrap(d, text, f, w - 140, 3)
    y = (h - len(lines) * 150) // 2
    for i, line in enumerate(lines):
        color = accent if i == len(lines) - 1 else (255, 255, 255)
        d.text((70, y), line, font=f, fill=color, stroke_width=6, stroke_fill=(0, 0, 0))
        y += 150
    d.rectangle([0, h - 70, w, h], fill=accent)
    d.text((70, h - 60), ep.cfg["channel"]["name"], font=_font(ep, 44), fill=(20, 20, 30))
    img.convert("RGB").save(out, quality=92)
    return out


# ─── ASS 자막 (화자별 색 + 현재 챕터 표시) ───

def _ass_color(hex_color: str) -> str:
    r, g, b = _rgb(hex_color)
    return f"&H00{b:02X}{g:02X}{r:02X}"


def _ass_time(t: float) -> str:
    cs = max(0, int(round(t * 100)))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _esc(text: str) -> str:
    return text.replace("\\", "＼").replace("{", "(").replace("}", ")").replace("\n", " ")


def write_ass(ep: Episode, path: Path, caps: list[dict], chapters: list[dict], total: float,
              w: int, h: int, offset: float = 0.0, vertical: bool = False) -> None:
    family = _font(ep, 10).getname()[0]
    size = int(w * 0.062) if vertical else int(h * 0.052)
    margin_v = int(h * (0.22 if vertical else 0.07))
    styles = [
        f"Style: Sub,{family},{size},&H00FFFFFF,&H00FFFFFF,&H00101010,&H96000000,1,0,0,0,100,100,0,0,1,"
        f"{max(3, size // 14)},2,2,{int(w * 0.06)},{int(w * 0.06)},{margin_v},1",
        f"Style: Chapter,{family},{int(size * 0.62)},{_ass_color(ep.cfg['video'].get('accent', '#FFD166'))},"
        f"&H00FFFFFF,&H00101010,&H00000000,1,0,0,0,100,100,0,0,1,2,0,9,{int(w * 0.04)},{int(w * 0.04)},"
        f"{int(h * 0.05)},1",
    ]
    colors = {hh["id"]: _ass_color(hh.get("color", "#FFFFFF")) for hh in ep.hosts}
    names = {hh["id"]: hh["name"] for hh in ep.hosts}
    events = []
    for c in caps:
        s, e = c["start"] - offset, c["end"] - offset
        if e <= 0:
            continue
        name = "{\\fscx80\\fscy80\\c" + colors[c["speaker"]] + "&}" + _esc(names[c["speaker"]]) + "{\\r}\\N"
        events.append(f"Dialogue: 0,{_ass_time(s)},{_ass_time(e)},Sub,,0,0,0,,{name}{_esc(c['text'])}")
    if not vertical:
        for i, ch in enumerate(chapters):
            end = chapters[i + 1]["start"] if i + 1 < len(chapters) else total
            events.append(f"Dialogue: 1,{_ass_time(ch['start'])},{_ass_time(end)},Chapter,,0,0,0,,{_esc(ch['title'])}")
    path.write_text(
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {w}\nPlayResY: {h}\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n" + "\n".join(styles) +
        "\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n" +
        "\n".join(events) + "\n",
        encoding="utf-8",
    )


def _video(ep: Episode, bg: Path, ass: Path, out: Path, w: int, h: int,
           start: float = 0.0, length: float | None = None, wave_y: float = 0.56) -> None:
    vc = ep.cfg["video"]
    fontsdir = Path(ep.font).parent.as_posix()
    wave_h = int(h * 0.14)
    if vc.get("waveform", True):
        accent = vc.get("accent", "#FFD166").lstrip("#")
        fc = (f"[1:a]aformat=channel_layouts=mono,showwaves=s={int(w * 0.8)}x{wave_h}:mode=cline:draw=full:"
              f"rate={vc.get('fps', 30)}:colors=0x{accent}@0.85,format=rgba[wv];"
              f"[0:v][wv]overlay=(W-w)/2:{int(h * wave_y)}:shortest=1,"
              f"ass=filename={ass.name}:fontsdir='{fontsdir}',format=yuv420p[v]")
    else:
        fc = f"[0:v]ass=filename={ass.name}:fontsdir='{fontsdir}',format=yuv420p[v]"
    trim = ["-ss", f"{start:.3f}", "-t", f"{length:.3f}"] if length else []
    run(["ffmpeg", "-y", "-loop", "1", "-framerate", vc.get("fps", 30), "-i", bg.name,
         *trim, "-i", "episode.mp3",
         "-filter_complex", fc, "-map", "[v]", "-map", "1:a",
         "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-crf", "22",
         "-r", vc.get("fps", 30), "-c:a", "aac", "-b:a", "192k", "-shortest",
         "-movflags", "+faststart", out.name], cwd=ep.build)


def render(ep: Episode, script: dict | None, tl: dict) -> dict:
    if Image is None:
        raise StudioError("pip install Pillow 가 필요합니다.")
    vc = ep.cfg["video"]
    w, h = vc.get("width", 1920), vc.get("height", 1080)
    title = (script or {}).get("title") or ep.ep["topic"]

    thumb = thumbnail(ep, script)
    bg = background(ep, title, w, h, ep.path("background.png"))
    ass = ep.path("subtitles.ass")
    write_ass(ep, ass, tl["captions"], tl["chapters"], tl["duration"], w, h)
    log("render", f"본편 영상 렌더링 중 ({tl['duration'] / 60:.1f}분 분량)...")
    video = ep.path("episode.mp4")
    _video(ep, bg, ass, video, w, h)
    result = {"video": str(video), "thumbnail": str(thumb), "shorts": []}

    if vc.get("shorts", True) and script and script.get("shorts"):
        sw, sh = 1080, 1920
        for i, s in enumerate(script["shorts"][:3]):
            lines = [x for x in tl["timeline"] if x["section"] == s["section"]
                     and s["start_line"] <= x["line"] <= s["end_line"]]
            if not lines:
                continue
            start = max(0.0, lines[0]["start"] - 0.3)
            length = min(59.0, lines[-1]["end"] + 0.5 - start)
            caps = [c for c in tl["captions"] if start <= c["start"] < start + length]
            sbg = background(ep, s["title"], sw, sh, ep.path(f"short{i + 1}_bg.png"), vertical=True)
            sass = ep.path(f"short{i + 1}.ass")
            write_ass(ep, sass, caps, [], length, sw, sh, offset=start, vertical=True)
            write_srt(ep.path(f"short{i + 1}.srt"), caps, offset=start)
            out = ep.path(f"short{i + 1}.mp4")
            log("render", f"쇼츠 {i + 1}: '{s['title']}' ({length:.0f}초)")
            _video(ep, sbg, sass, out, sw, sh, start=start, length=length, wave_y=0.42)
            result["shorts"].append({"video": str(out), "title": s["title"]})

    write_json(ep.path("render.json"), result)
    log("render", f"완료: {video.name}, 썸네일, 쇼츠 {len(result['shorts'])}개")
    return result
