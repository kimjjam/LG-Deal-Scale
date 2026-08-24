from datetime import date
from typing import Any

SERVICE_ID = "03_11_03_P"
SBIZ_STORE_URL = "https://apis.data.go.kr/B553077/api/open/sdsc2/storeListInDong"
SBIZ_LODGING_CODE = "I1"


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


def sbiz_lead_score(business_type: str) -> tuple[int, dict[str, str]]:
    score = 80 if any(word in business_type for word in ("호텔", "모텔", "여관")) else 65
    return score, {
        "business_type": (
            f"공공데이터의 업종 '{business_type}'을 기준으로 숙박업 가전 수요 적합도 "
            f"{score}점을 부여했습니다. 개업일 정보는 제공되지 않아 업력은 반영하지 않았습니다."
        )
    }


def parse_sbiz_rows(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    header = payload.get("header", {})
    code = str(header.get("resultCode") or "").strip()
    if code != "00":
        raise ValueError(header.get("resultMsg") or f"상가정보 API 오류: {code or '응답 코드 없음'}")
    body = payload.get("body", {})
    rows = body.get("items") or []
    if not isinstance(rows, list):
        raise TypeError("body.items는 목록이어야 합니다.")
    parsed = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise TypeError(f"{index}번째 행은 객체여야 합니다.")
        external_id = str(row.get("bizesId") or "").strip()
        name = str(row.get("bizesNm") or "").strip()
        if not external_id or not name:
            raise ValueError(f"{index}번째 행에 상가업소번호 또는 상호명이 없습니다.")
        business_type = str(
            row.get("indsSclsNm") or row.get("indsMclsNm") or row.get("indsLclsNm") or "숙박"
        ).strip()
        score, reasoning = sbiz_lead_score(business_type)
        parsed.append(
            {
                "external_id": external_id,
                "name": name,
                "address": str(row.get("rdnmAdr") or row.get("lnoAdr") or "").strip() or None,
                "business_type": business_type,
                "source": "sbiz",
                "raw_data": dict(row),
                "lead_score": score,
                "lead_score_reasoning": reasoning,
                "lead_scoring_version": "v2",
            }
        )
    return parsed, int(body.get("totalCount") or len(parsed))
