"""Authenticated development staff inbox, replies and conversation controls."""

from datetime import datetime, timezone
import os
from secrets import compare_digest
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, StringConstraints
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from clinicflow.database import session_scope
from clinicflow.handoff import lock_conversation, resume_bot
from clinicflow.models import (
    AgentRun, Conversation, ConversationMessage, HandoffCase, InboxMessage, OutboxMessage, Tenant,
)

router = APIRouter(prefix="/staff", tags=["staff"])


class StaffReply(BaseModel):
    request_id: UUID
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


def _staff_tenant(authorization: Annotated[str | None, Header()] = None) -> int:
    token = os.getenv("STAFF_DEV_TOKEN", "")
    slug = os.getenv("STAFF_DEV_TENANT_SLUG", "")
    if len(token) < 32 or not slug:
        raise HTTPException(404)
    if not authorization or not compare_digest(
        authorization.encode(), f"Bearer {token}".encode()
    ):
        raise HTTPException(401, headers={"WWW-Authenticate": "Bearer"})
    with session_scope() as session:
        tenant_id = session.scalar(select(Tenant.id).where(Tenant.slug == slug))
    if tenant_id is None:
        raise HTTPException(404)
    return tenant_id


StaffTenant = Annotated[int, Depends(_staff_tenant)]


@router.get("/handoffs")
def list_open_cases(tenant_id: StaffTenant, response: Response):
    response.headers["Cache-Control"] = "no-store"
    with session_scope() as session:
        cases = session.scalars(select(HandoffCase).where(
            HandoffCase.tenant_id == tenant_id, HandoffCase.closed_at.is_(None),
        ).order_by(HandoffCase.created_at.desc()).limit(100)).all()
        return [
            {"id": case.id, "reason": case.reason, "created_at": case.created_at}
            for case in cases
        ]


@router.get("/handoffs/{case_id}")
def case_details(tenant_id: StaffTenant, case_id: int, response: Response):
    response.headers["Cache-Control"] = "no-store"
    with session_scope() as session:
        case = _case(session, tenant_id, case_id)
        messages = session.scalars(select(ConversationMessage).where(
            ConversationMessage.tenant_id == tenant_id,
            ConversationMessage.conversation_id == case.conversation_id,
        ).order_by(ConversationMessage.occurred_at.desc(), ConversationMessage.id.desc()).limit(10))
        recent = [
            {
                "direction": message.direction, "at": message.occurred_at,
                "message": (message.transcript or message.body or "")[:500],
            }
            for message in messages
        ]
        return {
            "id": case.id, "reason": case.reason, "closed_at": case.closed_at,
            "recent_messages": list(reversed(recent)),
        }


@router.post("/handoffs/{case_id}/reply", status_code=202)
def reply_to_case(tenant_id: StaffTenant, case_id: int, reply: StaffReply):
    with session_scope() as session:
        case = _case(session, tenant_id, case_id)
        conversation = lock_conversation(session, tenant_id, case.conversation_id)
        if conversation is None or case.closed_at or conversation.status != "human":
            raise HTTPException(409, "The case is not active")
        phone_number_id = session.scalar(select(InboxMessage.phone_number_id).where(
            InboxMessage.tenant_id == tenant_id,
            InboxMessage.conversation_id == conversation.id,
            InboxMessage.message_type != "staff",
        ).order_by(InboxMessage.id.desc()).limit(1))
        if not phone_number_id:
            raise HTTPException(409, "This conversation has no inbound route")
        now = datetime.now(timezone.utc)
        inbound_id = session.scalar(
            insert(InboxMessage).values(
                tenant_id=tenant_id, conversation_id=conversation.id,
                message_id=f"staff:{tenant_id}:{case_id}:{reply.request_id}",
                phone_number_id=phone_number_id, sender_id=conversation.customer_wa_id,
                message_type="staff", body=reply.text, message_timestamp=now,
                payload={}, status="processed", processed_at=now,
            ).on_conflict_do_nothing(constraint="uq_inbox_messages_message_id").returning(InboxMessage.id)
        )
        if inbound_id is not None:
            session.add(OutboxMessage(
                inbox_message_id=inbound_id, phone_number_id=phone_number_id,
                recipient_id=conversation.customer_wa_id, body=reply.text,
                message_type="text", allow_during_handoff=True,
            ))
    return {"queued": inbound_id is not None}


@router.get("/conversations/{conversation_id}/traces")
def conversation_traces(tenant_id: StaffTenant, conversation_id: int, response: Response):
    response.headers["Cache-Control"] = "no-store"
    with session_scope() as session:
        exists = session.scalar(select(Conversation.id).where(
            Conversation.tenant_id == tenant_id, Conversation.id == conversation_id,
        ))
        if exists is None:
            raise HTTPException(404)
        traces = session.scalars(select(AgentRun).where(
            AgentRun.tenant_id == tenant_id, AgentRun.conversation_id == conversation_id,
        ).order_by(AgentRun.id.desc()).limit(50))
        return [{
            "id": run.id, "status": run.status, "reason": run.guardrail_reason,
            "model": run.model_name, "prompt_version": run.prompt_version,
            "tools": [
                {"name": event["name"], "ok": event["error"] is None}
                for event in run.tool_events
            ],
            "input_tokens": run.input_tokens, "output_tokens": run.output_tokens,
            "latency_ms": run.latency_ms, "cost_usd": str(run.cost_usd),
        } for run in traces]


@router.post("/handoffs/{case_id}/resume", status_code=204)
def resume_case(tenant_id: StaffTenant, case_id: int) -> Response:
    with session_scope() as session:
        case = _case(session, tenant_id, case_id)
        conversation = lock_conversation(session, tenant_id, case.conversation_id)
        session.refresh(case)
        if conversation is not None and case.closed_at is None and conversation.status == "human":
            resume_bot(session, conversation, datetime.now(timezone.utc))
    return Response(status_code=204)


def _case(session: Session, tenant_id: int, case_id: int) -> HandoffCase:
    case = session.scalar(select(HandoffCase).where(
        HandoffCase.id == case_id, HandoffCase.tenant_id == tenant_id,
    ))
    if case is None:
        raise HTTPException(404, "Handoff case not found")
    return case
