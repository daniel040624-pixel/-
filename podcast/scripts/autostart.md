# watch 를 항상 켜두는 방법

컴퓨터가 켜져 있는 동안 `inbox/` 를 감시하게 하면, 휴대폰 영상을 그 폴더에 넣기만 하면 됩니다.
(Google Drive·iCloud·Dropbox 동기화 폴더를 `config.toml [footage] inbox` 로 지정하면 휴대폰에서 바로 넣을 수 있습니다.)

## macOS / Linux — 5분마다 한 번 처리 (cron)

```bash
crontab -e
# 아래 한 줄 추가 (경로는 내 환경에 맞게)
*/5 * * * * cd /path/to/podcast && /path/to/podcast/.venv/bin/python -m studio watch --once >> watch.log 2>&1
```

> `--once` 는 지금 inbox 에 있는 파일만 처리하고 끝납니다. 복사가 덜 끝난 파일을 피하려면
> 휴대폰에서 다 옮긴 뒤 넣거나, 계속 켜두는 방식(아래)을 쓰세요.

## 계속 켜두기 (터미널 하나)

```bash
cd podcast && source .venv/bin/activate
python -m studio watch            # 시간표대로 예약
python -m studio watch --mode now # 넣는 즉시 공개
```

## Windows — 작업 스케줄러

1. 작업 스케줄러 → 기본 작업 만들기 → 트리거 "로그온할 때"
2. 동작: 프로그램 시작 → `C:\path\to\podcast\.venv\Scripts\python.exe`
3. 인수: `-m studio watch`, 시작 위치: `C:\path\to\podcast`
