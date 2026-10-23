"""Admission leases for HTTP and MCP content mutations (reads stay available)."""
import json
from starlette.responses import JSONResponse
from src.services.auth import request_permission
from .maintenance import admission
from .git import BackupError

READ_TOOLS = {'memory_search', 'memory_context', 'memory_neighbors', 'memory_entity',
              'memory_task_status', 'search_memory', 'traverse_graph', 'get_brains',
              'get_entity_info', 'get_instructions'}
READ_TOOLS.update({'list_brains', 'search_semantically', 'get_search_operation_instructions'})


class ContentAdmissionMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        path, method = scope.get('path', ''), scope.get('method', 'GET')
        write = request_permission(method, path) == 'memory:write'
        if path.startswith(('/auth/', '/system/backup/', '/system/access-tokens')):
            write = False
        if '/mcp' in path and method == 'POST':
            chunks = []
            while True:
                msg = await receive()
                if msg['type'] != 'http.request':
                    return
                chunks.append(msg.get('body', b''))
                if sum(map(len, chunks)) > 2 * 1024 * 1024:
                    return await JSONResponse({'error': {'code': 'INVALID_REQUEST'}}, status_code=413)(scope, receive, send)
                if not msg.get('more_body', False):
                    break
            body = b''.join(chunks)
            try:
                payload = json.loads(body)
                calls = payload if isinstance(payload, list) else [payload]
                write = any(isinstance(c, dict) and c.get('method') == 'tools/call'
                    and (c.get('params') or {}).get('name') not in READ_TOOLS for c in calls)
            except (ValueError, TypeError, AttributeError):
                write = True
            previous_receive = receive
            delivered = False
            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {'type': 'http.request', 'body': body, 'more_body': False}
                return await previous_receive()
            receive = replay
        if not write:
            return await self.app(scope, receive, send)
        lease = admission()
        try:
            lease.__enter__()
        except BackupError as exc:
            return await JSONResponse({'detail': str(exc), 'error': {'code': str(exc)}}, status_code=503)(scope, receive, send)
        try:
            await self.app(scope, receive, send)
        finally:
            lease.__exit__(None, None, None)
