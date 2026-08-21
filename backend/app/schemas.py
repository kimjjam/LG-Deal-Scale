import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

ActivityType = Literal["call", "email", "meeting", "note", "purchase"]
InquiryStatus = Literal["open", "routed", "resolved"]
IntentCategory = Literal["구매임박", "정보탐색", "AS·불만"]
PurchaseStage = Literal["견적 요청", "모델 비교", "정보 수집"]
PurchaseTiming = Literal["즉시", "1개월 이내", "3개월 이내", "미정"]
NearbyStoreStatus = Literal["location_missing", "not_configured", "failed", "no_results", "success"]
OpportunityStage = Literal["qualify", "develop", "propose", "won", "lost"]
TaskStatus = Literal["pending", "completed"]
OPPORTUNITY_AMOUNT_MAX = Decimal("999999999999.99")
REGION_ALIASES = {
    "서울특별시": "서울",
    "서울시": "서울",
    "부산광역시": "부산",
    "대구광역시": "대구",
    "인천광역시": "인천",
    "광주광역시": "광주",
    "대전광역시": "대전",
    "울산광역시": "울산",
    "세종특별자치시": "세종",
    "경기도": "경기",
}
try:
    SEOUL_TIMEZONE = ZoneInfo("Asia/Seoul")
except ZoneInfoNotFoundError:
    # ponytail: Windows may omit IANA data; modern Korean business time is fixed UTC+9.
    SEOUL_TIMEZONE = timezone(timedelta(hours=9))


def normalize_phone(value: str) -> str:
    normalized = "".join(character for character in value if character in "0123456789")
    if not 7 <= len(normalized) <= 15:
        raise ValueError("연락처는 숫자 7~15자리여야 합니다.")
    return normalized


def strip_nonblank(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("문자열을 입력해주세요.")  # noqa: TRY004 - Pydantic converts to 422
    stripped = value.strip()
    if not stripped:
        raise ValueError("공백만 입력할 수 없습니다.")
    return stripped


def normalize_region_text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("지역을 입력해주세요.")  # noqa: TRY004 - Pydantic converts to 422
    normalized = "".join(value.split()).casefold()
    for official, short in REGION_ALIASES.items():
        normalized = normalized.replace(official.casefold(), short)
    return normalized


def seoul_business_date(now: datetime | None = None) -> date:
    instant = now or datetime.now(timezone.utc)
    return instant.astimezone(SEOUL_TIMEZONE).date()


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    role: Literal["owner", "manager", "rep"]
    name: str


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    phone: str = Field(min_length=7, max_length=30)
    attributes: dict[str, Any] = Field(default_factory=dict)

    _normalize_phone = field_validator("phone", mode="before")(normalize_phone)


class AccountResponse(AccountCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


class ContactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    role: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=30)
    email: EmailStr | None = None

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_optional_phone(cls, value: str | None) -> str | None:
        return normalize_phone(value) if value else value


class ContactResponse(ContactCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int


class InquiryCreate(BaseModel):
    account_id: int
    channel: str = Field(default="staff", max_length=30)
    content: str = Field(min_length=1, max_length=10_000)


class InquiryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    channel: str
    content: str
    status: str
    created_at: datetime


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4_000)


class IntakeFields(BaseModel):
    business_name: str | None = None
    phone: str | None = None
    inquiry: str | None = None
    business_type: str | None = None
    room_count: int | None = Field(default=None, ge=0, le=100_000)
    seat_count: int | None = Field(default=None, ge=0, le=100_000)
    employee_count: int | None = Field(default=None, ge=0, le=100_000)
    store_count: int | None = Field(default=None, ge=0, le=100_000)
    product: str | None = None
    quantity: int | None = Field(default=None, ge=1, le=100_000)
    location: str | None = None
    purchase_stage: PurchaseStage | None = None
    purchase_timing: PurchaseTiming | None = None

    @field_validator("business_name", "inquiry", "business_type", mode="before")
    @classmethod
    def strip_required_text(cls, value: Any) -> Any:
        if value is None:
            return None
        return strip_nonblank(value)

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_optional_phone(cls, value: str | None) -> str | None:
        return normalize_phone(value) if value else value

    @field_validator(
        "room_count", "seat_count", "employee_count", "store_count", "quantity", mode="before"
    )
    @classmethod
    def reject_boolean_counts(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("count must be a number")  # noqa: TRY004 - Pydantic returns 422
        return value


class ChatTurnRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=30)
    fields: IntakeFields = Field(default_factory=IntakeFields)


class ChatTurnResponse(BaseModel):
    message: str
    fields: IntakeFields
    ready_for_analysis: bool
    returning_customer: bool = False


class PublicSubmissionRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=30)
    fields: IntakeFields


