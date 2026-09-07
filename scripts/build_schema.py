"""schema.sql 생성기.

운영자가 최초 1회 실행할 DDL + 초기 데이터를 만든다.
초기 데이터는 기존 `data/schedule.json`(주말 표 · 평일 표)을 **하루 = 한 행**으로 합쳐서 넣는다.

사용법:
    python3 scripts/build_schema.py            # data/schedule.json -> schema.sql
    python3 scripts/build_schema.py <입력json> <출력sql>

이 파일을 손으로 고치지 말고, 원본 JSON을 고친 뒤 다시 실행한다.
"""

from __future__ import annotations

import json
import sys
from datetime import date

DAY_TYPES = ("평일", "휴일")
ORGS = ("영기", "소강", "도강", "전산휴무")

DDL = """\
-- =============================================================
--  당직 일정표 — 스키마 및 초기 데이터
--  운영자가 최초 1회 실행합니다. 여러 번 실행해도 안전합니다.
--  파일 인코딩: UTF-8
-- =============================================================
--  앱 계정에 필요한 권한: SELECT / INSERT / UPDATE / DELETE
--  (앱은 기동 시 테이블을 만들지 않습니다. DDL 권한이 필요 없습니다.)
-- =============================================================

BEGIN;

-- 당직 일정 본체. 하루 = 한 행이며 날짜가 곧 식별자다.
-- 날짜를 기본키로 두면 "같은 날짜에 서로 다른 당직 정보" 라는 모순 상태가 원천 차단된다.
CREATE TABLE IF NOT EXISTS duty_day (
    duty_date   DATE         PRIMARY KEY,
    day_type    TEXT         NOT NULL
                             CHECK (day_type IN ('평일', '휴일')),
    org         TEXT         CHECK (org IS NULL OR org IN ('영기', '소강', '도강', '전산휴무')),
    person      TEXT         NOT NULL DEFAULT ''
                             CHECK (char_length(person) <= 50),
    note        TEXT         NOT NULL DEFAULT ''
                             CHECK (char_length(note) <= 200),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

COMMENT ON TABLE  duty_day            IS '당직 일정 (하루 = 한 행)';
COMMENT ON COLUMN duty_day.day_type   IS '구분: 평일 | 휴일';
COMMENT ON COLUMN duty_day.org        IS '휴일 당직 조직 (평일은 NULL)';
COMMENT ON COLUMN duty_day.person     IS '담당자 성명 (조직과 무관하게 입력 가능)';
COMMENT ON COLUMN duty_day.note       IS '비고';

-- 변경 이력. 되돌리기의 근거가 되므로 duty_day 가 지워져도 남아야 한다(FK 없음).
CREATE TABLE IF NOT EXISTS duty_change_log (
    id           BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    changed_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    action       TEXT         NOT NULL
                              CHECK (action IN ('수정', '삭제', '엑셀교체', '되돌리기')),
    duty_date    DATE,
    before_data  JSONB,
    after_data   JSONB,
    summary      TEXT         NOT NULL DEFAULT ''
);

COMMENT ON TABLE  duty_change_log             IS '당직 일정 변경 이력 (되돌리기 근거)';
COMMENT ON COLUMN duty_change_log.duty_date   IS '대상 날짜. 전체 교체면 NULL';
COMMENT ON COLUMN duty_change_log.before_data IS '변경 전 스냅샷. 전체 교체면 전체 일정';
COMMENT ON COLUMN duty_change_log.after_data  IS '변경 후 스냅샷. 삭제면 NULL';

-- 최신 이력부터 조회한다.
CREATE INDEX IF NOT EXISTS idx_change_log_at ON duty_change_log (changed_at DESC);
"""

SEED_HEADER = """
-- -------------------------------------------------------------
--  초기 데이터 — {count}건 ({first} ~ {last})
--  이미 데이터가 있으면 건너뛴다(ON CONFLICT DO NOTHING).
-- -------------------------------------------------------------
INSERT INTO duty_day (duty_date, day_type, org, person, note) VALUES
"""


def sql_text(value: str | None) -> str:
    """문자열을 SQL 리터럴로 만든다. 작은따옴표를 두 번 써서 이스케이프한다."""
    if value is None:
        return "NULL"
    return "'" + value.replace("'", "''") + "'"


def merge(payload: dict) -> list[dict]:
    """주말 표와 평일 표를 날짜 기준 한 행으로 합친다.

    평일 표가 모든 날짜(구분 포함)를 담고 있으므로 이를 바탕으로 깔고,
    주말/휴일 표에 있는 날짜는 그 값으로 덮어쓴다.
    """
    merged: dict[str, dict] = {}

    for row in payload.get("weekdayDuty", []):
        merged[row["date"]] = {
            "date": row["date"],
            "day_type": row.get("type") or "평일",
            "org": None,
            "person": (row.get("person") or "").strip(),
            "note": "",
        }

    for row in payload.get("weekendDuty", []):
        merged[row["date"]] = {
            "date": row["date"],
            "day_type": row.get("type") or "휴일",
            "org": (row.get("org") or "").strip() or None,
            "person": (row.get("person") or "").strip(),
            "note": (row.get("note") or "").strip(),
        }

    rows = [merged[key] for key in sorted(merged)]
    validate(rows)
    return rows


def validate(rows: list[dict]) -> None:
    """생성 전에 값 자체를 검증한다. 잘못된 SQL 을 만들어 놓고 나중에 발견하는 일을 막는다."""
    for row in rows:
        date.fromisoformat(row["date"])  # 형식이 틀리면 여기서 예외
        if row["day_type"] not in DAY_TYPES:
            raise ValueError(f"{row['date']}: 알 수 없는 구분 {row['day_type']!r}")
        if row["org"] is not None and row["org"] not in ORGS:
            raise ValueError(f"{row['date']}: 알 수 없는 조직 {row['org']!r}")
        if len(row["person"]) > 50:
            raise ValueError(f"{row['date']}: 담당자명이 50자를 넘습니다")
        if len(row["note"]) > 200:
            raise ValueError(f"{row['date']}: 비고가 200자를 넘습니다")
        if row["day_type"] == "평일" and row["org"]:
            raise ValueError(f"{row['date']}: 평일에는 당직 조직을 둘 수 없습니다")


def build(rows: list[dict]) -> str:
    values = [
        "    ({date}, {day_type}, {org}, {person}, {note})".format(
            date=sql_text(row["date"]),
            day_type=sql_text(row["day_type"]),
            org=sql_text(row["org"]),
            person=sql_text(row["person"]),
            note=sql_text(row["note"]),
        )
        for row in rows
    ]
    seed = SEED_HEADER.format(count=len(rows), first=rows[0]["date"], last=rows[-1]["date"])
    seed += ",\n".join(values) + "\nON CONFLICT (duty_date) DO NOTHING;\n\nCOMMIT;\n"
    return DDL + seed


def main() -> None:
    src = sys.argv[1] if len(sys.argv) > 1 else "data/schedule.json"
    out = sys.argv[2] if len(sys.argv) > 2 else "schema.sql"

    with open(src, encoding="utf-8") as fp:
        payload = json.load(fp)

    rows = merge(payload)
    with open(out, "w", encoding="utf-8") as fp:
        fp.write(build(rows))

    holidays = sum(1 for r in rows if r["day_type"] == "휴일")
    print(f"{len(rows)}건 생성 (휴일 {holidays} · 평일 {len(rows) - holidays}) -> {out}")


if __name__ == "__main__":
    main()
