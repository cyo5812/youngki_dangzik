"""당직 일정표 — FastAPI 앱.

HTTP 경계만 담당한다. 업무 규칙은 services 에, SQL 은 repository 에 있다.

한 프로세스가 화면(정적 파일)과 API 를 함께 서빙한다 — 도구 하나 = 컨테이너 하나 원칙.
"""

from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

import asyncpg
from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import Settings, load_settings
from app.db import Database
from app.errors import (
    ProblemError,
    handle_problem,
    handle_unexpected,
    handle_validation,
    problem_response,
)
from app.models import (
    DutyDayOut,
    DutyUpsert,
    HistoryOut,
    ImportResultOut,
    RevertResultOut,
    ScheduleOut,
)
from app.services import excel as excel_service
from app.services import schedule as schedule_service

logger = logging.getLogger("duty.main")

STATIC_DIR = Path(__file__).parent / "static"

# 브라우저에게 이 페이지의 안전 규칙을 알려 준다.
# 외부 리소스를 전혀 쓰지 않으므로 'self' 만으로 충분하다 — 스크립트가 주입돼도 실행될 자리가 없다.
_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 기동·종료 시 한 번씩 실행된다."""
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger.info("설정 로드 완료: %s", settings.describe())

    database = Database(settings)
    await database.connect()

    app.state.settings = settings
    app.state.database = database
    try:
        yield
    finally:
        await database.close()


app = FastAPI(
    title="당직 일정표",
    description="영업총괄 3팀 주말·휴일 순환 당직과 영기팀 평일 당직 일정표",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,      # 사내 도구에 API 문서 UI 를 노출하지 않는다
    redoc_url=None,
    openapi_url=None,
)


# --------------------------------------------------------------------------
# 공통 미들웨어 · 예외 처리
# --------------------------------------------------------------------------


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response: Response = await call_next(request)
    for name, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


@app.exception_handler(ProblemError)
async def _problem_handler(request: Request, exc: ProblemError) -> JSONResponse:
    return await handle_problem(request, exc)


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return await handle_validation(request, exc)


@app.exception_handler(StarletteHTTPException)
async def _http_handler(_: Request, exc: StarletteHTTPException) -> Response:
    """기본 HTTP 오류도 problem+json 으로 통일한다."""
    titles = {404: "찾을 수 없음", 405: "허용되지 않은 방식", 413: "요청이 너무 큼"}
    return problem_response(
        exc.status_code,
        titles.get(exc.status_code, "요청 오류"),
        "요청을 처리할 수 없습니다.",
    )


async def _db_unavailable_handler(_: Request, exc: Exception) -> JSONResponse:
    """DB 를 쓸 수 없는 상황. 드라이버 메시지는 로그에만 남기고 일반화된 문장만 준다.

    잡아야 할 범위가 셋이다. 하나라도 빠뜨리면 그 경우만 500 으로 새어 나간다.
      · PostgresError  — 서버가 보고한 오류
      · InterfaceError — 커넥션이 끊긴 상태 (서버가 내려간 경우가 여기다)
      · OSError        — 소켓 자체를 열지 못한 경우(Connection refused)
    """
    logger.error("DB 사용 불가", exc_info=exc)
    return problem_response(
        503, "일시적 오류", "일시적으로 처리할 수 없습니다. 잠시 후 다시 시도해 주세요."
    )


async def _db_constraint_handler(_: Request, exc: Exception) -> JSONResponse:
    """DB 제약 위반. 검증을 통과한 값이 DB 규칙에 걸린 경우이므로 입력 오류로 돌려준다.

    503(일시적 오류)으로 뭉뚱그리면 사용자가 재시도만 반복하게 된다 — 다시 해도 똑같이 실패한다.
    """
    logger.warning("DB 제약 위반: %s", type(exc).__name__)
    return problem_response(400, "입력값 오류", "저장할 수 없는 값이 있습니다. 입력값을 확인해 주세요.")


# 구체적인 것부터 등록한다. 제약 위반은 PostgresError 의 하위 유형이다.
app.add_exception_handler(
    asyncpg.exceptions.IntegrityConstraintViolationError, _db_constraint_handler
)
for _db_exc in (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError):
    app.add_exception_handler(_db_exc, _db_unavailable_handler)


@app.exception_handler(Exception)
async def _unexpected_handler(request: Request, exc: Exception) -> JSONResponse:
    return await handle_unexpected(request, exc)


# --------------------------------------------------------------------------
# 의존성
# --------------------------------------------------------------------------


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_database(request: Request) -> Database:
    return request.app.state.database


async def require_admin(request: Request) -> None:
    """수정 계열 API 접근 통제.

    `ADMIN_KEY` 가 주입되지 않았으면 통과시킨다(현 정책: 사내망 사용자 누구나 수정).
    운영자가 나중에 Secret 으로 키를 넣는 것만으로 잠글 수 있다.
    """
    settings: Settings = request.app.state.settings
    if not settings.admin_lock_enabled:
        return

    # 타이밍 공격을 피하려고 길이·내용에 관계없이 일정 시간이 걸리는 비교를 쓴다.
    #
    # 비교는 **반드시 바이트로** 한다. 이유가 둘이다.
    #  1) hmac.compare_digest 는 비 ASCII 문자열을 받으면 TypeError 를 낸다.
    #     한글이 섞인 키를 주입하면 수정 기능 전체가 500 으로 죽는다.
    #  2) HTTP 헤더는 latin-1 로 디코딩된다. 키가 UTF-8 이면 문자열끼리 비교했을 때
    #     같은 값인데도 어긋난다. latin-1 로 되돌려 클라이언트가 보낸 원래 바이트를 복원한다.
    raw = request.headers.get("x-admin-key", "")
    try:
        provided = raw.encode("latin-1")
    except UnicodeEncodeError:  # 정상 경로에서는 발생하지 않는다
        provided = b""
    expected = settings.admin_key.encode("utf-8")
    if not hmac.compare_digest(provided, expected):
        raise ProblemError(401, "권한 없음", "관리자 키가 필요합니다.")


async def _read_body_limited(request: Request, limit: int) -> bytes:
    """요청 본문을 상한까지만 메모리로 읽는다.

    UploadFile(multipart)을 쓰지 않는 이유: Starlette 이 1MB 를 넘는 업로드를
    임시파일로 디스크에 흘린다. 읽기 전용 파일시스템·무상태성 원칙과 충돌한다.
    """
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise ProblemError(413, "파일이 너무 큼", f"최대 {limit // 1024 // 1024}MB 까지 올릴 수 있습니다.")

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            # 상한을 넘는 순간 읽기를 멈춘다 — 끝까지 받아 놓고 거절하지 않는다.
            raise ProblemError(413, "파일이 너무 큼", f"최대 {limit // 1024 // 1024}MB 까지 올릴 수 있습니다.")
        chunks.append(chunk)

    if not chunks:
        raise ProblemError(400, "입력값 오류", "업로드된 파일이 없습니다.")
    return b"".join(chunks)


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


@app.get("/api/health")
async def health(database: Database = Depends(get_database)) -> Response:
    """헬스체크. readinessProbe 가 부른다. DB 까지 실제로 확인한다."""
    if not await database.healthy():
        return problem_response(503, "서비스 준비 안 됨", "일시적으로 조회할 수 없습니다.")
    return JSONResponse({"status": "ok", "db": "ok"})


@app.get("/api/config")
async def get_config(settings: Settings = Depends(get_settings)) -> JSONResponse:
    """화면이 알아야 할 서버 설정. **비밀값은 담지 않는다** — 잠금 여부만 알린다."""
    return JSONResponse({"adminLockEnabled": settings.admin_lock_enabled})


@app.get("/api/schedule", response_model=ScheduleOut)
async def get_schedule(
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    database: Database = Depends(get_database),
) -> ScheduleOut:
    """일정 조회. 범위를 주지 않으면 전체."""
    async with database.pool.acquire() as conn:
        return await schedule_service.get_schedule(conn, date_from, date_to)


@app.put("/api/schedule/{duty_date}", response_model=DutyDayOut, dependencies=[Depends(require_admin)])
async def put_day(
    duty_date: date,
    payload: DutyUpsert,
    database: Database = Depends(get_database),
) -> DutyDayOut:
    """하루치 등록·수정."""
    async with database.pool.acquire() as conn:
        return await schedule_service.save_day(conn, duty_date, payload)


@app.delete("/api/schedule/{duty_date}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_day(
    duty_date: date,
    database: Database = Depends(get_database),
) -> Response:
    """하루치 비우기. 없던 날짜여도 204(멱등)."""
    async with database.pool.acquire() as conn:
        await schedule_service.clear_day(conn, duty_date)
    return Response(status_code=204)


@app.post("/api/schedule/import", response_model=ImportResultOut, dependencies=[Depends(require_admin)])
async def import_excel(
    request: Request,
    settings: Settings = Depends(get_settings),
    database: Database = Depends(get_database),
) -> ImportResultOut:
    """당직표 엑셀(.xlsx) 본문을 그대로 받아 전체 일정을 교체한다.

    파싱이 먼저, DB 변경이 나중이다. 형식 오류로 기존 데이터가 상하지 않는다.
    """
    content = await _read_body_limited(request, settings.max_upload_bytes)
    rows = excel_service.parse_duty_workbook(content)  # 실패하면 여기서 끝 — DB 는 그대로

    async with database.pool.acquire() as conn:
        replaced, snapshot_id = await schedule_service.replace_schedule(conn, rows)

    logger.info("엑셀 일괄 교체 %s건 (스냅샷 #%s)", replaced, snapshot_id)
    return ImportResultOut(
        replaced=replaced,
        snapshotId=snapshot_id,
        message=f"{replaced}건으로 교체했습니다. 되돌리려면 변경 이력에서 #{snapshot_id}을 복원하세요.",
    )


@app.get("/api/history", response_model=HistoryOut)
async def get_history(
    limit: int = Query(50, ge=1, le=schedule_service.MAX_HISTORY_LIMIT),
    database: Database = Depends(get_database),
) -> HistoryOut:
    """변경 이력 조회."""
    async with database.pool.acquire() as conn:
        return await schedule_service.get_history(conn, limit)


@app.post("/api/history/{change_id}/revert", response_model=RevertResultOut, dependencies=[Depends(require_admin)])
async def revert_change(
    change_id: int,
    database: Database = Depends(get_database),
) -> RevertResultOut:
    """해당 이력의 '변경 전 상태'로 되돌린다."""
    if change_id <= 0:
        raise ProblemError(400, "입력값 오류", "이력 번호가 올바르지 않습니다.")
    async with database.pool.acquire() as conn:
        restored = await schedule_service.revert(conn, change_id)
    return RevertResultOut(restored=restored, message=f"{restored}건을 복원했습니다.")


# --------------------------------------------------------------------------
# 화면 (정적 파일)
#   API 경로를 먼저 등록한 뒤 마지막에 붙여야 /api/* 가 가려지지 않는다.
# --------------------------------------------------------------------------

app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
