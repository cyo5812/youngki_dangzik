"""컨테이너 진입점.

`python -m app` 으로 실행한다.

uvicorn 명령을 직접 CMD 에 박지 않는 이유: 그렇게 하면 포트를 Dockerfile 에 하드코딩하게 되고,
`APP_PORT` 환경변수는 설정에만 존재하고 실제로는 쓰이지 않는 거짓말이 된다.
여기서 설정을 읽어 넘기면 운영자가 포트를 바꿔야 할 때 이미지를 다시 만들지 않아도 된다.
"""

from __future__ import annotations

import sys

import uvicorn

from app.config import ConfigError, load_settings


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        # 설정이 빠진 채 뜨면 첫 요청에서야 실패한다. 아예 기동을 막아 운영자가 즉시 알아채게 한다.
        print(f"[기동 중단] {exc}", file=sys.stderr)
        return 1

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",  # noqa: S104 — 컨테이너 안에서만 열리고 노출은 서비스가 통제한다
        port=settings.port,
        log_level=settings.log_level.lower(),
        access_log=False,  # 접근 로그에 경로·IP 가 남지 않게 한다(개인정보 최소화)
        server_header=False,  # 서버 종류·버전을 응답 헤더로 알리지 않는다
        date_header=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
