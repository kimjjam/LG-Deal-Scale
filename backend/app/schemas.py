import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    role: Literal["manager", "rep"]
    name: str


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    phone: str = Field(min_length=7, max_length=30)
    attributes: dict[str, Any] = Field(default_factory=dict)


class AccountResponse(AccountCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


class ContactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    role: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=30)
    email: EmailStr | None = None


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
    product: str | None = None
    quantity: int | None = Field(default=None, ge=1, le=100_000)
    location: str | None = None


class ChatTurnRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=30)
    fields: IntakeFields = Field(default_factory=IntakeFields)


class ChatTurnResponse(BaseModel):
    message: str
    fields: IntakeFields
    ready_for_analysis: bool
    returning_business_name: str | None = None


class PublicSubmissionRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=30)
    fields: IntakeFields


class ProductRecommendation(BaseModel):
    name: str
    brand: str
    price: float
    product_url: str


class PublicSubmissionResponse(BaseModel):
    inquiry_id: int
    confirmation: str
    analysis: str | None
    analysis_error: bool = False
    products: list[ProductRecommendation] = Field(default_factory=list)
    stores: list[dict[str, str]] = Field(default_factory=list)


class ManualAssignmentRequest(BaseModel):
    assignee_id: uuid.UUID


class IntentResult(BaseModel):
    category: Literal["구매임박", "정보탐색", "AS·불만"]
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
    role: Literal["manager", "rep"]