class ProductRecommendation(BaseModel):
    name: str
    brand: str
    price: float | None
    price_label: str
    price_source_url: str | None = None
    price_verified_at: date | None = None
    product_url: str


class NearbyStore(BaseModel):
    name: str
    address: str
    phone: str


class PublicSubmissionResponse(BaseModel):
    inquiry_id: int
    confirmation: str
    analysis: str | None
    analysis_error: bool = False
    products: list[ProductRecommendation] = Field(default_factory=list)
    stores: list[NearbyStore] = Field(default_factory=list)
    nearby_store_status: NearbyStoreStatus
    nearby_store_message: str


class ManualAssignmentRequest(BaseModel):
    assignee_id: uuid.UUID


class PartnerLinkRequest(BaseModel):
    partner_id: int | None = Field(default=None, ge=1)


class SalesRegionCreate(BaseModel):
    region_name: str = Field(min_length=1, max_length=100)
    match_keyword: str = Field(min_length=1, max_length=100)
    manager_id: uuid.UUID
    is_active: bool = True

    _strip_name = field_validator("region_name", mode="before")(strip_nonblank)
    _normalize_keyword = field_validator("match_keyword", mode="before")(normalize_region_text)


class SalesRegionUpdate(BaseModel):
    region_name: str | None = Field(default=None, min_length=1, max_length=100)
    match_keyword: str | None = Field(default=None, min_length=1, max_length=100)
    manager_id: uuid.UUID | None = None
    is_active: bool | None = None

    _strip_name = field_validator("region_name", mode="before")(strip_nonblank)
    _normalize_keyword = field_validator("match_keyword", mode="before")(normalize_region_text)

    @model_validator(mode="after")
    def reject_null_values(self) -> "SalesRegionUpdate":
        if any(value is None for field, value in self.model_dump(exclude_unset=True).items()):
            raise ValueError("수정할 지역 값은 null일 수 없습니다.")
        return self


class PartnerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    address: str = Field(min_length=1, max_length=500)
    phone: str | None = Field(default=None, max_length=30)
    region: str = Field(min_length=1, max_length=100)
    partner_type: Literal["총판", "전문점", "기타"]
    verification_source: str = Field(min_length=1, max_length=200)
    verified_at: date
    is_active: bool = True

    _strip_strings = field_validator(
        "name", "address", "region", "verification_source", mode="before"
    )(strip_nonblank)

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_partner_phone(cls, value: str | None) -> str | None:
        return value.strip() or None if isinstance(value, str) else value

    @field_validator("verified_at")
    @classmethod
    def reject_future_verification(cls, value: date) -> date:
        if value > seoul_business_date():
            raise ValueError("검증일은 미래일 수 없습니다.")
        return value


class PartnerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    address: str | None = Field(default=None, min_length=1, max_length=500)
    phone: str | None = Field(default=None, max_length=30)
    region: str | None = Field(default=None, min_length=1, max_length=100)
    partner_type: Literal["총판", "전문점", "기타"] | None = None
    verification_source: str | None = Field(default=None, min_length=1, max_length=200)
    verified_at: date | None = None
    is_active: bool | None = None

    _strip_strings = field_validator(
        "name", "address", "region", "verification_source", mode="before"
    )(strip_nonblank)

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_partner_phone(cls, value: str | None) -> str | None:
        return value.strip() or None if isinstance(value, str) else value

    @field_validator("verified_at")
    @classmethod
    def reject_future_verification(cls, value: date | None) -> date | None:
        if value and value > seoul_business_date():
            raise ValueError("검증일은 미래일 수 없습니다.")
        return value

    @model_validator(mode="after")
    def reject_null_required_values(self) -> "PartnerUpdate":
        values = self.model_dump(exclude_unset=True)
        if any(value is None for field, value in values.items() if field != "phone"):
            raise ValueError("수정할 파트너 값은 null일 수 없습니다.")
        return self


