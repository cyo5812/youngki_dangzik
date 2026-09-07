"""환경설정 로딩.

이 파일이 환경변수를 읽는 **유일한 지점**이다. 다른 모듈에서 os.environ 을 직접 만지지 않는다.
접속 문자열·관리자 키 같은 비밀값은 이 모듈 밖으로 원문이 나가지 않으며,
로그·예외 메시지·API 응답 어디에도 값을 싣지 않는다(있다/없다만 알린다).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# 사내 플랫폼 가이드 상한: 업로드 파일 개당 10MB
_DEFAULT_MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class ConfigError(RuntimeError):
    """환경설정이 잘못됐을 때 발생. 메시지에 비밀값을 담지 않는다."""


@dataclass(frozen=True)
class Settings:
    """앱 실행에 필요한 설정 묶음. 기동 시 한 번 만들고 이후 바꾸지 않는다."""

    database_url: str
    admin_key: str  # 빈 문자열이면 잠금 해제 상태(사내망 사용자 누구나 수정 가능)
    port: int
    db_pool_min: int
    db_pool_max: int
    max_upload_bytes: int
    log_level: str

    @property
    def admin_lock_enabled(self) -> bool:
        """관리자 키가 주입됐는지 여부.

        주입된 경우에만 수정 계열 API가 X-Admin-Key 헤더를 요구한다.
        즉 운영자가 Secret 을 넣는 것만으로 나중에 잠글 수 있다.
        """
        return bool(self.admin_key)

    def describe(self) -> dict[str, object]:
        """기동 로그용 요약. **비밀값은 값 대신 상태만 남긴다.**"""
        return {
            "port": self.port,
            "db_pool": f"{self.db_pool_min}-{self.db_pool_max}",
            "max_upload_mb": round(self.max_upload_bytes / 1024 / 1024, 1),
            "database_url": "설정됨" if self.database_url else "없음",
            "admin_lock": "켜짐" if self.admin_lock_enabled else "꺼짐",
        }


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    """정수형 환경변수를 범위 검증과 함께 읽는다. 값이 비면 기본값을 쓴다."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"환경변수 {name} 은(는) 정수여야 합니다.") from None
    if not minimum <= value <= maximum:
        raise ConfigError(f"환경변수 {name} 은(는) {minimum}~{maximum} 범위여야 합니다.")
    return value


def load_settings() -> Settings:
    """환경변수를 읽어 Settings 를 만든다. 필수값이 없으면 기동을 중단시킨다.

    기동 시점에 실패시키는 이유: 설정이 빠진 채로 떠 있다가 첫 요청에서 500을 내는 것보다,
    아예 뜨지 않고 파드가 재시작 루프에 들어가는 편이 운영자가 알아채기 쉽다.
    """
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        # 값이 아니라 '없다'는 사실만 알린다.
        raise ConfigError(
            "환경변수 DATABASE_URL 이 필요합니다. 운영자에게 DB 접속 정보 주입을 요청하세요."
        )

    db_pool_min = _int_env("DB_POOL_MIN", default=1, minimum=1, maximum=20)
    db_pool_max = _int_env("DB_POOL_MAX", default=5, minimum=1, maximum=50)
    if db_pool_min > db_pool_max:
        raise ConfigError("DB_POOL_MIN 은 DB_POOL_MAX 보다 클 수 없습니다.")

    log_level = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ConfigError("환경변수 LOG_LEVEL 은 DEBUG·INFO·WARNING·ERROR 중 하나여야 합니다.")

    return Settings(
        database_url=database_url,
        admin_key=os.environ.get("ADMIN_KEY", "").strip(),
        port=_int_env("APP_PORT", default=8080, minimum=1024, maximum=65535),
        db_pool_min=db_pool_min,
        db_pool_max=db_pool_max,
        max_upload_bytes=_int_env(
            "MAX_UPLOAD_BYTES",
            default=_DEFAULT_MAX_UPLOAD_BYTES,
            minimum=1024,
            maximum=_DEFAULT_MAX_UPLOAD_BYTES,
        ),
        log_level=log_level,
    )
