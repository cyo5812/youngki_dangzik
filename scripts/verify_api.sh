#!/usr/bin/env bash
# API 검증 스크립트 (개발용). 컨테이너 이미지에는 포함되지 않는다.
#
#   1) PostgreSQL 을 띄우고 schema.sql 을 적용한다
#   2) DATABASE_URL 을 주고 앱을 실행한다
#   3) BASE_URL 을 주고 이 스크립트를 돌린다
#
#   BASE_URL=http://127.0.0.1:8080 ./scripts/verify_api.sh
#
# 수정 계열 검증을 하려면 ADMIN_KEY 가 주입되지 않은 상태로 앱을 띄워야 한다.

set -uo pipefail
B="${BASE_URL:-http://127.0.0.1:8080}"
J='Content-Type: application/json'
PASS=0; FAIL=0

check() { # check <설명> <기대> <실제>
  if [ "$2" = "$3" ]; then PASS=$((PASS+1)); printf "  PASS  %-44s %s\n" "$1" "$3"
  else FAIL=$((FAIL+1)); printf "  FAIL  %-44s 기대=%s 실제=%s\n" "$1" "$2" "$3"; fi
}
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

echo "== 기본 =="
check "헬스체크" 200 "$(code $B/api/health)"
check "설정 조회" 200 "$(code $B/api/config)"
check "일정 조회" 200 "$(code $B/api/schedule)"
check "CSP 헤더" 1 "$(curl -s -D- -o /dev/null $B/api/health | grep -ci 'content-security-policy')"
check "nosniff 헤더" 1 "$(curl -s -D- -o /dev/null $B/api/health | grep -ci 'x-content-type-options')"
check "오류가 problem+json" 1 \
  "$(curl -s -D- -o /dev/null -X PUT $B/api/schedule/2026-10-01 -H "$J" -d '{"dayType":"반휴"}' | grep -ci 'application/problem+json')"

echo "== 입력 검증 (전부 400) =="
check "알 수 없는 구분" 400 "$(code -X PUT $B/api/schedule/2026-10-01 -H "$J" -d '{"dayType":"반휴"}')"
check "알 수 없는 조직" 400 "$(code -X PUT $B/api/schedule/2026-10-01 -H "$J" -d '{"dayType":"휴일","org":"없는팀"}')"
check "정의되지 않은 필드" 400 "$(code -X PUT $B/api/schedule/2026-10-01 -H "$J" -d '{"dayType":"평일","해킹":"x"}')"
check "잘못된 날짜 경로" 400 "$(code -X PUT $B/api/schedule/2026-13-45 -H "$J" -d '{"dayType":"평일"}')"
check "조회 범위 초과" 400 "$(code "$B/api/schedule?from=2020-01-01&to=2026-12-31")"
check "없는 경로" 404 "$(code $B/api/없는것)"

echo "== 멱등성 =="
check "삭제 1회" 204 "$(code -X DELETE $B/api/schedule/2027-12-31)"
check "삭제 2회 (없던 날짜)" 204 "$(code -X DELETE $B/api/schedule/2027-12-31)"

echo "== 업로드 방어 =="
head -c 2000 /dev/urandom > /tmp/_garbage.xlsx
check "쓰레기 파일 거부" 400 "$(code -X POST $B/api/schedule/import --data-binary @/tmp/_garbage.xlsx)"
head -c 11000000 /dev/zero > /tmp/_big.bin
check "10MB 초과 거부" 413 "$(code -X POST $B/api/schedule/import --data-binary @/tmp/_big.bin)"
check "빈 본문 거부" 400 "$(code -X POST $B/api/schedule/import --data-binary '')"
rm -f /tmp/_garbage.xlsx /tmp/_big.bin

echo "== SQL 인젝션 =="
BEFORE=$(curl -s $B/api/schedule | python3 -c "import json,sys;print(json.load(sys.stdin)['meta']['count'])")
curl -s -o /dev/null -X PUT "$B/api/schedule/2027-01-01" -H "$J" \
  -d "{\"dayType\":\"평일\",\"person\":\"'; DROP TABLE duty_day; --\"}"
AFTER=$(curl -s $B/api/schedule | python3 -c "import json,sys;print(json.load(sys.stdin)['meta']['count'])" 2>/dev/null || echo "테이블소실")
check "인젝션 후 테이블 생존" "$((BEFORE+1))" "$AFTER"
curl -s -o /dev/null -X DELETE "$B/api/schedule/2027-01-01"

echo
echo "결과: PASS $PASS · FAIL $FAIL"
[ "$FAIL" -eq 0 ]
