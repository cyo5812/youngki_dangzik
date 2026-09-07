"""당직표 엑셀 파싱.

**파일을 디스크에 쓰지 않는다.** 넘어온 바이트를 메모리에서 열어 읽고 즉시 버린다.

시트 이름을 고정하지 않는다. 원본이 「26년 당직 일정_정리」라 해서 그대로 박아 두면
내년 파일(「27년 …」)에서 바로 깨진다. 대신 **머리글 '날짜'를 찾아 표의 위치를 스스로 알아낸다.**

표는 두 종류다.
  · 주말/휴일 표 — 월 · 날짜 · 요일 · 구분 · 조직 · 담당자 · 비고
  · 평일 표     — 월 · 날짜 · 요일 · 구분 · 담당자
'날짜' 머리글 오른쪽 세 칸 뒤에 '조직'이 있으면 주말 표, 없으면 평일 표로 판별한다.
"""

from __future__ import annotations

import io
import logging
from datetime import date, datetime
from typing import Any

from openpyxl import load_workbook

from app.errors import ProblemError
from app.models import NOTE_MAX, PERSON_MAX, clean_text

logger = logging.getLogger("duty.excel")

VALID_DAY_TYPES = {"평일", "휴일"}
VALID_ORGS = {"영기", "소강", "도강", "전산휴무"}

# 머리글을 찾을 범위와 표 길이 상한 — 이상한 파일이 메모리를 끝없이 먹지 않게 한다.
_HEADER_SEARCH_ROWS = 20
_MAX_DATA_ROWS = 1000

DutyRow = tuple[date, str, str | None, str, str]


def _cell_text(value: Any) -> str:
    """셀 값을 비교 가능한 문자열로 만든다. 머리글에 줄바꿈이 섞여 있어도 견딘다."""
    if value is None:
        return ""
    return clean_text(str(value)).replace(" ", "")


def _as_date(value: Any) -> date | None:
    """셀 값을 날짜로 바꾼다. 날짜가 아니면 None."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def _find_tables(sheet: Any) -> list[dict[str, Any]]:
    """시트에서 '날짜' 머리글을 찾아 표의 시작 위치와 열 배치를 알아낸다."""
    tables: list[dict[str, Any]] = []
    max_row = min(sheet.max_row or 0, _HEADER_SEARCH_ROWS)

    for row_idx in range(1, max_row + 1):
        for col_idx in range(1, (sheet.max_column or 0) + 1):
            if _cell_text(sheet.cell(row=row_idx, column=col_idx).value) != "날짜":
                continue
            has_org = _cell_text(sheet.cell(row=row_idx, column=col_idx + 3).value) == "조직"
            tables.append(
                {
                    "header_row": row_idx,
                    "date_col": col_idx,
                    "type_col": col_idx + 2,  # 구분
                    "org_col": col_idx + 3 if has_org else None,
                    "person_col": col_idx + 4 if has_org else col_idx + 3,
                    "note_col": col_idx + 5 if has_org else None,
                    "kind": "휴일표" if has_org else "평일표",
                }
            )
    return tables


def _read_table(sheet: Any, table: dict[str, Any]) -> dict[date, dict[str, Any]]:
    """표 하나를 날짜별 dict 로 읽는다. 날짜 칸이 비면 표가 끝난 것으로 본다."""
    rows: dict[date, dict[str, Any]] = {}
    row_idx = table["header_row"] + 1
    read = 0

    while read < _MAX_DATA_ROWS:
        raw_date = sheet.cell(row=row_idx, column=table["date_col"]).value
        if raw_date is None or (isinstance(raw_date, str) and not raw_date.strip()):
            break

        duty_date = _as_date(raw_date)
        if duty_date is None:
            raise ProblemError(
                400,
                "엑셀 형식 오류",
                f"{table['kind']} {row_idx}행의 날짜를 읽을 수 없습니다.",
            )

        day_type = _cell_text(sheet.cell(row=row_idx, column=table["type_col"]).value)
        if day_type not in VALID_DAY_TYPES:
            raise ProblemError(
                400,
                "엑셀 형식 오류",
                f"{table['kind']} {row_idx}행의 구분은 '평일' 또는 '휴일'이어야 합니다.",
            )

        org = None
        if table["org_col"]:
            org_text = _cell_text(sheet.cell(row=row_idx, column=table["org_col"]).value)
            if org_text and org_text not in VALID_ORGS:
                raise ProblemError(
                    400,
                    "엑셀 형식 오류",
                    f"{table['kind']} {row_idx}행에 알 수 없는 조직이 있습니다: {org_text[:20]}",
                )
            org = org_text or None

        person = clean_text(str(sheet.cell(row=row_idx, column=table["person_col"]).value or ""))
        note = ""
        if table["note_col"]:
            note = clean_text(str(sheet.cell(row=row_idx, column=table["note_col"]).value or ""))

        if len(person) > PERSON_MAX or len(note) > NOTE_MAX:
            raise ProblemError(
                400,
                "엑셀 형식 오류",
                f"{table['kind']} {row_idx}행의 담당자 또는 비고가 너무 깁니다.",
            )

        rows[duty_date] = {
            "day_type": day_type,
            "org": None if day_type == "평일" else org,
            "person": person,
            "note": note,
        }
        row_idx += 1
        read += 1

    return rows


def parse_duty_workbook(content: bytes) -> list[DutyRow]:
    """엑셀 바이트를 읽어 하루 = 한 행 목록으로 만든다.

    한 행이라도 이상하면 **여기서 예외로 끝난다.** 호출자는 이 함수가 성공한 뒤에야
    DB 를 건드리므로, 형식 오류로 기존 데이터가 손상되는 일이 없다.
    """
    try:
        # data_only=True  : 수식을 평가하지 않고 저장된 값만 읽는다(수식 폭탄 차단)
        # read_only=True  : 시트를 통째로 메모리에 올리지 않는다
        workbook = load_workbook(io.BytesIO(content), data_only=True, read_only=False)
    except Exception as exc:
        logger.warning("엑셀 열기 실패: %s", type(exc).__name__)
        raise ProblemError(400, "엑셀 형식 오류", "엑셀 파일(.xlsx)로 읽을 수 없습니다.") from None

    try:
        merged: dict[date, dict[str, Any]] = {}
        found = False

        for sheet in workbook.worksheets:
            tables = _find_tables(sheet)
            if not tables:
                continue
            found = True
            # 평일표를 먼저 깔고 휴일표로 덮는다 — 같은 날짜면 휴일 정보가 우선이다.
            for table in sorted(tables, key=lambda t: t["kind"] == "휴일표"):
                merged.update(_read_table(sheet, table))

        if not found:
            raise ProblemError(
                400,
                "엑셀 형식 오류",
                "당직표 형식을 인식할 수 없습니다. '날짜'·'구분' 머리글이 있는 표가 필요합니다.",
            )
        if not merged:
            raise ProblemError(400, "엑셀 형식 오류", "당직 일정 행을 찾지 못했습니다.")

        return [
            (
                duty_date,
                row["day_type"],
                row["org"],
                row["person"],
                row["note"],
            )
            for duty_date, row in sorted(merged.items())
        ]
    finally:
        workbook.close()
