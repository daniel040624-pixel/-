"""1단계: 주제와 '나의 생각'으로 토론형 팟캐스트 대본을 만든다 (Claude API)."""
from __future__ import annotations

import json

from .common import Episode, StudioError, log, write_json

SYSTEM = """당신은 한국어 토론형 팟캐스트의 수석 작가입니다.

채널: {name} — {tagline}
채널의 사명:
{mission}

출연자:
{hosts}

대본 원칙
- 채널 주인({owner})의 생각이 에피소드의 중심입니다. 그 관점을 1인칭으로, 구체적인 근거와 경험으로 말하게 하세요.
- 반대 관점은 허수아비가 아니라 가장 강한 형태로 제시합니다. 진짜 토론처럼 서로 반박하고, 양보할 것은 양보하세요.
- 구조: 1) 훅(첫 15초 안에 듣는 사람을 붙잡는 질문이나 장면) 2) 배경과 쟁점 정리 3) 나의 주장 4) 반론 5) 맞부딪히는 토론 6) 깨달음 — 토론 끝에 처음보다 한 단계 깊어진 결론 7) 오늘의 실천 한 가지와 청취자에게 던지는 질문.
- 귀로 듣는 글입니다. 실제 사람이 말하는 자연스러운 구어체로, 한 대사는 1~3문장(대략 150자 이내)으로 짧게 주고받으세요.
- 무대 지시문, 괄호, 이모지, 마크다운, 영어 약어 남발을 쓰지 마세요. 숫자와 기호는 읽는 그대로 적으세요(예: 10% → 10퍼센트).
- 확인되지 않은 통계나 인용을 지어내지 마세요. 확신이 없으면 '~라는 이야기도 있죠'처럼 단정하지 않게 말하세요.
- shorts 에는 맥락 없이 들어도 흥미로운 30~55초 분량의 연속 구간을 1~3개 고르세요(섹션 번호와 그 섹션 안의 대사 번호, 0부터)."""

USER = """이번 에피소드를 써 주세요.

주제: {topic}

나의 생각:
{my_view}

꼭 다룰 논점: {points}
청취자가 가져갔으면 하는 것: {takeaway_goal}
톤: {tone}
목표 길이: 약 {minutes}분 (전체 대사 합계 약 {chars}자)"""


def schema(host_ids: list[str]) -> dict:
    def obj(props: dict) -> dict:
        return {"type": "object", "properties": props,
                "required": list(props), "additionalProperties": False}

    s = {"type": "string"}
    strings = {"type": "array", "items": s}
    i = {"type": "integer"}
    return obj({
        "title": s,                 # 유튜브 제목 (60자 이내)
        "thumbnail_text": s,        # 썸네일 큰 글씨 (15자 이내)
        "summary": s,               # 설명란 요약 2~3문장
        "tags": strings,
        "sections": {"type": "array", "items": obj({
            "title": s,
            "lines": {"type": "array", "items": obj({
                "speaker": {"type": "string", "enum": host_ids},
                "text": s,
            })},
        })},
        "takeaways": strings,
        "listener_questions": strings,
        "shorts": {"type": "array", "items": obj({
            "title": s, "section": i, "start_line": i, "end_line": i,
        })},
    })


def generate(ep: Episode, force: bool = False) -> dict:
    out = ep.path("script.json")
    if out.exists() and not force:
        log("script", f"기존 대본 사용: {out} (다시 만들려면 --force)")
        return json.loads(out.read_text(encoding="utf-8"))

    try:
        import anthropic
    except ImportError as e:
        raise StudioError("pip install -r requirements.txt 로 anthropic 패키지를 설치하세요.") from e

    cfg, e = ep.cfg, ep.ep
    ch, sc = cfg["channel"], cfg["script"]
    hosts = "\n".join(f"- {h['name']} (id: {h['id']}): {h['role']}" for h in ep.hosts)
    minutes = e.get("minutes", 12)
    system = SYSTEM.format(name=ch["name"], tagline=ch.get("tagline", ""),
                           mission=ch.get("mission", "").strip(), hosts=hosts,
                           owner=ep.hosts[0]["name"])
    user = USER.format(topic=e["topic"], my_view=e.get("my_view", "").strip() or "(아직 정리 안 됨 — 토론하며 찾아가기)",
                       points=" / ".join(e.get("points", [])) or "자유",
                       takeaway_goal=e.get("takeaway_goal", "자유"),
                       tone=e.get("tone", "진지하지만 따뜻하게"),
                       minutes=minutes, chars=int(minutes * sc.get("chars_per_minute", 330)))

    log("script", f"Claude({sc['model']})로 대본 작성 중... (수 분 걸릴 수 있음)")
    client = anthropic.Anthropic()
    with client.beta.messages.stream(
        model=sc["model"],
        max_tokens=64000,
        system=system,
        messages=[{"role": "user", "content": user}],
        thinking={"type": "adaptive"},
        output_config={
            "effort": sc.get("effort", "high"),
            "format": {"type": "json_schema", "schema": schema([h["id"] for h in ep.hosts])},
        },
        # 안전 분류기가 거절하면 서버에서 대체 모델로 자동 재시도
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    ) as stream:
        msg = stream.get_final_message()

    if msg.stop_reason == "refusal":
        raise StudioError("모델이 이 주제의 대본 작성을 거절했습니다. 주제나 표현을 조정해 보세요.")
    if msg.stop_reason == "max_tokens":
        raise StudioError("대본이 너무 길어 잘렸습니다. episode.toml 의 minutes 를 줄여 보세요.")

    text = next(b.text for b in msg.content if b.type == "text")
    data = json.loads(text)
    write_json(out, data)
    n = sum(len(s["lines"]) for s in data["sections"])
    log("script", f"완료: '{data['title']}' — {len(data['sections'])}개 섹션, {n}개 대사 → {out}")
    log("script", "대본을 직접 고친 뒤 다음 단계를 실행해도 됩니다.")
    return data
