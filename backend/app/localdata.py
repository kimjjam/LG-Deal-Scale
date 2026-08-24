from datetime import date, datetime, timedelta, timezone
from typing import Any

SERVICE_ID = "03_11_03_P"
SBIZ_STORE_URL = "https://apis.data.go.kr/B553077/api/open/sdsc2/storeListInDong"
BUILDING_TITLE_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"
BUILDING_PERMIT_URL = "https://apis.data.go.kr/1613000/ArchPmsHubService/getApBasisOulnInfo"


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


def sbiz_lead_score() -> tuple[int, dict[str, str]]:
    return 50, {
        "source_data": (
            "상가정보로 존재와 업종을 확인했습니다. 업종만으로 노후도를 추정하지 않아 "
            "건축물대장 확인 전에는 중립 점수 50점을 적용합니다."
        )
    }


def building_query_from_sbiz(raw_data: dict[str, Any]) -> dict[str, str | int]:
    legal_code = str(raw_data.get("ldongCd") or "").strip()
    main_number = str(raw_data.get("lnoMnno") or "").strip()
    sub_number = str(raw_data.get("lnoSlno") or "0").strip()
    if len(legal_code) != 10 or not legal_code.isdigit():
        raise ValueError("상가정보에 10자리 법정동 코드가 없습니다.")
    if not main_number.isdigit() or not sub_number.isdigit():
        raise ValueError("상가정보에 유효한 지번이 없습니다.")
    mountain = str(raw_data.get("lnoCd") or "") == "2" or " 산" in str(
        raw_data.get("lnoAdr") or ""
    )
    return {
        "sigunguCd": legal_code[:5],
        "bjdongCd": legal_code[5:],
        "platGbCd": "1" if mountain else "0",
        "bun": main_number.zfill(4),
        "ji": sub_number.zfill(4),
        "numOfRows": 100,
        "pageNo": 1,
        "_type": "json",
    }


def parse_building_title(payload: dict[str, Any]) -> dict[str, Any]:
    response = payload.get("response", {})
    header = response.get("header", {})
    code = str(header.get("resultCode") or "").strip()
    if code != "00":
        raise ValueError(header.get("resultMsg") or f"건축물대장 API 오류: {code or '응답 코드 없음'}")
    item = response.get("body", {}).get("items", {}).get("item", [])
    rows = [item] if isinstance(item, dict) else item
    if not isinstance(rows, list):
        raise TypeError("건축물대장 응답 항목은 목록이어야 합니다.")
    candidates = [row for row in rows if isinstance(row, dict) and row.get("useAprDay")]
    if not candidates:
        raise ValueError("해당 지번의 사용승인일을 찾지 못했습니다.")

    def rank(row: dict[str, Any]) -> tuple[bool, float]:
        try:
            area = float(row.get("totArea") or 0)
        except (TypeError, ValueError):
            area = 0
        return row.get("mainAtchGbCdNm") == "주건축물", area

    selected = max(candidates, key=rank)
    approval_date = _date(selected["useAprDay"])
    if approval_date is None:
        raise ValueError("사용승인일을 확인할 수 없습니다.")
    return {
        "approval_date": approval_date.isoformat(),
        "building_name": selected.get("bldNm") or None,
        "main_purpose": selected.get("mainPurpsCdNm") or None,
        "total_area": selected.get("totArea") or None,
        "source_row": selected,
    }


def parse_building_permits(payload: dict[str, Any]) -> list[dict[str, Any]]:
    response = payload.get("response", {})
    header = response.get("header", {})
    code = str(header.get("resultCode") or "").strip()
    if code != "00":
        raise ValueError(header.get("resultMsg") or f"건축인허가 API 오류: {code or '응답 코드 없음'}")
    item = response.get("body", {}).get("items", {}).get("item", [])
    rows = [item] if isinstance(item, dict) else item
    if not isinstance(rows, list):
        raise TypeError("건축인허가 응답 항목은 목록이어야 합니다.")
    permits = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("archGbCdNm") or "").strip()
        if not any(word in kind for word in ("대수선", "증축", "개축", "재축", "용도변경")):
            continue
        date_field = next(
            (
                field
                for field in ("useAprDay", "realStcnsDay", "archPmsDay", "crtnDay")
                if row.get(field)
            ),
            None,
        )
        event_date = _date(row.get(date_field)) if date_field else None
        permits.append(
            {
                "kind": kind,
                "date": event_date.isoformat() if event_date else None,
                "date_basis": date_field,
                "building_name": row.get("bldNm") or None,
                "source_row": row,
            }
        )
    return sorted(permits, key=lambda permit: str(permit["date"] or ""), reverse=True)


def building_age_score(approval_date: date, today: date | None = None) -> tuple[int, str]:
    current = today or datetime.now(timezone.utc).date()
    age = current.year - approval_date.year - (
        (current.month, current.day) < (approval_date.month, approval_date.day)
    )
    if age >= 30:
        score = 85
    elif age >= 20:
        score = 70
    elif age >= 10:
        score = 55
    else:
        score = 35
    return score, (
        f"건축물대장 사용승인일 {approval_date.isoformat()} 기준 건물 연식은 {age}년입니다. "
        f"내부 상태를 확정할 수 없어 리모델링 필요가 아닌 교체·리모델링 잠재력 {score}점으로 산정했습니다."
    )


def apply_recent_major_repair(
    base_score: int, permits: list[dict[str, Any]], today: date | None = None
) -> tuple[int, str]:
    current = today or datetime.now(timezone.utc).date()
    cutoff = current - timedelta(days=365 * 5)
    recent = [
        permit
        for permit in permits
        if "대수선" in str(permit.get("kind") or "")
        and permit.get("date")
        and date.fromisoformat(str(permit["date"])) >= cutoff
    ]
    if not recent:
        return base_score, (
            "최근 5년 내 공식 대수선 기록을 찾지 못해 건물 연식 점수를 유지했습니다. "
            "기록이 없다는 사실이 내부 리모델링을 하지 않았다는 뜻은 아닙니다."
        )
    latest = recent[0]
    score = max(20, base_score - 30)
    return score, (
        f"{latest['date']} 공식 대수선 기록을 확인해 건물 연식 점수 {base_score}점에서 "
        f"30점을 낮춘 {score}점으로 산정했습니다."
    )


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
            row.get("indsSclsNm")
            or row.get("indsMclsNm")
            or row.get("indsLclsNm")
            or "업종 미상"
        ).strip()
        score, reasoning = sbiz_lead_score()
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
