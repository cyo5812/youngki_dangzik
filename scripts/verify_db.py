"""데이터 접근 계층 검증 스크립트 (개발용).

로컬 PostgreSQL 을 띄운 뒤 DATABASE_URL 을 주고 실행한다. 컨테이너 이미지에는 포함되지 않는다.
    DATABASE_URL=postgresql://... python scripts/verify_db.py
"""
import asyncio, sys, datetime
sys.path.insert(0, ".")
import asyncpg
from app.config import load_settings
from app.db import Database
from app import repository as repo
from app.models import DutyDayOut, DutyUpsert

async def main():
    db = Database(load_settings())
    await db.connect()
    ok = lambda n, c: print(f"  {'PASS' if c else 'FAIL'}  {n}")

    print("[1] 연결·헬스체크")
    ok("healthy()", await db.healthy())

    async with db.pool.acquire() as conn:
        print("[2] 조회")
        rows = await repo.fetch_days(conn)
        ok(f"전체 조회 {len(rows)}건", len(rows) == 122)
        sep = await repo.fetch_days(conn, datetime.date(2026,9,1), datetime.date(2026,9,30))
        ok(f"9월 범위 조회 {len(sep)}건", len(sep) == 30)
        one = await repo.fetch_day(conn, datetime.date(2026,9,19))
        ok("담당자 보존(최진필)", one["person"] == "최진필")

        print("[3] 응답 변환 (요일 계산)")
        out = DutyDayOut.from_row(one)
        ok(f"2026-09-19 -> {out.weekday}요일", out.weekday == "토")
        ok("응답 필드 형식", out.date == "2026-09-19" and out.type == "휴일" and out.org == "도강")

        print("[4] upsert / delete (트랜잭션 롤백으로 원복)")
        tx = conn.transaction(); await tx.start()
        r = await repo.upsert_day(conn, datetime.date(2026,9,5), "휴일", "영기", "테스트", "메모")
        ok("신규 수정 반영", r["person"] == "테스트")
        r2 = await repo.upsert_day(conn, datetime.date(2026,9,5), "휴일", "소강", "", "")
        ok("같은 날짜 재수정(행 1개 유지)", r2["org"] == "소강")
        cnt = await conn.fetchval("SELECT count(*) FROM duty_day WHERE duty_date='2026-09-05'")
        ok("중복 행 없음", cnt == 1)
        d = await repo.delete_day(conn, datetime.date(2026,9,5))
        ok("삭제 반환값", d is not None)
        d2 = await repo.delete_day(conn, datetime.date(2026,9,5))
        ok("없는 날짜 재삭제 = None (멱등)", d2 is None)

        print("[5] 변경 이력 · JSONB 왕복")
        cid = await repo.insert_change(conn, "수정", datetime.date(2026,9,5),
                                       {"person":"배병모"}, {"person":"황성용"}, "테스트 변경")
        ok("이력 번호 발급", isinstance(cid, int) and cid > 0)
        got = await repo.fetch_change(conn, cid)
        ok("JSONB가 dict로 복원", isinstance(got["before_data"], dict) and got["before_data"]["person"]=="배병모")
        hist = await repo.fetch_changes(conn, 10)
        ok("이력 목록 revertable 판정", hist[0]["revertable"] is True)
        await tx.rollback()

        print("[6] DB 제약조건이 잘못된 값을 막는가")
        for label, args in [
            ("알 수 없는 구분('반휴')", (datetime.date(2027,1,1), "반휴", None, "", "")),
            ("알 수 없는 조직('없는팀')", (datetime.date(2027,1,1), "휴일", "없는팀", "", "")),
            ("담당자 50자 초과", (datetime.date(2027,1,1), "평일", None, "가"*51, "")),
        ]:
            try:
                tx2 = conn.transaction(); await tx2.start()
                await repo.upsert_day(conn, *args)
                await tx2.rollback(); ok(label + " 차단", False)
            except asyncpg.PostgresError:
                await tx2.rollback(); ok(label + " 차단", True)

        print("[7] 최소권한 확인 (앱 계정은 DDL 불가여야 정상)")
        try:
            await conn.execute("CREATE TABLE should_not_exist (id int)")
            ok("DDL 거부", False)
        except asyncpg.InsufficientPrivilegeError:
            ok("DDL 거부됨 (권한 부족)", True)

        print("[8] Pydantic 입력 검증")
        m = DutyUpsert(dayType="평일", org="영기", person="  홍길동\x00 ", note="비고\n줄바꿈")
        ok("제어문자·공백 제거", m.person == "홍길동" and "\n" not in m.note)
        ok("평일이면 조직 무시", m.normalized_org() is None)
        m2 = DutyUpsert(dayType="휴일", org="도강", person="최진필")
        ok("휴일 조직 유지 + 담당자 보존", m2.normalized_org() == "도강" and m2.person == "최진필")
        for bad in [dict(dayType="반휴"), dict(dayType="평일", org="없는팀"),
                    dict(dayType="평일", person="가"*51), dict(dayType="평일", 이상한필드="x")]:
            try:
                DutyUpsert(**bad); ok(f"거부 {bad}", False)
            except Exception:
                ok(f"거부 {list(bad)[-1]}={list(bad.values())[-1]!r:>8}", True)

    await db.close()
    print("\n[9] 종료 후 상태")
    ok("풀 정리됨", db._pool is None)

asyncio.run(main())
