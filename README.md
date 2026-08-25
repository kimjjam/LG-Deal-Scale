# LG Deal Scale

숙박업 소상공인의 인바운드 문의와 아웃바운드 잠재고객을 관리하는 AI CRM 프로토타입입니다.

## 구현 범위

- 공개 채팅형 문의 접수, 제품 데이터 기반 맞춤 분석, 지역 매장 안내
- 고객사·담당자 360도 조회, 문의 상태/의도 보정, 활동 기록과 할 일
- 영업기회 파이프라인, 단계 이력, 금액·확률, 문의/리드의 영업기회 전환
- 문의별 fit/intent/recency 근거와 `fit×0.35 + intent×0.50 + recency×0.15` 영업 우선순위
- 미채점 문의 재시도, 기존 담당자 우선/라운드로빈 자동배정, 관리자 재배정
- owner 전용 read-only DB 연결, SQL 검증·화이트리스트, 성공/실패 감사로그를 사용하는 NL2SQL
- 담당자·연락처·다음 행동일이 있는 잠재고객 작업 목록과 최대 3단계 AI 초안, 검토·안전 발송·실제 접촉 기록
- 고객사·리드 CSV 가져오기/내보내기와 규칙 기반 CRM 성과 대시보드
- 가상 고객·리드와 공식 제품 페이지를 근거로 한 고정 데모 데이터

## 역할과 권한

- `owner`: 직원 생성, 역할·활성 상태·비밀번호 관리와 모든 CRM 기능
- `manager`: 가장 구체적인 활성 지역 매핑에서 본인이 담당하는 고객·문의만 조회·변경, 매핑이 없으면 접근 거부
- `rep`: 자신에게 배정된 고객·문의·영업기회·할 일·리드만 조회하고 담당 기록과 초안을 처리

리드 담당자는 연락처와 다음 행동일을 관리하고, 자신의 리드에 한해 단계 변경·전환·초안 생성/수정/검토/안전 발송·실제 접촉·시퀀스 중단을 수행합니다. 다음 행동일을 지정하면 `follow_up_due`로 이동하며 실제 접촉 또는 후속 초안 생성 시 일정이 해제됩니다.

rep의 대시보드와 고객사 상세 기록은 담당자 소유 범위로 제한됩니다. 같은 고객사를 협업하더라도 문의는 현재 배정분, 활동·영업기회·할 일은 본인 기록만 조회하며 manager와 owner는 전체 기록을 조회합니다.

활성 `sales_regions` 중 고객 위치와 가장 긴 행정구역 접두사로 일치하는 한 건의 manager만 해당 고객·문의를 볼 수 있습니다. 따라서 `서울`과 `서울강남`이 모두 있으면 강남 담당자만 강남 기록을 보며, 매핑이 없는 manager는 전역 권한을 받지 않습니다. NL2SQL은 지역별 행 보안을 적용하지 않으므로 owner만 사용합니다.

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

