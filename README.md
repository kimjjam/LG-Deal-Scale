# LG Deal Scale

가상의 가전 공급사 **LG Deal Scale**이 숙박업 소상공인의 인바운드 문의와 아웃바운드 잠재고객을 관리하는 AI CRM 프로토타입입니다.

## 구현 범위

- 공개 채팅형 문의 접수, 제품 데이터 기반 맞춤 분석, 지역 매장 안내
- 고객사·담당자 360도 조회, 문의 상태/의도 보정, 활동 기록과 할 일
- 영업기회 파이프라인, 단계 이력, 금액·확률, 문의/리드의 영업기회 전환
- 문의별 fit/intent/recency 근거와 `fit×0.35 + intent×0.50 + recency×0.15` 영업 우선순위
- 미채점 문의 재시도, 기존 담당자 우선/라운드로빈 자동배정, 관리자 재배정
- read-only DB 연결, SQL 검증·화이트리스트, 성공/실패 감사로그를 사용하는 NL2SQL
- 잠재고객 검색·단계 관리, 최대 3단계 AI 초안, 수정·검토·안전 발송·실제 접촉 기록
- 고객사·리드 CSV 가져오기/내보내기와 규칙 기반 CRM 성과 대시보드
- 가상 고객·리드와 공식 제품 페이지를 근거로 한 고정 데모 데이터

## 역할과 권한

- `owner`: 직원 생성, 역할·활성 상태·비밀번호 관리와 모든 CRM 기능
- `manager`: 전체 고객/문의/영업기회 관리, 담당자 배정, CSV 가져오기, 아웃바운드 변경 작업
- `rep`: 자신에게 배정된 고객·문의·영업기회·할 일을 중심으로 조회하고 담당 기록을 처리

리드에는 아직 담당자 소유권 모델이 없습니다. 따라서 리드 단계 변경, 전환, 초안 생성·수정·검토·발송, 실제 접촉, 시퀀스 중단은 `owner`/`manager`만 수행할 수 있습니다. 모든 로그인 사용자는 리드와 초안을 조회할 수 있습니다.

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

백엔드는 `backend/.env`를 자동으로 읽으며 현재 셸의 환경변수가 우선합니다. 비밀값은 저장소에 커밋하지 마세요.

```powershell
$env:DATABASE_URL='postgresql+asyncpg://...'
$env:DATABASE_READONLY_URL='postgresql+asyncpg://...'
$env:JWT_SECRET_KEY='32자 이상의 임의 값'
$env:SEED_STAFF_PASSWORD='12자 이상의 데모 비밀번호'
```

선택 기능은 `GEMINI_API_KEY`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`, `OUTBOUND_EMAIL_MODE`, `TEST_EMAIL_ADDRESS`, `EMAIL_PROVIDER_API_KEY`를 사용합니다. `LOCALDATA_API_KEY`는 설정에 존재하지만 현재 실 API 동기화에는 사용하지 않습니다.

### 마이그레이션과 시드

Alembic 마이그레이션은 PostgreSQL 전용입니다. 아래 명령은 대상 DB와 백업을 확인한 뒤 사용자가 직접 실행해야 하며, 이 저장소 작업에서는 적용하지 않았습니다.

```powershell
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe scripts\seed.py
.\.venv\Scripts\uvicorn.exe app.main:app --reload
```

NL2SQL 전용 read-only role은 관리자 연결에서 별도로 생성합니다.

```powershell
psql $env:DATABASE_URL -v nl2sql_password='...' -f sql/create_readonly_role.sql
```

### 최초 owner 생성

마이그레이션 후 owner가 없을 때 한 번만 실행합니다. 이미 owner 또는 같은 이메일의 직원이 있으면 실패합니다.

```powershell
$env:OWNER_NAME='관리자 이름'
$env:OWNER_EMAIL='owner@example.com'
$env:OWNER_PASSWORD='12자 이상의 비밀번호'
.\.venv\Scripts\python.exe scripts\create_owner.py
```

데모 시드는 `manager@directdesk.test`, `rep1@directdesk.test`, `rep2@directdesk.test`, `rep3@directdesk.test`를 만들며 비밀번호는 실행 당시 `SEED_STAFF_PASSWORD`입니다.

## 프론트엔드 실행

Node.js 22 이상이 필요합니다.

```powershell
cd frontend
npm install
npm run dev
```

내부 앱은 `http://localhost:5173/`, 공개 문의는 `http://localhost:5173/inquiry`입니다. 개발 서버는 `/api`를 `http://localhost:8000`으로 프록시합니다.

## CSV 가져오기/내보내기

화면에서 선택한 CSV 파일은 프론트엔드가 텍스트로 읽어 `{ "csv_text": "..." }` JSON으로 전송합니다. 고객사는 `name,phone`이 필수이고 `attributes`에 JSON 객체를 넣을 수 있습니다. 리드는 `name,lead_score`가 필수이며 가져온 리드는 항상 `discovered` 단계에서 시작합니다.

- 가져오기: `owner`/`manager` 전용, 최대 500행, 한 행이라도 유효하지 않으면 전체를 저장하지 않음
- 내보내기: UTF-8 BOM CSV, 스프레드시트 수식으로 해석될 수 있는 셀을 중립화
- API: `POST /api/accounts/import`, `GET /api/accounts/export.csv`, `POST /api/outbound/leads/import`, `GET /api/outbound/leads/export.csv`

## 대시보드 지표

`/api/crm/dashboard`는 파이프라인 단계별 건수·금액, 영업기회 확률을 반영한 가중 금액, `won / (won + lost)` 수주율, 미완료·기한 초과 할 일, 담당자별 활동/영업기회/수주, 현재 체류시간을 포함한 단계별 평균 시간, 영업 우선순위 점수 구간별 종결·수주 결과를 집계합니다. 이는 실제 업무 기록을 계산한 비즈니스 지표이며 AI 정확도·precision·recall 지표가 아닙니다.

아웃바운드 화면은 리드 단계, 초안 검토율, 시퀀스 단계 분포와 현재 안전 발송 모드를 별도로 보여줍니다.

## LOCALDATA 범위

`backend/app/localdata.py`에는 숙박업 서비스 ID `03_11_03_P`의 공식 응답 필드(`bplcNm`, `rdnWhlAddr`, `siteWhlAddr`, `apvPermYmd`, `uptaeNm` 등)를 정규화하는 순수 파서만 있습니다. 오류 응답, 빈 행 형태, 주소 폴백과 날짜를 검증하지만 API 호출·페이지 순회·전체 CSV 최초 적재·증분 동기화는 구현하지 않았습니다.

## 공개 문의 안전 경계

- IP 기준 채팅 60회/시간, 확정 제출 5회/시간 제한을 적용합니다.
- 분석 버튼을 누르기 전에는 문의를 저장하지 않으며, 제출 시 전화번호 정확 일치로 고객사를 연결합니다.
- 기존 고객사의 이름·속성은 공개 제출로 덮어쓰지 않습니다. 재방문 응답은 일치 여부만 알리고 업체명·문의 이력·점수·담당자는 노출하지 않습니다.
- 제품 추천은 등록된 LG 제품 중 문의와 관련된 항목만 사용합니다. 항목이 없거나 분석 생성이 실패해도 문의 저장은 유지됩니다.
- 아웃바운드는 `dry_run`이 기본이며 `test_override`도 수신자를 `TEST_EMAIL_ADDRESS`로 고정합니다. 안전 발송 기록은 실제 접촉으로 간주하지 않습니다.

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
