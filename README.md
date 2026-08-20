# DirectDesk

가상의 가전 공급사 **다온비즈**가 숙박업 소상공인의 인바운드 문의와 아웃바운드 잠재고객을 관리하는 AI CRM 프로토타입입니다.

## 현재 구현 범위

- 공개 채팅형 문의 접수와 제품 데이터 기반 맞춤 분석
- FastAPI JWT 로그인과 manager/rep 권한 검사
- 계정·연락처·문의 CRUD, 스코어링 재시도, 자동배정과 manager 수동 재배정
- fit/intent/recency 근거와 `fit×0.35 + intent×0.50 + recency×0.15` 영업 우선순위
- read-only 연결과 AST 화이트리스트를 사용하는 NL2SQL
- 리드 목록, 3단계 마케팅 초안, 사람 검토, dry-run/test override 발송, 내부 이벤트 대시보드
- React 공개 상담 화면과 밀도형 내부 직원 화면
- 가상 계정 24개, 가상 리드 12개, 공식 제품 페이지 기반 고정 제품 9개

LOCALDATA 수집 파서는 아직 포함하지 않았습니다. 공식 API가 인증키 없는 실제 호출에 `401/403`을 반환해 응답 필드를 확인할 수 없었기 때문입니다. `LOCALDATA_API_KEY`로 실제 응답을 확인한 뒤 추가해야 합니다.

## 구조

```text
backend/   FastAPI, async SQLAlchemy, Alembic, 테스트, 시드
frontend/  React, Vite, TypeScript
```

## 백엔드 실행

Python 3.10 이상이 필요합니다.

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
```

필수 환경변수를 현재 셸 또는 안전한 비밀 관리 도구에 설정합니다. `.env` 파일은 앱이 자동으로 읽지 않습니다.

```powershell
$env:DATABASE_URL='postgresql+asyncpg://...'
$env:DATABASE_READONLY_URL='postgresql+asyncpg://...'
$env:JWT_SECRET_KEY='...'
$env:SEED_STAFF_PASSWORD='...'
```

선택 기능은 `GEMINI_API_KEY`, `LOCALDATA_API_KEY`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`, `OUTBOUND_EMAIL_MODE`, `TEST_EMAIL_ADDRESS`, `EMAIL_PROVIDER_API_KEY`를 사용합니다.

마이그레이션과 시드는 대상으로 삼을 DB를 확인한 뒤 실행합니다.

```powershell
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe scripts\seed.py
.\.venv\Scripts\uvicorn.exe app.main:app --reload
```

NL2SQL read-only role은 관리자 연결에서 다음처럼 별도로 생성합니다. 비밀번호는 명령행 변수로만 전달합니다.

```powershell
psql $env:DATABASE_URL -v nl2sql_password='...' -f sql/create_readonly_role.sql
```

시드 로그인 이메일은 `manager@directdesk.test`, `rep1@directdesk.test`, `rep2@directdesk.test`, `rep3@directdesk.test`이며 비밀번호는 시드 실행 당시 `SEED_STAFF_PASSWORD` 값입니다.

## 프론트엔드 실행

Node.js 22 이상이 필요합니다.

```powershell
cd frontend
npm install
npm run dev
```

내부 앱은 `http://localhost:5173/`, 공개 문의는 `http://localhost:5173/inquiry`입니다. 개발 서버는 `/api` 요청을 `http://localhost:8000`으로 프록시합니다.

## 검증

```powershell
cd backend
.\.venv\Scripts\ruff.exe check app alembic tests scripts
.\.venv\Scripts\pytest.exe -q

cd ..\frontend
npm run build
npm run lint
```

제품 가격은 2026-08-20 공식 LG전자·Samsung 페이지의 표시 가격을 수동 확인한 고정 데모 데이터입니다. 할인과 재고에 따라 실제 가격은 달라질 수 있으며 실시간 크롤링하지 않습니다.

## 안전 경계

- 공개 채팅은 분석 버튼 전까지 DB에 저장하지 않습니다.
- 공개 응답에는 스코어, 근거, 담당자를 포함하지 않습니다.
- 아웃바운드 기본값은 `dry_run`입니다.
- `test_override`도 수신자를 `TEST_EMAIL_ADDRESS`로만 고정합니다.
- NL2SQL 실행은 별도 read-only 연결만 사용하고 감사로그는 일반 연결로 기록합니다.

