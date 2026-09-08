"""PostgreSQL 연결 관리 (asyncpg 커넥션 풀).

앱은 DDL 권한을 쓰지 않는다. 테이블은 운영자가 `schema.sql` 로 미리 만들어 둔다.
앱이 부팅하며 테이블을 만들면 파드가 여러 개로 뜰 때 서로 충돌하기 때문이다.

파드가 몇 개로 늘어나도 각 파드는 자기 커넥션 풀만 갖고,
진실은 전부 DB 한 곳에 있다(무상태성 원칙).
"""

from __future__ import annotations

import json
import logging

import asyncpg

from app import repository as repo
from app.config import Settings

logger = logging.getLogger("duty.db")

# 느린 질의가 커넥션을 붙잡고 놓지 않는 상황을 막는다(초).
_COMMAND_TIMEOUT = 10.0


async def _init_connection(conn: asyncpg.Connection) -> None:
    """새 커넥션마다 적용할 설정.

    asyncpg 는 jsonb 를 문자열로 돌려준다. 이력의 스냅샷을 매번 손으로 json.loads 하지 않도록
    커넥션 수준에서 dict 로 오가게 등록한다.
    """
    for type_name in ("json", "jsonb"):
        await conn.set_type_codec(
            type_name,
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )


class Database:
    """커넥션 풀의 수명주기를 담는다. 앱 기동 시 연결하고 종료 시 닫는다."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: asyncpg.Pool | None = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("DB 커넥션 풀이 아직 준비되지 않았습니다.")
        return self._pool

    async def connect(self) -> None:
        """커넥션 풀을 만든다.

        실패 시 예외 메시지에 접속 문자열이 섞여 나가지 않도록 원문을 다시 던지지 않고
        일반화된 메시지로 감싼다. 상세 원인은 로그로만 남긴다.
        """
        try:
            self._pool = await asyncpg.create_pool(
                dsn=self._settings.database_url,
                min_size=self._settings.db_pool_min,
                max_size=self._settings.db_pool_max,
                command_timeout=_COMMAND_TIMEOUT,
                init=_init_connection,
            )
        except Exception as exc:  # asyncpg 는 다양한 예외를 낸다
            logger.error("DB 연결 실패", exc_info=exc)
            raise RuntimeError("데이터베이스에 연결하지 못했습니다.") from None
        logger.info(
            "DB 커넥션 풀 준비 완료 (%s~%s)",
            self._settings.db_pool_min,
            self._settings.db_pool_max,
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.info("DB 커넥션 풀 종료")

    async def healthy(self) -> bool:
        """헬스체크용. 풀에서 커넥션을 하나 빌려 실제로 질의가 되는지 본다."""
        if self._pool is None:
            return False
        try:
            async with self._pool.acquire() as conn:
                await repo.ping(conn)
        except Exception as exc:
            logger.warning("헬스체크 실패: %s", type(exc).__name__)
            return False
        return True
