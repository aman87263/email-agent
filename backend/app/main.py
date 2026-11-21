import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import agent, audit, auth, connections
from app.config import get_settings
from app.db.session import close_db, init_db

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    settings = get_settings()
    if settings.environment != 'test':
        await init_db()
        # Resolve any provider write whose process died after submission. This
        # only reads current Gmail labels; it never retries an unknown write.
        from app.auth.session import ConnectionManager
        from app.db.session import get_session
        from app.execution.engine import ExecutionEngine
        from app.policy.engine import PolicyEngine
        from app.providers.gmail import GmailProvider

        manager = ConnectionManager()
        executor = ExecutionEngine(manager, GmailProvider(manager), PolicyEngine())
        async with get_session() as db:
            await executor.reconcile_all_pending(db)
    yield
    # Shutdown
    await close_db()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version='0.1.0',
        lifespan=lifespan,
        root_path=settings.root_path,
        docs_url='/docs' if settings.debug else None,
        redoc_url='/redoc' if settings.debug else None,
    )

    # CORS - only allow our frontend origin
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_url.rstrip('/')],
        allow_credentials=True,
        allow_methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
        allow_headers=['*'],
        expose_headers=['X-CSRF-Token'],
    )

    # Include routers
    app.include_router(auth.router)
    app.include_router(connections.router)
    app.include_router(agent.router)
    app.include_router(audit.router)

    # Health endpoints (no auth required)
    @app.get('/healthz')
    async def healthz():
        return {'status': 'ok'}

    @app.get('/readyz')
    async def readyz():
        # Check database and Keycloak JWKS reachability
        from app.auth.oidc import KeycloakClient
        from app.db.session import get_engine

        checks = {'database': False, 'keycloak': False}

        # Database
        try:
            engine = get_engine()
            async with engine.connect() as conn:
                await conn.execute(text('SELECT 1'))
            checks['database'] = True
        except Exception:
            logger.warning('Database readiness check failed', exc_info=True)

        # Keycloak JWKS
        try:
            oidc = KeycloakClient()
            await oidc._get_jwks()
            checks['keycloak'] = True
        except Exception:
            logger.warning('Keycloak readiness check failed', exc_info=True)

        status_code = 200 if all(checks.values()) else 503
        return JSONResponse(content={'checks': checks}, status_code=status_code)

    # Global exception handler
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        settings = get_settings()
        logger.exception('Unhandled API exception', extra={'path': request.url.path})
        if settings.debug:
            import traceback

            return JSONResponse(
                status_code=500,
                content={'detail': str(exc), 'traceback': traceback.format_exc()},
            )
        return JSONResponse(status_code=500, content={'detail': 'Internal server error'})

    return app


app = create_app()