선택 기능은 `GEMINI_API_KEY`, `DATA_GO_KR_SERVICE_KEY`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`, `OUTBOUND_EMAIL_MODE`, `TEST_EMAIL_ADDRESS`, `EMAIL_PROVIDER_API_KEY`를 사용합니다.

### 마이그레이션과 시드

Alembic 마이그레이션은 PostgreSQL 전용입니다. 아래 명령은 대상 DB와 백업을 확인한 뒤 실행하세요.

`0008`은 기존 `follow_up_due` 단계를 바꾸지 않고 `next_action_at`을 리드의 기존 `created_at`으로 보정해, 마이그레이션 직후 기한이 지난 후속 작업으로 안전하게 노출합니다.

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

화면에서 선택한 CSV 파일은 프론트엔드가 텍스트로 읽어 `{ "csv_text": "..." }` JSON으로 전송합니다. 고객사는 `name,phone`이 필수이고 `attributes`에 JSON 객체를 넣을 수 있습니다. 리드는 `name,lead_score`가 필수이고 `contact_name,contact_phone,contact_email`을 선택적으로 포함할 수 있으며, 가져온 리드는 항상 `discovered` 단계에서 시작합니다.

- 지역 담당 CSV 정확한 헤더: `region_name,match_keyword,staff_email,is_active`
- 파트너 CSV 정확한 헤더: `name,address,phone,region,partner_type,verification_source,verified_at,is_active`
- `is_active`는 `true/false`, 파트너 `partner_type`은 `총판/전문점/기타`, `verified_at`은 `YYYY-MM-DD`입니다. 지역 CSV의 `staff_email`은 이미 등록된 활성 manager로만 해석하며 직원을 생성하지 않습니다.
- 지역 매칭 키워드와 파트너 지역은 `서울특별시 중구`처럼 시·도를 반드시 포함합니다. `중구`처럼 모호한 구·군만으로는 등록하거나 자동 라우팅하지 않습니다.
- 재가져오기 키는 지역의 정규화된 `match_keyword`, 파트너의 정규화된 `name+address+region+partner_type`입니다. 같은 키는 새 행을 만들지 않고 갱신합니다.

- 지역·파트너 가져오기: `owner` 전용, 최대 500행, 한 행이라도 유효하지 않으면 전체를 저장하지 않음
- 내보내기: UTF-8 BOM CSV, 스프레드시트 수식으로 해석될 수 있는 셀을 중립화
- API: `POST /api/accounts/import`, `GET /api/accounts/export.csv`, `POST /api/outbound/leads/import`, `GET /api/outbound/leads/export.csv`, `POST /api/partners-regions/regions/import`, `POST /api/partners-regions/partners/import`

## 대시보드 지표

`/api/crm/dashboard`는 파이프라인 단계별 건수·금액, 월별 예상 수주, `won / (won + lost)` 수주율, 오늘 마감·기한 초과 할 일과 담당자별 성과를 집계합니다. 영업기회 제품 항목이 있으면 수량×저장 단가 합계가 예상 금액이 되며, 제품 마스터 가격 변경 후에도 저장 단가는 유지됩니다.

제품 마스터에서 영업기회 항목을 선택할 때는 30일 이내 HTTPS 출처로 검증된 `wholesale` 사업자 가격만 사용합니다. `retail_reference`만 있는 카탈로그는 현재 내부 단가 선택에서 제외하며, 수동 견적 항목은 계속 입력할 수 있습니다.

고객사 데이터 품질 API는 공백·대소문자를 정규화한 정확 일치 상호명 후보와 같은 고객사 안의 중복 전화·이메일만 경고하며 자동 병합은 하지 않습니다.

아웃바운드 화면은 리드 단계, 초안 검토율, 시퀀스 단계 분포와 현재 안전 발송 모드를 별도로 보여줍니다.

## 공공데이터 잠재고객

잠재고객 화면은 소상공인시장진흥공단 상가(상권)정보 API의 지역별 숙박업소를 페이지당 100건씩 가져옵니다. 상가업소번호로 중복을 막고, API가 개업일을 제공하지 않으므로 리드 점수에는 업종 적합도만 반영합니다.

## 공개 문의 안전 경계

- IP 기준 채팅 60회/시간, 확정 제출 5회/시간 제한을 적용합니다.
- 분석 버튼을 누르기 전에는 문의를 저장하지 않으며, 제출 시 전화번호 정확 일치로 고객사를 연결합니다.
- 기존 고객사의 이름·속성은 공개 제출로 덮어쓰지 않습니다. 재방문 응답은 일치 여부만 알리고 업체명·문의 이력·점수·담당자는 노출하지 않습니다.
- 견적 요청·모델 비교는 제품, 양의 정수 수량과 설치 지역이 모두 있어야 접수할 수 있습니다. 신규 고객사에는 정규화된 `attributes.location`을 저장합니다.
- 공개 결과의 숫자 가격은 30일 이내에 HTTPS 출처로 검증된 사업자 단가 또는 `retail_reference` 공식몰 참고가만 노출합니다. 제품별 수량이 없으므로 총액은 계산하지 않으며 실제 B2B 견적·설치·배송·할인은 상담에서 확정합니다.
- 공개 응답은 지역 담당팀 연결 여부와 검증 파트너 요약만 보여주며 직원 이름·이메일·UUID는 노출하지 않습니다. 파트너는 가장 구체적인 지역 매칭을 선택하고 같은 구체성이면 총판을 우선합니다.
- 공개 파트너 요약은 검증 출처 내용을 노출하지 않고 상호·주소·전화·구분·확인일만 보여줍니다. 다온비즈는 LG전자의 공식·제휴 서비스가 아닌 독립 제품 비교 안내 서비스입니다.
- 제품 안내 문장은 정규화된 업종·구매 단계·정수 수량과 등록 제품명만으로 결정론적으로 생성합니다. 제출 단계에서 업체명·전화번호를 LLM에 보내지 않으며, 대화 중 정보 추출 단계는 현재 누적 필드(업체명·연락처 포함)를 LLM에 전달합니다.
- 같은 고객사·정규화된 문의 내용이 5분 이내 다시 제출되면 기존 문의를 재사용합니다.
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
