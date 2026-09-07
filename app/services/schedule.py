"""당직 일정 업무 규칙.

HTTP 도 SQL 세부사항도 모른다. '무엇을 저장할지'만 정하고, '어떻게 저장할지'는 repository 에 맡긴다.

이 계층의 핵심 약속: **데이터를 바꾸기 전에 반드시 바꾸기 전 상태를 이력에 남긴다.**
인증이 없는 도구이므로, 잘못된 변경을 막는 대신 되돌릴 수 있게 하는 것이 방어선이다.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable, Sequence

import asyncpg

from app import repository as repo
from app.errors import ProblemError
from app.models import (
    DayType,
    DutyDayOut,
    DutyUpsert,
    HistoryItemOut,
    HistoryOut,
    ScheduleMeta,
    ScheduleOut,
    clean_text,
)

# 조회 가능한 최대 기간. 실수나 장난으로 수십 년치를 긁어가는 것을 막는다.
MAX_RANGE_DAYS = 400
# 변경 이력 조회 상한
MAX_HISTORY_LIMIT = 200

VALID_DAY_TYPES = {"평일", "휴일"}
VALID_ORGS = {"영기", "소강", "도강", "전산휴무"}

# 스냅샷 적용 범위
SCOPE_DAY = "day"
SCOPE_ALL = "all"

DutyRow = tuple[date, str, str | None, str, str]


# --------------------------------------------------------------------------
# 스냅샷 변환 — 이력에 남기고 되돌릴 때 쓰는 공통 형식
# --------------------------------------------------------------------------


def _row_to_snapshot(row: asyncpg.Record) -> dict[str, Any]:
    """DB 행을 JSON 으로 저장 가능한 형태로 바꾼다."""
    return {
        "date": row["duty_date"].isoformat(),
        "dayType": row["day_type"],
        "org": row["org"],
        "person": row["person"],
        "note": row["note"],
    }


def _snapshot(scope: str, rows: Iterable[asyncpg.Record]) -> dict[str, Any]:
    """이력에 넣을 스냅샷을 만든다.

    범위를 함께 적어 두는 이유: 되돌릴 때 '이 날짜만 복원'인지 '전체 복원'인지를
    이력만 보고 판단할 수 있어야 하기 때문이다.
    """
    return {"scope": scope, "rows": [_row_to_snapshot(r) for r in rows]}


def _snapshot_to_rows(snapshot: dict[str, Any]) -> list[DutyRow]:
    """스냅샷을 DB 에 다시 넣을 수 있는 튜플 목록으로 바꾼다.

    우리가 만든 데이터지만 그대로 믿지 않는다 — 저장 시점 이후 스키마가 바뀌었을 수도 있고,
    잘못된 값이 그대로 되살아나면 되돌리기가 오히려 사고가 된다.
    """
    rows: list[DutyRow] = []
    for item in snapshot.get("rows", []):
        try:
            duty_date = date.fromisoformat(str(item["date"]))
            day_type = str(item["dayType"])
        except (KeyError, TypeError, ValueError):
            raise ProblemError(422, "되돌리기 실패", "이력의 형식이 올바르지 않아 되돌릴 수 없습니다.") from None

        if day_type not in VALID_DAY_TYPES:
            raise ProblemError(422, "되돌리기 실패", "이력에 알 수 없는 구분 값이 있어 되돌릴 수 없습니다.")

        org = item.get("org") or None
        if org is not None and org not in VALID_ORGS:
            raise ProblemError(422, "되돌리기 실패", "이력에 알 수 없는 조직 값이 있어 되돌릴 수 없습니다.")

        rows.append(
            (
                duty_date,
                day_type,
                None if day_type == "평일" else org,
                clean_text(str(item.get("person") or ""))[:50],
                clean_text(str(item.get("note") or ""))[:200],
            )
        )
    return rows


# --------------------------------------------------------------------------
# 이력 요약문 — 사람이 목록에서 읽고 바로 알아볼 수 있게
# --------------------------------------------------------------------------


def _label(row: dict[str, Any] | None) -> str:
    """한 행을 '조직 담당자' 한 줄로 요약한다."""
    if row is None:
        return "없음"
    parts = [row.get("org") or row.get("dayType") or "", row.get("person") or ""]
    label = " ".join(p for p in parts if p).strip()
    return label or "빈 일정"


def _day_summary(duty_date: date, before: dict | None, after: dict | None) -> str:
    return f"{duty_date.isoformat()} {_label(before)} → {_label(after)}"


# --------------------------------------------------------------------------
# 조회
# --------------------------------------------------------------------------


async def get_schedule(
    conn: asyncpg.Connection,
    date_from: date | None,
    date_to: date | None,
) -> ScheduleOut:
    """일정을 읽어 화면이 기대하는 두 갈래 배열로 바꿔 준다."""
    if date_from and date_to:
        if date_from > date_to:
            raise ProblemError(400, "입력값 오류", "시작일이 종료일보다 늦습니다.")
        if (date_to - date_from).days > MAX_RANGE_DAYS:
            raise ProblemError(400, "입력값 오류", f"조회 기간은 최대 {MAX_RANGE_DAYS}일까지입니다.")

    rows = await repo.fetch_days(conn, date_from, date_to)
    updated_at: datetime | None = await repo.max_updated_at(conn)

    weekend: list[DutyDayOut] = []
    weekday: list[DutyDayOut] = []
    for row in rows:
        item = DutyDayOut.from_row(row)
        # 저장은 하루 한 행이지만, 화면은 「휴일 3팀 순환」과 「평일 개별」 두 표를 기대한다.
        (weekend if item.type == "휴일" else weekday).append(item)

    return ScheduleOut(
        meta=ScheduleMeta(
            updatedAt=updated_at.isoformat() if updated_at else None,
            count=len(rows),
        ),
        weekendDuty=weekend,
        weekdayDuty=weekday,
    )


# --------------------------------------------------------------------------
# 하루 단위 변경
# --------------------------------------------------------------------------


async def save_day(
    conn: asyncpg.Connection,
    duty_date: date,
    payload: DutyUpsert,
) -> DutyDayOut:
    """하루치를 저장한다. 이력 기록과 저장이 한 트랜잭션으로 묶인다.

    둘을 묶는 이유: 저장은 됐는데 이력이 빠지면 그 변경은 되돌릴 수 없는 변경이 된다.
    """
    async with conn.transaction():
        existing = await repo.fetch_day(conn, duty_date)
        before = _snapshot(SCOPE_DAY, [existing] if existing else [])

        saved = await repo.upsert_day(
            conn,
            duty_date,
            payload.day_type,
            payload.normalized_org(),
            payload.person,
            payload.note,
        )
        after = _snapshot(SCOPE_DAY, [saved])

        await repo.insert_change(
            conn,
            action="수정",
            duty_date=duty_date,
            before_data=before,
            after_data=after,
            summary=_day_summary(
                duty_date,
                before["rows"][0] if before["rows"] else None,
                after["rows"][0],
            ),
        )

    return DutyDayOut.from_row(saved)


async def clear_day(conn: asyncpg.Connection, duty_date: date) -> bool:
    """하루치를 비운다. 원래 없던 날짜면 아무 일도 하지 않는다(멱등)."""
    async with conn.transaction():
        removed = await repo.delete_day(conn, duty_date)
        if removed is None:
            return False

        before = _snapshot(SCOPE_DAY, [removed])
        await repo.insert_change(
            conn,
            action="삭제",
            duty_date=duty_date,
            before_data=before,
            after_data=_snapshot(SCOPE_DAY, []),
            summary=_day_summary(duty_date, before["rows"][0], None),
        )
    return True


# --------------------------------------------------------------------------
# 전체 교체 (엑셀 일괄 반영)
# --------------------------------------------------------------------------


async def replace_schedule(
    conn: asyncpg.Connection,
    rows: Sequence[DutyRow],
) -> tuple[int, int]:
    """전체 일정을 새 목록으로 갈아끼운다. (교체 건수, 스냅샷 이력번호)를 돌려준다.

    **순서가 곧 방어 장치다.** 기존 전체를 이력에 남긴 뒤에 지운다.
    호출자는 이 함수를 부르기 전에 이미 입력 전건을 검증해 두어야 한다 —
    파싱 도중 실패는 데이터를 건드리기 전에 끝나야 하기 때문이다.
    """
    if not rows:
        raise ProblemError(400, "엑셀 형식 오류", "반영할 당직 일정이 없습니다.")

    async with conn.transaction():
        existing = await repo.fetch_days(conn)
        before = _snapshot(SCOPE_ALL, existing)

        snapshot_id = await repo.insert_change(
            conn,
            action="엑셀교체",
            duty_date=None,
            before_data=before,
            after_data={"scope": SCOPE_ALL, "rows": [
                {
                    "date": r[0].isoformat(),
                    "dayType": r[1],
                    "org": r[2],
                    "person": r[3],
                    "note": r[4],
                }
                for r in rows
            ]},
            summary=f"엑셀 일괄 교체 ({len(existing)}건 → {len(rows)}건)",
        )
        replaced = await repo.replace_all_days(conn, rows)

    return replaced, snapshot_id


# --------------------------------------------------------------------------
# 이력 · 되돌리기
# --------------------------------------------------------------------------


async def get_history(conn: asyncpg.Connection, limit: int) -> HistoryOut:
    limit = max(1, min(limit, MAX_HISTORY_LIMIT))
    rows = await repo.fetch_changes(conn, limit)
    return HistoryOut(
        items=[
            HistoryItemOut(
                id=row["id"],
                changedAt=row["changed_at"].isoformat(),
                action=row["action"],
                dutyDate=row["duty_date"].isoformat() if row["duty_date"] else None,
                summary=row["summary"],
                revertable=row["revertable"],
            )
            for row in rows
        ]
    )


async def revert(conn: asyncpg.Connection, change_id: int) -> int:
    """이력의 '변경 전 상태'로 되돌린다. 복원한 행 수를 돌려준다.

    되돌리기 자체도 이력에 남긴다. 되돌리기를 잘못했을 때 다시 되돌릴 수 있어야 한다.
    """
    async with conn.transaction():
        change = await repo.fetch_change(conn, change_id)
        if change is None:
            raise ProblemError(404, "이력 없음", "해당 변경 이력을 찾을 수 없습니다.")

        snapshot = change["before_data"]
        if not snapshot:
            raise ProblemError(422, "되돌리기 불가", "이 이력에는 되돌릴 이전 상태가 없습니다.")

        target_rows = _snapshot_to_rows(snapshot)
        scope = snapshot.get("scope", SCOPE_DAY)

        if scope == SCOPE_ALL:
            current = _snapshot(SCOPE_ALL, await repo.fetch_days(conn))
            await repo.replace_all_days(conn, target_rows)
        else:
            duty_date = change["duty_date"]
            if duty_date is None:
                raise ProblemError(422, "되돌리기 불가", "이력에 대상 날짜가 없습니다.")
            existing = await repo.fetch_day(conn, duty_date)
            current = _snapshot(SCOPE_DAY, [existing] if existing else [])
            await repo.delete_day(conn, duty_date)
            for row in target_rows:
                await repo.upsert_day(conn, *row)

        await repo.insert_change(
            conn,
            action="되돌리기",
            duty_date=change["duty_date"],
            before_data=current,
            after_data=snapshot,
            summary=f"이력 #{change_id}({change['action']}) 되돌리기 · {len(target_rows)}건 복원",
        )

    return len(target_rows)
