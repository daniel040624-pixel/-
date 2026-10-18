"""5단계: 제목·설명·챕터를 만들고 유튜브에 업로드한다. (선택) 팟캐스트 RSS 피드도 갱신."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

from .common import ROOT, Episode, StudioError, log, read_json, timestamp, write_json

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.force-ssl"]


def uses_tts(ep: Episode) -> bool:
    return not ep.ep.get("recording")


def metadata(ep: Episode, script: dict | None, tl: dict) -> dict:
    ch = ep.cfg["channel"]
    script = script or {}
    title = (script.get("title") or ep.ep["topic"])[:100]
    parts = [script.get("summary") or ep.ep["topic"], ""]

    chapters = tl.get("chapters", [])
    # 유튜브 챕터 조건: 0:00 시작, 3개 이상, 각 10초 이상
    if len(chapters) >= 3 and all(b["start"] - a["start"] >= 10 for a, b in zip(chapters, chapters[1:])):
        parts += ["📌 챕터"] + [f"{timestamp(c['start'])} {c['title']}" for c in chapters] + [""]
    if script.get("takeaways"):
        parts += ["💡 오늘의 깨달음"] + [f"• {t}" for t in script["takeaways"]] + [""]
    if script.get("listener_questions"):
        parts += ["💬 댓글로 생각을 나눠 주세요"] + [f"• {q}" for q in script["listener_questions"]] + [""]
    parts.append(f"🎙 {ch['name']} — {ch.get('tagline', '')}")
    parts.append("출연: " + ", ".join(h["name"] for h in ep.hosts))
    if uses_tts(ep):
        parts.append("※ 이 에피소드의 음성은 AI 음성 합성(TTS)으로 제작되었습니다.")
    tags = list(dict.fromkeys((script.get("tags") or []) + ch.get("hashtags", [])))
    parts += ["", " ".join("#" + t.replace(" ", "") for t in tags[:5])]

    description = "\n".join(parts).replace("<", "‹").replace(">", "›")[:4900]
    tag_budget, kept = 480, []
    for t in tags:  # 유튜브 태그 총 길이 500자 제한
        if len(t) + 1 > tag_budget:
            break
        kept.append(t)
        tag_budget -= len(t) + 1
    meta = {"title": title, "description": description, "tags": kept,
            "duration": tl["duration"], "slug": ep.dir.name,
            "created": datetime.now(timezone.utc).isoformat()}
    write_json(ep.path("metadata.json"), meta)
    (ep.path("description.txt")).write_text(f"{title}\n\n{description}\n", encoding="utf-8")
    return meta


def youtube_client(cfg: dict):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as e:
        raise StudioError("pip install google-api-python-client google-auth-oauthlib 가 필요합니다.") from e
    up = cfg["upload"]
    secrets, token = ROOT / up["client_secrets"], ROOT / up["token_file"]
    creds = Credentials.from_authorized_user_file(str(token), SCOPES) if token.exists() else None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        if not secrets.exists():
            raise StudioError(f"{secrets} 가 없습니다. README 의 'YouTube 연결' 절차를 따라 OAuth 클라이언트 파일을 받으세요.")
        log("upload", "브라우저에서 유튜브 계정 로그인을 진행하세요 (최초 1회).")
        creds = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES).run_local_server(port=0)
        token.parent.mkdir(parents=True, exist_ok=True)
        token.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds)


def upload_video(yt, cfg: dict, path: Path, title: str, description: str, tags: list[str],
                 synthetic: bool, publish_at: datetime | None = None) -> str:
    """publish_at 을 주면 비공개로 올린 뒤 그 시각에 유튜브가 자동 공개한다(예약 업로드)."""
    from googleapiclient.http import MediaFileUpload
    up = cfg["upload"]
    status = {"privacyStatus": up.get("privacy", "private"),
              "selfDeclaredMadeForKids": up.get("made_for_kids", False),
              "containsSyntheticMedia": synthetic}  # AI 음성 사용 고지
    if publish_at:
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = {
        "snippet": {"title": title, "description": description, "tags": tags,
                    "categoryId": str(up.get("category_id", "27")),
                    "defaultLanguage": cfg["channel"].get("language", "ko"),
                    "defaultAudioLanguage": cfg["channel"].get("language", "ko")},
        "status": status,
    }
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(path), chunksize=8 * 1024 * 1024, resumable=True))
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            log("upload", f"{path.name} {int(status.progress() * 100)}%")
    return resp["id"]


def upload_captions(yt, cfg: dict, video_id: str, srt: Path) -> None:
    from googleapiclient.http import MediaFileUpload
    if not srt.exists() or srt.stat().st_size == 0:
        return
    yt.captions().insert(
        part="snippet",
        body={"snippet": {"videoId": video_id, "language": cfg["channel"].get("language", "ko"),
                          "name": "한국어", "isDraft": False}},
        media_body=MediaFileUpload(str(srt), mimetype="application/octet-stream"),
    ).execute()


def upload(ep: Episode, meta: dict, rendered: dict, force: bool = False) -> dict:
    state_path = ep.path("upload.json")
    state = read_json(state_path) if state_path.exists() and not force else {}
    up = ep.cfg["upload"]
    if not up.get("youtube", True):
        log("upload", "config.toml 에서 youtube 업로드가 꺼져 있습니다.")
        return state
    if state.get("video_id"):
        log("upload", f"이미 업로드됨: https://youtu.be/{state['video_id']} (다시 올리려면 --force)")
        return state

    yt = youtube_client(ep.cfg)
    from googleapiclient.http import MediaFileUpload
    vid = upload_video(yt, ep.cfg, Path(rendered["video"]), meta["title"], meta["description"], meta["tags"],
                       uses_tts(ep))
    state["video_id"] = vid
    write_json(state_path, state)
    log("upload", f"본편 업로드 완료: https://youtu.be/{vid} ({up.get('privacy', 'private')})")

    try:
        yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(rendered["thumbnail"])).execute()
    except Exception as e:  # 채널 인증 전에는 맞춤 썸네일이 막혀 있을 수 있음
        log("upload", f"썸네일 설정 실패(채널 전화번호 인증 필요할 수 있음): {e}")
    if up.get("upload_captions", True):
        try:
            upload_captions(yt, ep.cfg, vid, ep.path("subtitles.srt"))
        except Exception as e:
            log("upload", f"자막 업로드 실패: {e}")
    if up.get("playlist_id"):
        yt.playlistItems().insert(part="snippet", body={"snippet": {
            "playlistId": up["playlist_id"], "resourceId": {"kind": "youtube#video", "videoId": vid}}}).execute()

    state["shorts"] = []
    for i, s in enumerate(rendered.get("shorts", []), 1):
        desc = f"전체 에피소드 ▶ https://youtu.be/{vid}\n\n{meta['title']}\n\n#shorts " + \
               " ".join("#" + t.replace(" ", "") for t in meta["tags"][:3])
        sid = upload_video(yt, ep.cfg, Path(s["video"]), f"{s['title']} #shorts"[:100], desc, meta["tags"],
                           uses_tts(ep))
        state["shorts"].append(sid)
        write_json(state_path, state)
        if up.get("upload_captions", True):
            try:
                upload_captions(yt, ep.cfg, sid, ep.path(f"short{i}.srt"))
            except Exception as e:
                log("upload", f"쇼츠 자막 업로드 실패: {e}")
        log("upload", f"쇼츠 업로드 완료: https://youtube.com/shorts/{sid}")
    return state


def rss(cfg: dict) -> Path | None:
    """모든 에피소드의 episode.mp3 로 팟캐스트 RSS 피드를 만든다 (Spotify/Apple 등록용)."""
    rc = cfg.get("rss", {})
    if not rc.get("enabled"):
        return None
    if not rc.get("base_url"):
        raise StudioError("config.toml [rss] base_url 을 지정하세요 (mp3 를 올려둘 공개 주소).")
    ch = cfg["channel"]
    items = []
    for meta_path in sorted((ROOT / "episodes").glob("*/build/metadata.json"), reverse=True):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        mp3 = meta_path.parent / "episode.mp3"
        if not mp3.exists():
            continue
        url = f"{rc['base_url'].rstrip('/')}/{meta['slug']}.mp3"
        pub = format_datetime(datetime.fromisoformat(meta["created"]))
        items.append(
            f"<item><title>{escape(meta['title'])}</title>"
            f"<description>{escape(meta['description'])}</description>"
            f"<enclosure url=\"{escape(url)}\" length=\"{mp3.stat().st_size}\" type=\"audio/mpeg\"/>"
            f"<guid isPermaLink=\"false\">{escape(meta['slug'])}</guid><pubDate>{pub}</pubDate>"
            f"<itunes:duration>{int(meta['duration'])}</itunes:duration></item>")
    feed = ROOT / "feed.xml"
    feed.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        f"<channel><title>{escape(ch['name'])}</title><link>{escape(rc['base_url'])}</link>"
        f"<language>{ch.get('language', 'ko')}</language>"
        f"<description>{escape(ch.get('mission', '').strip())}</description>"
        f"<itunes:author>{escape(rc.get('author', ''))}</itunes:author>"
        f"<itunes:owner><itunes:email>{escape(rc.get('email', ''))}</itunes:email></itunes:owner>"
        f"<itunes:image href=\"{escape(rc.get('image_url', ''))}\"/>"
        f"<itunes:category text=\"{escape(rc.get('category', 'Society & Culture'))}\"/>"
        f"<itunes:explicit>false</itunes:explicit>{''.join(items)}</channel></rss>\n",
        encoding="utf-8")
    log("rss", f"{feed} 갱신 ({len(items)}개 에피소드). 각 build/episode.mp3 를 base_url 에 <slug>.mp3 로 올리세요.")
    return feed
