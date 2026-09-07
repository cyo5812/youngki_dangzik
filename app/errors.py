"""오류 응답 — RFC 7807 (application/problem+json).

원칙: 사용자에게는 **대응할 수 있는 한국어 문장 하나**만 준다.
스택 트레이스·SQL·드라이버 메시지·환경변수 값은 응답에 절대 담지 않고 서버 로그에만 남긴다.
공격자에게 내부 구조를 알려주지 않기 위해서다.
"""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("duty.errors")

PROBLEM_MEDIA_TYPE = "application/problem+json"


class ProblemError(Exception):
    """업무 로직에서 의도적으로 발생시키는 오류.

    여기서 발생한 detail 은 사용자에게 그대로 보인다.
    따라서 detail 에 내부 식별자·경로·비밀값을 넣지 않는다.
    """

    def __init__(self, status: int, title: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.title = title
        self.detail = detail


def problem_response(status: int, title: str, detail: str) -> JSONResponse:
    """RFC 7807 형식의 응답을 만든다."""
    return JSONResponse(
        status_code=status,
        media_type=PROBLEM_MEDIA_TYPE,
        content={
            "type": "about:blank",
            "title": title,
            "status": status,
            "detail": detail,
        },
    )


async def handle_problem(_: Request, exc: ProblemError) -> JSONResponse:
    """의도된 업무 오류. 원인이 명확하므로 로그는 경고 수준으로만 남긴다."""
    logger.warning("업무 오류 %s %s: %s", exc.status, exc.title, exc.detail)
    return problem_response(exc.status, exc.title, exc.detail)


async def handle_validation(_: Request, exc: Exception) -> JSONResponse:
    """Pydantic 검증 실패.

    어느 필드가 왜 틀렸는지의 상세 구조는 내부 스키마를 드러내므로 응답에 싣지 않는다.
    사용자에게는 '입력값을 확인하라'는 사실만 전하고, 상세는 로그로 넘긴다.
    """
    logger.warning("입력 검증 실패: %s", exc)
    return problem_response(400, "입력값 오류", "입력값을 확인해 주세요.")


async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
    """예상하지 못한 예외.

    exc_info 로 스택은 로그에만 남기고, 사용자에게는 일반화된 문장만 준다.
    """
    logger.error("처리되지 않은 예외", exc_info=exc)
    return problem_response(
        500, "서버 오류", "요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요."
    )
