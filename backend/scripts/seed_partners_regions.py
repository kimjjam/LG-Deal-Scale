import asyncio
import csv
from datetime import date
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal, engine
from app.models import Partner, SalesRegion, Staff
from app.schemas import PartnerCreate, normalize_region_text
from app.security import hash_password

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "seed" / "lg_distributors_all.csv"
SOURCE_VERIFIED_AT = date(2026, 8, 25)

REGIONS = (
    ("서울특별시", "서울", "seoul"),
    ("부산광역시", "부산", "busan"),
    ("대구광역시", "대구", "daegu"),
    ("인천광역시", "인천", "incheon"),
    ("광주광역시", "광주", "gwangju"),
    ("대전광역시", "대전", "daejeon"),
    ("울산광역시", "울산", "ulsan"),
    ("세종특별자치시", "세종", "sejong"),
    ("경기도", "경기", "gyeonggi"),
    ("강원특별자치도", "강원", "gangwon"),
    ("충청북도", "충북", "chungbuk"),
    ("충청남도", "충남", "chungnam"),
    ("전북특별자치도", "전북", "jeonbuk"),
    ("전라남도", "전남", "jeonnam"),
    ("경상북도", "경북", "gyeongbuk"),
    ("경상남도", "경남", "gyeongnam"),
    ("제주특별자치도", "제주", "jeju"),
)

SD_REGIONS = {
    "서울특별시": "서울특별시",
    "부산광역시": "부산광역시",
    "대구광역시": "대구광역시",
    "인천광역시": "인천광역시",
    "광주광역시": "광주광역시",
    "대전광역시": "대전광역시",
    "울산광역시": "울산광역시",
    "세종특별자치시": "세종특별자치시",
    "경기도": "경기도",
    "강원도": "강원특별자치도",
    "충청북도": "충청북도",
    "충청남도": "충청남도",
    "전라북도": "전북특별자치도",
    "전라남도": "전라남도",
    "경상북도": "경상북도",
    "경상남도": "경상남도",
    "제주도": "제주특별자치도",
}

AREA_REGIONS = {
    "so": "서울특별시",
    "bs": "부산광역시",
    "dg": "대구광역시",
    "ic": "인천광역시",
    "gj": "광주광역시",
    "dj": "대전광역시",
    "us": "울산광역시",
    "ggd": "경기도",
    "gwd": "강원특별자치도",
    "ccbd": "충청북도",
    "ccnd": "충청남도",
    "jlbd": "전북특별자치도",
    "jlnd": "전라남도",
    "gsbd": "경상북도",
    "gsnd": "경상남도",
    "jjd": "제주특별자치도",
}

STORE_TYPES = {
    "CAC_전시매장": "기타",
    "CAC_공식인증전문점": "전문점",
    "IT가전_B2B공식인증전문점": "전문점",
}


def regional_manager_rows() -> list[dict[str, str]]:
    return [
        {
            "name": f"{keyword} 지역담당 {number:02d}",
            "email": f"region-{slug}-{number:02d}@example.com",
            "region_name": region_name,
            "match_keyword": keyword,
        }
        for region_name, keyword, slug in REGIONS
        for number in range(1, 3)
    ]


def partner_key(partner: PartnerCreate | Partner) -> tuple[str, str, str, str]:
    compact = lambda value: "".join(value.split()).casefold()
    return (
        compact(partner.name),
        compact(partner.address),
        normalize_region_text(partner.region),
        partner.partner_type,
    )


def load_partner_rows(path: Path = SOURCE_PATH) -> list[PartnerCreate]:
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        expected = {
            "storeType",
            "storeName",
            "storeOwner",
            "storeTelNumber",
            "storeAddress",
            "sd",
            "sgg",
            "storeArea",
            "no",
            "rnum",
        }
        if not reader.fieldnames or set(reader.fieldnames) != expected:
            raise ValueError("총판 CSV 헤더가 예상 형식과 다릅니다.")
        partners: list[PartnerCreate] = []
        for row_number, row in enumerate(reader, start=2):
            try:
                store_type = (row["storeType"] or "").strip()
                sd = (row["sd"] or "").strip()
                region = SD_REGIONS[sd] if sd else AREA_REGIONS[(row["storeArea"] or "").strip()]
                partners.append(
                    PartnerCreate(
                        name=row["storeName"],
                        address=row["storeAddress"],
                        phone=row["storeTelNumber"],
                        region=region,
                        partner_type=STORE_TYPES[store_type],  # type: ignore[arg-type]
                        verification_source=f"사용자 제공 매장 데이터 ({store_type})",
                        verified_at=SOURCE_VERIFIED_AT,
                    )
                )
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"총판 CSV {row_number}행을 변환할 수 없습니다.") from error
    keys = [partner_key(item) for item in partners]
    if len(partners) != 519 or len(set(keys)) != len(keys):
        raise ValueError("총판 CSV는 중복 없는 519건이어야 합니다.")
    return partners


async def main() -> None:
    password = get_settings().seed_staff_password
    if not password:
        raise RuntimeError("SEED_STAFF_PASSWORD is required")
    manager_rows = regional_manager_rows()
    partner_rows = load_partner_rows()
    current_emails = {item["email"] for item in manager_rows}
    legacy_emails = {email.replace("@example.com", "@daonbiz.test") for email in current_emails}
    hashed_password = hash_password(password)

    async with SessionLocal() as session, session.begin():
        managers = {
            item.email: item
            for item in (
                await session.scalars(
                    select(Staff).where(Staff.email.in_(current_emails | legacy_emails))
                )
            ).all()
        }
        for item in manager_rows:
            legacy_email = item["email"].replace("@example.com", "@daonbiz.test")
            current_manager = managers.get(item["email"])
            legacy_manager = managers.get(legacy_email)
            if current_manager and legacy_manager and current_manager.id != legacy_manager.id:
                raise RuntimeError(f"duplicate regional manager: {item['name']}")
            manager = current_manager or legacy_manager
            if manager:
                manager.name = item["name"]
                manager.email = item["email"]
                manager.role = "manager"
                manager.is_active = True
            else:
                manager = Staff(
                    name=item["name"],
                    email=item["email"],
                    role="manager",
                    hashed_password=hashed_password,
                )
                session.add(manager)
            managers[item["email"]] = manager
        await session.flush()

        keywords = {item["match_keyword"] for item in manager_rows}
        regions = {
            (item.match_keyword, item.manager_id): item
            for item in (
                await session.scalars(
                    select(SalesRegion).where(SalesRegion.match_keyword.in_(keywords))
                )
            ).all()
        }
        for item in manager_rows:
            manager = managers[item["email"]]
            key = (item["match_keyword"], manager.id)
            region = regions.get(key)
            if region:
                region.region_name = item["region_name"]
                region.is_active = True
            else:
                session.add(
                    SalesRegion(
                        region_name=item["region_name"],
                        match_keyword=item["match_keyword"],
                        manager_id=manager.id,
                    )
                )

        existing_partners = {
            partner_key(item): item for item in (await session.scalars(select(Partner))).all()
        }
        for item in partner_rows:
            partner = existing_partners.get(partner_key(item))
            if partner:
                for field, value in item.model_dump().items():
                    setattr(partner, field, value)
            else:
                session.add(Partner(**item.model_dump()))

    print(
        f"regional_managers={len(manager_rows)} "
        f"sales_regions={len(manager_rows)} partners={len(partner_rows)}"
    )


async def run() -> None:
    try:
        await main()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
