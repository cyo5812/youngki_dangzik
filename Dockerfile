# 당직 일정표 — 사내 AI 도구 플랫폼(kt cloud Managed KS) 배포용 이미지
#
# 이 저장소의 Dockerfile 은 이것 하나뿐이다(도구 하나 = 컨테이너 하나).
# 한 프로세스가 화면(정적 파일)과 API 를 함께 서빙하므로 웹서버를 따로 두지 않는다.
#
# 단일 스테이지로 둔 이유: 의존성 5종이 전부 cp312 manylinux 휠로 제공돼
# 컴파일 도구(gcc)가 필요 없다. 빌드 스테이지를 나눠도 줄일 용량이 없다.

FROM python:3.12-slim

# 패치 버전을 고정하지 않은 것은 의도적이다. 재빌드할 때마다 베이스 이미지의
# 보안 패치를 자동으로 흡수한다(취약점 스캔 지적 시 재빌드만으로 해소되는 경우가 많다).

# ---------------------------------------------------------------------------
# 1) 사내 CA 등록 — 반드시 패키지 설치보다 먼저
#
# 사내망은 HTTPS 를 열어보고 사내 CA 로 재서명한다. 컨테이너 안은 이 CA 를 모르므로
# 이 단계가 없으면 아래 pip install 이 certificate verify failed 로 멈춘다.
# (--trusted-host 나 검증 비활성화로 우회하지 않는다. 사내 가드레일 위반이다.)
# ---------------------------------------------------------------------------
COPY corp-ca.crt /usr/local/share/ca-certificates/corp-ca.crt
RUN update-ca-certificates
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
# NODE_EXTRA_CA_CERTS 는 Node 런타임 전용이라 파이썬 이미지에서는 설정하지 않는다.

# ---------------------------------------------------------------------------
# 2) 파이썬 실행 환경
# ---------------------------------------------------------------------------
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
# PYTHONDONTWRITEBYTECODE — 실행 중 __pycache__ 를 만들지 않는다.
#   readOnlyRootFilesystem 으로 띄워도 쓰기 시도가 없어 안전하다.
# PYTHONUNBUFFERED — 로그가 버퍼에 갇히지 않고 즉시 나온다(파드 로그 수집).

WORKDIR /app

# 의존성을 먼저 설치한다. 소스만 바뀐 재빌드에서 이 레이어가 캐시된다.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 애플리케이션 소스 (.dockerignore 가 문서·기존 GitHub Pages 자산을 제외한다)
COPY app ./app

# 바이트코드를 빌드 시점에 미리 만들어 둔다.
# 실행 중에는 쓰기가 없고(위의 DONTWRITEBYTECODE), 첫 요청 지연도 줄어든다.
RUN python -m compileall -q ./app

# ---------------------------------------------------------------------------
# 3) 비루트 실행
#
# root 로 두면 클러스터 보안 정책(PSA restricted)이 파드 생성을 거부한다.
# 소스는 읽기 전용이면 충분하므로 소유권을 넘기지 않고 root 소유 그대로 둔다
# — 실행 계정이 자기 코드를 고칠 수 없는 상태가 더 안전하다.
# ---------------------------------------------------------------------------
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin duty
USER 10001

# 리슨 포트. 1024 미만은 비루트가 열 수 없고 정책상으로도 금지된다.
# 실제 포트는 APP_PORT 환경변수로 바꿀 수 있다(기본 8080).
EXPOSE 8080

# 상태는 전부 PostgreSQL 에 있다. 접속 정보와 키는 환경변수로만 받는다.
#   필수 : DATABASE_URL
#   선택 : ADMIN_KEY · APP_PORT · DB_POOL_MIN · DB_POOL_MAX · MAX_UPLOAD_BYTES · LOG_LEVEL
CMD ["python", "-m", "app"]
