from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.auth.session import ConnectionManager, SessionData, SessionManager
from app.config import get_settings
from app.db.models import (
    ApprovalProposal,
    AuditEvent,
    ExecutionState,
    OperationExecution,
)
from app.db.session import get_session
from app.execution.engine import ExecutionEngine
from app.policy.engine import PolicyEngine
from app.providers.gmail import GmailProvider

router = APIRouter(tags=['audit'])


class AuditEventResponse(BaseModel):
    id: UUID
    user_id: UUID
    connection_id: UUID | None
    action: str
    tool: str | None
    policy_decision: str
    impact_count: int
    impact_bytes: int
    label_delta_add: list[str]
    label_delta_remove: list[str]
    verification_outcome: str | None
    execution_state: str | None
    created_at: datetime


class ProposalResponse(BaseModel):
    id: UUID
    tool: str
    status: str
    estimated_count: int
    estimated_bytes: int
    label_delta_add: list[str]
    label_delta_remove: list[str]
    expires_at: datetime
    created_at: datetime


class ExecutionResponse(BaseModel):
    id: UUID
    chunk_index: int
    state: str
    attempts: int
    error_message: str | None
    updated_at: datetime


async def get_session_manager() -> SessionManager:
    return SessionManager()


async def get_current_session(
    request: Request,
    session_manager: SessionManager = Depends(get_session_manager),
) -> SessionData:
    settings = get_settings()
    session_id = request.cookies.get(settings.session_cookie_name)
    if not session_id:
        raise HTTPException(status_code=401, detail='Not authenticated')

    session = await session_manager.validate_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail='Session expired or invalid')

    return session


def get_csrf_dependency():
    async def _verify_csrf(request: Request, session: SessionData = Depends(get_current_session)):
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            csrf_token = request.headers.get('X-CSRF-Token')
            if not csrf_token:
                raise HTTPException(status_code=403, detail='CSRF token required')

            session_manager = SessionManager()
            csrf_secret = await session_manager.get_csrf_secret(session.session_id)
            if not csrf_secret or not session_manager.verify_csrf(
                session.session_id, csrf_token, csrf_secret
            ):
                raise HTTPException(status_code=403, detail='Invalid CSRF token')
        return session.csrf_token

    return _verify_csrf


@router.get('/v1/audit-events')
async def list_audit_events(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    action: str | None = Query(None),
    policy_decision: str | None = Query(None),
    session: SessionData = Depends(get_current_session),
):
    """Paginated audit trail for the authenticated user."""
    async with get_session() as db:
        stmt = select(AuditEvent).where(AuditEvent.user_id == session.user_id)
        if action:
            stmt = stmt.where(AuditEvent.action == action)
        if policy_decision:
            stmt = stmt.where(AuditEvent.policy_decision == policy_decision)

        count_stmt = select(AuditEvent.id).where(AuditEvent.user_id == session.user_id)
        if action:
            count_stmt = count_stmt.where(AuditEvent.action == action)
        if policy_decision:
            count_stmt = count_stmt.where(AuditEvent.policy_decision == policy_decision)

        from sqlalchemy import func

        total = (
            await db.execute(select(func.count()).select_from(count_stmt.subquery()))
        ).scalar_one()

        stmt = (
            stmt.order_by(AuditEvent.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = (await db.execute(stmt)).scalars().all()

        return {
            'events': [
                AuditEventResponse(
                    id=e.id,
                    user_id=e.user_id,
                    connection_id=e.connection_id,
                    action=e.action,
                    tool=e.tool,
                    policy_decision=e.policy_decision,
                    impact_count=e.impact_count,
                    impact_bytes=e.impact_bytes,
                    label_delta_add=e.label_delta_add,
                    label_delta_remove=e.label_delta_remove,
                    verification_outcome=e.verification_outcome,
                    execution_state=e.execution_state,
                    created_at=e.created_at,
                )
                for e in rows
            ],
            'total': total,
            'page': page,
            'page_size': page_size,
        }


@router.get('/v1/proposals/{proposal_id}', response_model=ProposalResponse)
async def get_proposal(
    proposal_id: UUID,
    session: SessionData = Depends(get_current_session),
):
    async with get_session() as db:
        stmt = select(ApprovalProposal).where(
            ApprovalProposal.id == proposal_id,
            ApprovalProposal.user_id == session.user_id,
        )
        proposal = (await db.execute(stmt)).scalar_one_or_none()
        if not proposal:
            raise HTTPException(status_code=404, detail='Proposal not found')

        return ProposalResponse(
            id=proposal.id,
            tool=proposal.tool,
            status=proposal.status.value,
            estimated_count=proposal.estimated_count,
            estimated_bytes=proposal.estimated_bytes,
            label_delta_add=proposal.label_delta_add,
            label_delta_remove=proposal.label_delta_remove,
            expires_at=proposal.expires_at,
            created_at=proposal.created_at,
        )


@router.get('/v1/executions/{execution_id}', response_model=ExecutionResponse)
async def get_execution(
    execution_id: UUID,
    session: SessionData = Depends(get_current_session),
):
    """Look up an execution chunk. Ownership is enforced through the connection."""
    async with get_session() as db:
        stmt = (
            select(OperationExecution)
            .join(
                ApprovalProposal, ApprovalProposal.connection_id == OperationExecution.connection_id
            )
            .where(
                OperationExecution.id == execution_id,
                ApprovalProposal.user_id == session.user_id,
            )
        )
        execution = (await db.execute(stmt)).scalar_one_or_none()
        if not execution:
            raise HTTPException(status_code=404, detail='Execution not found')

        return ExecutionResponse(
            id=execution.id,
            chunk_index=execution.chunk_index,
            state=execution.state.value,
            attempts=execution.attempts,
            error_message=execution.error_message,
            updated_at=execution.updated_at,
        )


@router.post('/v1/executions/{execution_id}/undo')
async def undo_execution(
    execution_id: UUID,
    session: SessionData = Depends(get_current_session),
    _csrf: str = Depends(get_csrf_dependency()),
):
    """Restore the prior label state for a verified execution chunk."""
    async with get_session() as db:
        stmt = (
            select(OperationExecution)
            .join(
                ApprovalProposal, ApprovalProposal.connection_id == OperationExecution.connection_id
            )
            .where(
                OperationExecution.id == execution_id,
                ApprovalProposal.user_id == session.user_id,
            )
        )
        execution = (await db.execute(stmt)).scalar_one_or_none()
        if not execution:
            raise HTTPException(status_code=404, detail='Execution not found')
        if execution.state != ExecutionState.VERIFIED:
            raise HTTPException(status_code=409, detail='Only verified executions can be undone')

        connection_manager = ConnectionManager()
        engine = ExecutionEngine(
            connection_manager,
            GmailProvider(connection_manager),
            PolicyEngine(),
        )
        result = await engine.undo_execution(db, execution_id, session.user_id)
        return {
            'state': result.state.value,
            'verified_count': result.verified_count,
            'failed_count': result.failed_count,
        }
