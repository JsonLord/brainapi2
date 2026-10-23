from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.services.api.connection_docs import CONNECTION_GUIDE, connection_docs_router
from src.services.api.middlewares.auth import BrainPATMiddleware
from src.services.api.middlewares.brains import BrainMiddleware


def test_public_guide_serves_committed_content_without_auth_or_brain_access():
    app = FastAPI()
    app.include_router(connection_docs_router)
    app.add_middleware(BrainPATMiddleware)
    app.add_middleware(BrainMiddleware)
    with patch('src.services.api.middlewares.auth.get_auth_service', side_effect=AssertionError('auth touched')), \
         patch('src.services.api.middlewares.brains.resolve_workspace_scope', side_effect=AssertionError('brain touched')):
        client = TestClient(app)
        response = client.get('/api-docs', follow_redirects=False)
        assert response.status_code == 200
        assert response.text == CONNECTION_GUIDE.read_text()
        assert response.headers['content-type'].startswith('text/plain')
        assert response.headers['content-disposition'].startswith('inline;')
        assert response.headers['x-content-type-options'] == 'nosniff'
        head = client.head('/api-docs')
        assert head.status_code == 200 and head.content == b''
        assert head.headers['content-length'] == response.headers['content-length']

