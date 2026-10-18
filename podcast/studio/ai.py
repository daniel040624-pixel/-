"""Claude 호출 공통 헬퍼: JSON 스키마에 맞는 결과를 받아온다."""
from __future__ import annotations

import json
import os

from .common import StudioError


def available() -> bool:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


STR = {"type": "string"}
INT = {"type": "integer"}
STRS = {"type": "array", "items": STR}


def ask_json(cfg: dict, system: str, user: str, schema: dict, effort: str | None = None) -> dict:
    import anthropic

    sc = cfg["script"]
    client = anthropic.Anthropic()
    with client.beta.messages.stream(
        model=sc["model"],
        max_tokens=32000,
        system=system,
        messages=[{"role": "user", "content": user}],
        thinking={"type": "adaptive"},
        output_config={"effort": effort or sc.get("effort", "high"),
                       "format": {"type": "json_schema", "schema": schema}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise StudioError("모델이 요청을 거절했습니다. 주제나 표현을 조정해 보세요.")
    if msg.stop_reason == "max_tokens":
        raise StudioError("응답이 너무 길어 잘렸습니다.")
    return json.loads(next(b.text for b in msg.content if b.type == "text"))
