"""요청·응답 스키마 (Pydantic).

**모든 외부 입력은 여기를 통과해야만 아래 계층으로 갈 수 있다.**
클라이언트가 보낸 값은 신뢰하지 않는다 — 타입·열거값·길이를 서버에서 다시 확인하고,
제어문자처럼 화면·로그를 어지럽힐 수 있는 문자는 걸러낸다.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 구분과 조직은 열거값이다. 여기에 없는 값은 애초에 들어올 수 없다.
DayType = Literal["평일", "휴일"]
OrgName = Literal["영기", "소강", "도강", "전산휴무"]

PERSON_MAX = 50
NOTE_MAX = 200

# 요일 라벨 — date.weekday() 는 월요일이 0 이다.
WEEKDAY_LABELS = ("월", "화", "수", "목", "금", "토", "일")

# 제어문자(개행·탭·NUL 등). 화면 깨짐과 로그 위조(CRLF 주입)를 막기 위해 제거한다.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def clean_text(value: str) -> str:
    """앞뒤 공백을 자르고 제어문자를 제거한다."""
    return _CONTROL_CHARS.sub("", value).strip()


def weekday_label(day: date) -> str:
    """날짜에서 요일 라벨을 구한다.

    요일은 저장하지 않고 매번 계산한다. 저장해 두면 날짜와 어긋날 수 있는 중복 정보가 된다.
    """
    return WEEKDAY_LABELS[day.weekday()]


class DutyUpsert(BaseModel):
    """하루치 당직 등록·수정 요청.

    `extra="forbid"` — 정의하지 않은 필드가 섞여 오면 요청 자체를 거부한다.
    오탈자나 클라이언트 버그가 조용히 무시되는 대신 즉시 드러난다.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    day_type: DayType = Field(alias="dayType")
    org: OrgName | None = None
    person: Annotated[str, Field(max_length=PERSON_MAX)] = ""
    note: Annotated[str, Field(max_length=NOTE_MAX)] = ""

    @field_validator("person", "note", mode="before")
    @classmethod
    def _clean(cls, value: object) -> object:
        return clean_text(value) if isinstance(value, str) else value

    def normalized_org(self) -> str | None:
        """평일은 3팀 순환 대상이 아니므로 조직을 비운다.

        담당자(person)는 조직과 무관하게 그대로 둔다 — 실제 데이터에 영기가 아닌 조직 차례에도
        담당자가 적힌 행이 있어, 규칙으로 지우면 값이 소리 없이 사라진다.
        """
        return None if self.day_type == "평일" else self.org


class DutyDayOut(BaseModel):
    """하루치 당직 응답. 기존 화면이 기대하는 필드 이름을 그대로 쓴다."""

    date: str
    weekday: str
    type: DayType
    org: str = ""
    person: str = ""
    note: str = ""

    @classmethod
    def from_row(cls, row: object) -> "DutyDayOut":
        """DB 행(asyncpg Record)을 응답 모델로 바꾼다."""
        duty_date: date = row["duty_date"]
        return cls(
            date=duty_date.isoformat(),
            weekday=weekday_label(duty_date),
            type=row["day_type"],
            org=row["org"] or "",
            person=row["person"] or "",
            note=row["note"] or "",
        )


class ScheduleMeta(BaseModel):
    updatedAt: str | None = None
    count: int = 0


class ScheduleOut(BaseModel):
    """전체 일정 응답.

    저장은 하루 한 행이지만, 화면은 「주말/휴일 표」와 「평일 표」 두 갈래를 기대한다.
    변환을 서버가 맡아 기존 프론트 로직을 그대로 재사용한다.
    """

    meta: ScheduleMeta
    weekendDuty: list[DutyDayOut]
    weekdayDuty: list[DutyDayOut]


class HistoryItemOut(BaseModel):
    """변경 이력 한 건."""

    id: int
    changedAt: str
    action: str
    dutyDate: str | None
    summary: str
    revertable: bool


class HistoryOut(BaseModel):
    items: list[HistoryItemOut]


class ImportResultOut(BaseModel):
    """엑셀 일괄 반영 결과."""

    replaced: int
    snapshotId: int
    message: str


class ScheduleSummary(BaseModel):
    """일정 묶음의 요약. 교체 전후를 나란히 보여 주려고 쓴다."""

    count: int = 0
    firstDate: str | None = None
    lastDate: str | None = None
    holidays: int = 0


class ImportPreviewOut(BaseModel):
    """엑셀 미리보기 결과. 아직 DB 는 건드리지 않은 상태다.

    작년 파일이나 다른 팀 파일을 실수로 올리면 형식은 멀쩡해 파싱에 성공한다.
    그래서 **바꾸기 전에 무엇으로 바뀌는지** 먼저 보여 준다.
    """

    current: ScheduleSummary
    incoming: ScheduleSummary
    warnings: list[str] = []


class RevertResultOut(BaseModel):
    """되돌리기 결과."""

    restored: int
    message: str
