"""설정 로딩, 경로, ffmpeg 헬퍼 등 모든 단계가 공유하는 부분."""
from __future__ import annotations

import json
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/Library/Fonts/NanumGothic.ttf",
    "C:/Windows/Fonts/malgunbd.ttf",
    "C:/Windows/Fonts/malgun.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]


class StudioError(RuntimeError):
    pass


@dataclass
class Episode:
    dir: Path
    cfg: dict      # config.toml
    ep: dict       # episode.toml

    @property
    def build(self) -> Path:
        p = self.dir / "build"
        p.mkdir(exist_ok=True)
        return p

    def path(self, name: str) -> Path:
        return self.build / name

    @property
    def hosts(self) -> list[dict]:
        return self.cfg["hosts"]

    def host(self, host_id: str) -> dict:
        for h in self.hosts:
            if h["id"] == host_id:
                return h
        raise StudioError(f"config.toml 에 없는 출연자 id: {host_id}")

    def asset(self, name: str) -> Path | None:
        if not name:
            return None
        for base in (self.dir, ROOT / "assets"):
            if (base / name).exists():
                return base / name
        raise StudioError(f"에셋 파일을 찾을 수 없습니다: {name} (에피소드 폴더나 assets/ 에 두세요)")

    @property
    def font(self) -> str:
        configured = self.cfg["video"].get("font")
        if configured:
            return configured
        for f in FONT_CANDIDATES:
            if Path(f).exists():
                return f
        raise StudioError(
            "한글 폰트를 찾지 못했습니다. config.toml [video] font 에 경로를 지정하거나 "
            "'sudo apt install fonts-noto-cjk' 로 설치하세요."
        )


def load_config(path: Path | None = None) -> dict:
    with open(path or ROOT / "config.toml", "rb") as f:
        return tomllib.load(f)


def load_episode(ep_dir: str | Path, config_path: Path | None = None) -> Episode:
    ep_dir = Path(ep_dir).resolve()
    toml = ep_dir / "episode.toml"
    if not toml.exists():
        raise StudioError(f"{toml} 가 없습니다. 'python -m studio new \"주제\"' 로 만드세요.")
    with open(toml, "rb") as f:
        ep = tomllib.load(f)
    return Episode(ep_dir, load_config(config_path), ep)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def require(tool: str) -> None:
    if not shutil.which(tool):
        raise StudioError(f"'{tool}' 이(가) 필요합니다. ffmpeg 를 설치하세요 (https://ffmpeg.org).")


def run(cmd: list, quiet: bool = True, cwd: Path | None = None) -> None:
    require(str(cmd[0]))
    proc = subprocess.run([str(c) for c in cmd], capture_output=quiet, text=True, cwd=cwd)
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-2000:]
        raise StudioError(f"명령 실패: {' '.join(map(str, cmd[:6]))} ...\n{tail}")


def duration(path: Path) -> float:
    require("ffprobe")
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def timestamp(sec: float) -> str:
    """유튜브 챕터용 0:00 / 1:02:03 형식."""
    s = int(sec)
    h, m, s = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def log(stage: str, msg: str) -> None:
    print(f"[{stage}] {msg}", flush=True)
