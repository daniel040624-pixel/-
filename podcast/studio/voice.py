"""2단계: 대본의 대사마다 출연자 목소리로 음성 파일을 만든다 (TTS)."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from .common import Episode, StudioError, log, run


def line_file(ep: Episode, si: int, li: int, speaker: str, text: str) -> Path:
    # 대사를 고치면 해시가 바뀌어 그 줄만 다시 합성된다
    h = hashlib.sha1(f"{speaker}|{text}".encode()).hexdigest()[:8]
    d = ep.build / "lines"
    d.mkdir(exist_ok=True)
    return d / f"{si:02d}_{li:03d}_{h}.mp3"


def jobs(ep: Episode, script: dict) -> list[tuple[Path, dict, str]]:
    out = []
    for si, sec in enumerate(script["sections"]):
        for li, line in enumerate(sec["lines"]):
            path = line_file(ep, si, li, line["speaker"], line["text"])
            out.append((path, ep.host(line["speaker"]), line["text"]))
    return out


async def _edge(todo, rate: str, workers: int = 4) -> None:
    try:
        import edge_tts
    except ImportError as e:
        raise StudioError("pip install edge-tts 가 필요합니다 (또는 config.toml [voice] provider = \"mock\").") from e
    sem = asyncio.Semaphore(workers)

    async def one(path: Path, host: dict, text: str) -> None:
        async with sem:
            tmp = path.with_suffix(".part")
            for attempt in range(3):
                try:
                    await edge_tts.Communicate(text, host["voice"], rate=rate).save(str(tmp))
                    tmp.rename(path)
                    return
                except Exception as e:  # 네트워크 오류 등은 재시도
                    if attempt == 2:
                        raise StudioError(f"TTS 실패 ({host['name']}): {text[:30]}... — {e}") from e
                    await asyncio.sleep(2 ** attempt)

    await asyncio.gather(*(one(*j) for j in todo))


def _mock(todo) -> None:
    """네트워크 없이 파이프라인을 시험하기 위한 가짜 음성 (글자 수에 비례한 길이의 낮은 톤)."""
    for path, host, text in todo:
        freq = 180 + (sum(map(ord, host["id"])) % 5) * 40
        dur = max(1.0, len(text) / 6.5)
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={dur:.2f}",
             "-af", "volume=0.05", "-ar", "48000", "-ac", "1", path])


def synthesize(ep: Episode, script: dict) -> list[Path]:
    all_jobs = jobs(ep, script)
    todo = [j for j in all_jobs if not j[0].exists()]
    provider = ep.cfg["voice"].get("provider", "edge")
    log("voice", f"{len(all_jobs)}개 대사 중 {len(todo)}개 합성 ({provider})")
    if todo:
        if provider == "edge":
            asyncio.run(_edge(todo, ep.cfg["voice"].get("rate", "+0%")))
        elif provider == "mock":
            _mock(todo)
        else:
            raise StudioError(f"알 수 없는 TTS provider: {provider}")
    return [j[0] for j in all_jobs]
