"""데이터 접근 계층 — SQL 은 이 파일에만 존재한다.

**SQL 문자열 결합을 금지한다.** 값은 전부 asyncpg 파라미터 바인딩($1, $2 …)으로 넘긴다.
인젝션 점검 범위를 한 파일로 좁히기 위한 배치이므로, 다른 모듈에서 SQL 을 쓰지 않는다.

이 계층은 업무 규칙을 판단하지 않는다. 무엇을 저장할지는 services 가 정하고,
여기서는 '어떻게 저장하는가'만 다룬다.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Sequence

import asyncpg

# 조회 시 항상 같은 컬럼 순서를 쓰기 위한 공통 조각
_DUTY_COLUMNS = "duty_date, day_type, org, person, note"


# --------------------------------------------------------------------------
# 당직 일정 (duty_day)
# --------------------------------------------------------------------------


async def fetch_days(
    conn: asyncpg.Connection,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[asyncpg.Record]:
    """기간 내 당직 일정을 날짜순으로 읽는다. 범위를 주지 않으면 전체."""
    return await conn.fetch(
        f"""
        SELECT {_DUTY_COLUMNS}
          FROM duty_day
         WHERE ($1::date IS NULL OR duty_date >= $1)
           AND ($2::date IS NULL OR duty_date <= $2)
         ORDER BY duty_date
        """,
        date_from,
        date_to,
    )


async def fetch_day(conn: asyncpg.Connection, duty_date: date) -> asyncpg.Record | None:
    """하루치를 읽는다. 없으면 None."""
    return await conn.fetchrow(
        f"SELECT {_DUTY_COLUMNS} FROM duty_day WHERE duty_date = $1",
        duty_date,
    )


async def upsert_day(
    conn: asyncpg.Connection,
    duty_date: date,
    day_type: str,
    org: str | None,
    person: str,
    note: str,
) -> asyncpg.Record:
    """하루치를 등록하거나 수정한다(있으면 갱신).

    날짜가 기본키라 같은 날짜로 두 행이 생길 수 없다 — 동시에 저장이 들어와도
    DB가 하나로 합쳐 주므로 애플리케이션에서 잠금을 걸 필요가 없다.
    """
    return await conn.fetchrow(
        f"""
        INSERT INTO duty_day (duty_date, day_type, org, person, note, updated_at)
        VALUES ($1, $2, $3, $4, $5, now())
        ON CONFLICT (duty_date) DO UPDATE
           SET day_type   = EXCLUDED.day_type,
               org        = EXCLUDED.org,
               person     = EXCLUDED.person,
               note       = EXCLUDED.note,
               updated_at = now()
        RETURNING {_DUTY_COLUMNS}
        """,
        duty_date,
        day_type,
        org,
        person,
        note,
    )


async def delete_day(conn: asyncpg.Connection, duty_date: date) -> asyncpg.Record | None:
    """하루치를 지운다. 없던 날짜면 None 을 돌려준다(멱등)."""
    return await conn.fetchrow(
        f"DELETE FROM duty_day WHERE duty_date = $1 RETURNING {_DUTY_COLUMNS}",
        duty_date,
    )


async def replace_all_days(
    conn: asyncpg.Connection,
    rows: Sequence[tuple[date, str, str | None, str, str]],
) -> int:
    """전체를 새 목록으로 갈아끼운다.

    호출자가 **반드시 트랜잭션 안에서** 부르고, 부르기 전에 기존 상태를 이력에 남겨야 한다.
    그래야 잘못된 교체를 되돌릴 수 있다.
    """
    await conn.execute("DELETE FROM duty_day")
    await conn.executemany(
        """
        INSERT INTO duty_day (duty_date, day_type, org, person, note, updated_at)
        VALUES ($1, $2, $3, $4, $5, now())
        """,
        rows,
    )
    return len(rows)


async def max_updated_at(conn: asyncpg.Connection) -> Any:
    """가장 최근 수정 시각. 화면의 '최종 갱신' 표시에 쓴다."""
    return await conn.fetchval("SELECT max(updated_at) FROM duty_day")


# --------------------------------------------------------------------------
# 변경 이력 (duty_change_log)
# --------------------------------------------------------------------------


async def insert_change(
    conn: asyncpg.Connection,
    action: str,
    duty_date: date | None,
    before_data: Any,
    after_data: Any,
    summary: str,
) -> int:
    """변경 이력을 남기고 이력 번호를 돌려준다."""
    return await conn.fetchval(
        """
        INSERT INTO duty_change_log (action, duty_date, before_data, after_data, summary)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id
        """,
        action,
        duty_date,
        before_data,
        after_data,
        summary,
    )


async def fetch_changes(conn: asyncpg.Connection, limit: int) -> list[asyncpg.Record]:
    """최신 변경 이력을 읽는다. before_data 가 있으면 되돌릴 수 있다."""
    return await conn.fetch(
        """
        SELECT id,
               changed_at,
               action,
               duty_date,
               summary,
               (before_data IS NOT NULL) AS revertable
          FROM duty_change_log
         ORDER BY changed_at DESC, id DESC
         LIMIT $1
        """,
        limit,
    )


async def fetch_change(conn: asyncpg.Connection, change_id: int) -> asyncpg.Record | None:
    """되돌리기에 쓸 이력 한 건을 읽는다."""
    return await conn.fetchrow(
        """
        SELECT id, changed_at, action, duty_date, before_data, after_data, summary
          FROM duty_change_log
         WHERE id = $1
        """,
        change_id,
    )
