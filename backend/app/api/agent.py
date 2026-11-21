import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent.coordinator import AgentCoordinator
from app.auth.session import SessionData, SessionManager
from app.config import get_settings

router = APIRouter(prefix='/v1/agent', tags=['agent'])


class RunAgentInput(BaseModel):
    thread_id: UUID | None = None
    messages: list[dict] = Field(default_factory=list)
    state: dict | None = None
    tools: list[dict] | None = None
    context: dict | None = None
    resume: list[dict] | None = None


class RunStateResponse(BaseModel):
    run_id: UUID
    thread_id: UUID
    status: str
    outcome: dict | None = None
    created_at: datetime
    completed_at: datetime | None = None


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


async def get_agent_coordinator(
    session: SessionData = Depends(get_current_session),
) -> AgentCoordinator:
    return AgentCoordinator(user_id=session.user_id)


@router.post('/runs')
async def start_run(
    input_data: RunAgentInput,
    request: Request,
    coordinator: AgentCoordinator = Depends(get_agent_coordinator),
    session: SessionData = Depends(get_current_session),
    _csrf: str = Depends(get_csrf_dependency()),
):
    """Start a run and stream AG-UI events as Server-Sent Events."""
    run = await coordinator.create_run(
        thread_id=input_data.thread_id,
        messages=input_data.messages,
        state=input_data.state,
        tools=input_data.tools,
        context=input_data.context,
        resume=input_data.resume,
    )

    start_seq = await coordinator.current_seq(run.id)

    return StreamingResponse(
        _stream_run(coordinator, run.id, input_data, start_seq),
        media_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',
        },
    )


async def _stream_run(
    coordinator: AgentCoordinator,
    run_id: UUID,
    input_data: RunAgentInput,
    start_seq: int,
) -> AsyncGenerator[str, None]:
    """Run the agent and stream events as they are persisted."""
    task = asyncio.create_task(
        coordinator.execute_run(run_id, input_data.resume, input_data.messages)
    )

    last_seq = start_seq
    try:
        while True:
            await asyncio.sleep(0.25)
            events = await coordinator.get_events(run_id, after=last_seq)
            for event in events:
                last_seq = event['seq']
                yield _sse(event['seq'], event['event_type'], event['payload'])

            run = await coordinator.get_run(run_id)
            if run and run.status.value != 'running':
                events = await coordinator.get_events(run_id, after=last_seq)
                for event in events:
                    yield _sse(event['seq'], event['event_type'], event['payload'])
                break
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def _sse(seq: int, event_type: str, payload: dict) -> str:
    return f'id: {seq}\nevent: {event_type}\ndata: {json.dumps(payload)}\n\n'


@router.get('/runs/{run_id}', response_model=RunStateResponse)
async def get_run(
    run_id: UUID,
    coordinator: AgentCoordinator = Depends(get_agent_coordinator),
    session: SessionData = Depends(get_current_session),
):
    """Get run state."""
    run = await coordinator.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail='Run not found')
    return RunStateResponse(
        run_id=run.id,
        thread_id=run.thread_id,
        status=run.status.value,
        outcome=run.outcome_payload,
        created_at=run.created_at,
        completed_at=run.completed_at,
    )


@router.get('/runs/{run_id}/events')
async def get_run_events(
    run_id: UUID,
    request: Request,
    after: int | None = None,
    coordinator: AgentCoordinator = Depends(get_agent_coordinator),
    session: SessionData = Depends(get_current_session),
):
    """Replay run events as JSON (used for reconnection/history)."""
    if after is None and request is not None:
        last_event_id = request.headers.get('Last-Event-ID')
        if last_event_id and last_event_id.isdigit():
            after = int(last_event_id)

    events = await coordinator.get_events(run_id, after=after)
    return {'events': events}


@router.post('/runs/{run_id}/cancel')
async def cancel_run(
    run_id: UUID,
    coordinator: AgentCoordinator = Depends(get_agent_coordinator),
    session: SessionData = Depends(get_current_session),
    _csrf: str = Depends(get_csrf_dependency()),
):
    """Cancel a running agent run."""
    await coordinator.cancel_run(run_id)
    return {'status': 'cancelled'}
