from datetime import date
from typing import Any

SERVICE_ID = "03_11_03_P"


def _date(value: object) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    for separator in ("-", "."):
        text = text.replace(separator, "")
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"잘못된 인허가일자입니다: {value}")
    return date(int(text[:4]), int(text[4:6]), int(text[6:]))


def parse_localdata_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result", {})
    process = result.get("header", {}).get("process", {})
    code = str(process.get("code") or "").strip()
    if code and code not in {"00", "INFO-000", "SUCCESS"}:
        raise ValueError(process.get("message") or f"LOCALDATA 오류: {code}")
    rows = result.get("body", {}).get("rows", [])
    if rows == {"@class": "list"}:
        return []
    if not isinstance(rows, list):
        raise TypeError("result.body.rows는 목록이어야 합니다.")
    parsed = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise TypeError(f"{index}번째 행은 객체여야 합니다.")
        name = str(row.get("bplcNm") or "").strip()
        if not name:
            raise ValueError(f"{index}번째 행에 bplcNm이 없습니다.")
        parsed.append(
            {
                "name": name,
                "address": (
                    str(row.get("rdnWhlAddr") or "").strip()
                    or str(row.get("siteWhlAddr") or "").strip()
                    or None
                ),
                "license_date": _date(row.get("apvPermYmd")),
                "business_type": row.get("uptaeNm") or None,
                "source": "localdata",
                "management_number": row.get("mgtNo") or None,
                "status_name": row.get("trdStateNm") or None,
                "status_code": row.get("trdStateGbn") or None,
                "detailed_status_name": row.get("dtlStateNm") or None,
                "detailed_status_code": row.get("dtlStateGbn") or None,
                "raw_data": dict(row),
            }
        )
    return parsed
