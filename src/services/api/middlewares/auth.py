"""All HTTP authentication delegates to the shared identity service."""

from starlette.middleware.base import BaseHTTPMiddleware
from src.services.api.console_static import is_console_path
from src.services.api.errors import error_response
from src.services.data.main import data_adapter
from src.services.auth import AuthError, bearer_token, get_auth_service, request_permission


class BrainPATMiddleware(BaseHTTPMiddleware):
    excluded_prefixes = {"/docs", "/redoc", "/demo", "/api-docs"}
    auth_exempt_paths = {"/", "/health", "/openapi.json", "/favicon.ico", "/auth/session"}

    async def dispatch(self, request, call_next):
        path = request.url.path
        if (request.method == "OPTIONS" or is_console_path(path)
                or path in self.auth_exempt_paths
                or any(path.startswith(p) for p in self.excluded_prefixes) or "/mcp" in path):
            return await call_next(request)
        service = get_auth_service(data_adapter)
        try:
            token = bearer_token(request.headers)
            principal = service.authenticate_token(token)
            request.state.principal = principal
            request.state.is_system_pat = principal.is_system
            if path.startswith("/system"):
                discovery = request.method == "GET" and path.startswith("/system/workspaces")
                if not principal.is_system and not discovery:
                    raise AuthError(403, "FORBIDDEN")
                if not principal.is_system:
                    request.state.brain_id = principal.brain_id
            elif path not in {"/auth/me", "/auth/logout", "/meta/login-info"}:
                brain_id = getattr(request.state, "brain_id", None)
                if not brain_id:
                    return error_response(request, status_code=400, detail="Brain ID is required.", code="BRAIN_ID_REQUIRED")
                service.authorize_workspace(principal, brain_id, request_permission(request.method, path))
        except AuthError as error:
            return error_response(request, status_code=error.status, detail=error.code, code=error.code)
        except Exception:
            return error_response(request, status_code=503, detail="Authentication store unavailable")
        return await call_next(request)