class PartnerResponse(PartnerCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


class IntentResult(BaseModel):
    category: IntentCategory
    confidence: float = Field(ge=0, le=1)
    reasoning: str = Field(min_length=1, max_length=1000)


class SearchRequest(BaseModel):
    question: str = Field(min_length=2, max_length=1000)


class SearchResponse(BaseModel):
    sql: str
    rows: list[dict[str, Any]]


class LeadStageRequest(BaseModel):
    pipeline_stage: Literal[
        "discovered",
        "draft_generated",
        "approved",
        "contacted",
        "follow_up_due",
        "converted",
        "dropped",
    ]


class StaffIdentity(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: EmailStr
    role: Literal["owner", "manager", "rep"]
    is_active: bool


class StaffCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    role: Literal["manager", "rep"]
    password: str = Field(min_length=12, max_length=128)


class StaffRoleUpdate(BaseModel):
    role: Literal["manager", "rep"]


class StaffActiveUpdate(BaseModel):
    is_active: bool


class StaffPasswordReset(BaseModel):
    password: str = Field(min_length=12, max_length=128)


class OpportunityCreate(BaseModel):
    account_id: int
    assignee_id: uuid.UUID
    title: str = Field(min_length=1, max_length=200)
    inquiry_id: int | None = None
    lead_id: int | None = None
    amount: Decimal | None = Field(
        default=None, ge=0, le=OPPORTUNITY_AMOUNT_MAX, max_digits=14, decimal_places=2
    )
    probability: int = Field(default=10, ge=0, le=100)
    expected_close_date: date | None = None
    stage: OpportunityStage = "qualify"
    loss_reason: str | None = Field(default=None, max_length=500)


class OpportunityUpdate(BaseModel):
    assignee_id: uuid.UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    amount: Decimal | None = Field(
        default=None, ge=0, le=OPPORTUNITY_AMOUNT_MAX, max_digits=14, decimal_places=2
    )
    probability: int | None = Field(default=None, ge=0, le=100)
    expected_close_date: date | None = None
    stage: OpportunityStage | None = None
    loss_reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def reject_null_required_fields(self) -> "OpportunityUpdate":
        for field in ("assignee_id", "title", "stage", "probability"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field}에는 null을 사용할 수 없습니다.")
        return self


class OpportunityResponse(OpportunityCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class ActivityCreate(BaseModel):
    account_id: int
    type: ActivityType
    staff_id: uuid.UUID | None = None
    contact_id: int | None = None
    inquiry_id: int | None = None
    opportunity_id: int | None = None
    content: str | None = Field(default=None, max_length=10_000)
    outcome: str | None = Field(default=None, max_length=200)
    amount: Decimal | None = Field(default=None, ge=0)


class ActivityResponse(ActivityCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


class TaskCreate(BaseModel):
    account_id: int
    assignee_id: uuid.UUID
    title: str = Field(min_length=1, max_length=200)
    due_at: datetime
    opportunity_id: int | None = None
    inquiry_id: int | None = None


class TaskUpdate(BaseModel):
    assignee_id: uuid.UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    due_at: datetime | None = None
    status: TaskStatus | None = None

    @model_validator(mode="after")
    def reject_null_required_fields(self) -> "TaskUpdate":
        for field in ("assignee_id", "title", "due_at", "status"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field}에는 null을 사용할 수 없습니다.")
        return self


class TaskResponse(TaskCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: TaskStatus
    completed_at: datetime | None
    created_at: datetime


class InquiryStatusRequest(BaseModel):
    status: InquiryStatus


class InquiryConversionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    amount: Decimal | None = Field(
        default=None, ge=0, le=OPPORTUNITY_AMOUNT_MAX, max_digits=14, decimal_places=2
    )
    probability: int = Field(default=10, ge=0, le=100)
    expected_close_date: date | None = None


class IntentCorrectionRequest(BaseModel):
    category: IntentCategory
    confidence: float = Field(default=1, ge=0, le=1)
    reasoning: str = Field(min_length=1, max_length=1000)


class LeadConversionRequest(BaseModel):
    phone: str = Field(min_length=7, max_length=30)
    assignee_id: uuid.UUID
    account_name: str | None = Field(default=None, min_length=1, max_length=200)
    opportunity_title: str = Field(min_length=1, max_length=200)
    amount: Decimal | None = Field(
        default=None, ge=0, le=OPPORTUNITY_AMOUNT_MAX, max_digits=14, decimal_places=2
    )

    _normalize_phone = field_validator("phone", mode="before")(normalize_phone)


class DraftEditRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=20_000)


class ManualContactRequest(BaseModel):
    channel: Literal["phone", "email", "meeting", "other"]
    note: str | None = Field(default=None, max_length=1000)


class CsvTextRequest(BaseModel):
    csv_text: str = Field(min_length=1, max_length=2_000_000)
