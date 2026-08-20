import asyncio
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.llm import get_llm_client

ROOT = Path(__file__).resolve().parents[1]


class GeneratedInquiry(BaseModel):
    content: str
    intent_category: Literal["구매임박", "정보탐색", "AS·불만"]


class GeneratedAccount(BaseModel):
    name: str
    phone: str
    attributes: dict[str, str | int]
    inquiry: GeneratedInquiry


class GeneratedAccounts(BaseModel):
    accounts: list[GeneratedAccount] = Field(min_length=24, max_length=24)


async def main() -> None:
    prompt = """가상의 가전 공급사 CRM 데모용 숙박업 계정 24개를 한국어로 생성하세요.
실제 업체명이나 실제 전화번호를 쓰지 말고 업체명은 모두 '가상 '으로 시작하며 전화번호는 010-9000-0001부터 순서대로 씁니다.
attributes에는 business_type(호텔/모텔/펜션/게스트하우스), room_count, renovation_status를 넣으세요.
문의는 구매임박, 정보탐색, AS·불만 각 8개씩이며 intent_category와 실제 문장 의미가 일치해야 합니다."""
    generated = await get_llm_client().structured(prompt, GeneratedAccounts)
    destination = ROOT / "seed" / "generated_seed.json"
    existing = json.loads(destination.read_text(encoding="utf-8")) if destination.exists() else {}
    existing["accounts"] = [account.model_dump() for account in generated.accounts]
    destination.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())

