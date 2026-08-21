from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Staff


def record_audit(
    session: AsyncSession,
    actor: Staff,
    action: str,
    resource_type: str,
    resource_id: object,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            actor_id=actor.id,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id),
            details=details or {},
        )
    )
