import json
from typing import Any


def intent_prompt(content: str) -> str:
    return (
        "다음 문의 본문만 근거로 영업 의도를 분류하세요. 계정 속성이나 과거 활동은 사용하지 마세요.\n"
        "카테고리는 구매임박, 정보탐색, AS·불만 중 하나입니다. reasoning에는 본문에 실제로 있는 "
        "표현만 인용하거나 요약하세요.\n\n"
        f"문의 본문:\n{content}"
    )


def intake_prompt(messages: list[dict[str, str]], fields: dict[str, Any]) -> str:
    return (
        "당신은 가상 가전 공급사 다온비즈의 상담 접수 도우미입니다. 제품 FAQ에 답하지 말고 상담에 필요한 "
        "업체명, 연락처, 문의내용, 업종, 규모, 필요제품, 수량, 위치를 자연스럽게 한 번에 한두 항목씩 "
        "물어보세요. 이미 받은 정보는 다시 묻지 마세요. JSON으로 message와 fields를 반환하세요.\n\n"
        f"현재 필드: {json.dumps(fields, ensure_ascii=False)}\n"
        f"대화: {json.dumps(messages, ensure_ascii=False)}"
    )


def analysis_prompt(fields: dict[str, Any], products: list[dict[str, Any]]) -> str:
    return (
        "고객 상황에 맞는 짧은 가전 구매 분석을 한국어로 작성하세요. 제공된 제품 데이터 밖의 사양이나 "
        "가격을 만들지 말고 경쟁사를 비하하지 마세요. 왜 추천이 상황에 맞는지 설명하세요.\n\n"
        f"고객 상황: {json.dumps(fields, ensure_ascii=False)}\n"
        f"제품 데이터: {json.dumps(products, ensure_ascii=False, default=str)}"
    )


def nl2sql_prompt(question: str, schema: dict[str, set[str]]) -> str:
    rendered = "\n".join(
        f"- {table}: {', '.join(sorted(columns))}" for table, columns in schema.items()
    )
    return (
        "PostgreSQL 쿼리를 정확히 하나의 SELECT 문으로만 반환하세요. 설명, 마크다운, 주석, CTE(WITH)는 "
        "금지합니다. 아래 화이트리스트 밖의 테이블이나 컬럼을 사용하지 마세요.\n"
        f"{rendered}\n\n질문: {question}"
    )


def outbound_prompt(
    lead: dict[str, Any],
    products: list[dict[str, Any]],
    sequence_step: int,
    previous_draft: dict[str, str] | None = None,
) -> str:
    return (
        "가상 공급사 다온비즈 직원이 검토할 B2B 이메일 초안을 JSON subject/body로 작성하세요. 실제 발송 "
        "전 검토용이며 과장, 없는 사양, 수신 동의가 있다는 표현을 쓰지 마세요.\n"
        f"시퀀스 단계: {sequence_step}\n리드: {json.dumps(lead, ensure_ascii=False, default=str)}\n"
        f"이전 초안: {json.dumps(previous_draft, ensure_ascii=False) if previous_draft else '없음'}\n"
        f"제품: {json.dumps(products, ensure_ascii=False, default=str)}"
    )
