"""Real HTTP middleware/routes with controlled persistence boundaries."""
from datetime import datetime,timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from src.constants.data import Brain
from src.services.auth import AuthService
from src.services.credentials import PostgreSQLTokenRepository
from src.services.console_sessions import ConsoleSessions,SESSION_COOKIE
from src.services.api.errors import install_error_handlers
from src.services.api.middlewares import auth as middleware,brains
from src.services.api.routes import auth,access_tokens,meta,workspaces,workspace_api,agent,ingest,public,system
from src.services.api.controllers import system as system_controller
from tests.test_console_sessions import Redis
from tests.test_workspaces import MemoryRepository

class Store(PostgreSQLTokenRepository):
    """Substitute SQL execution only; issuance, hashing, metadata and revocation use production code."""
    def __init__(self): super().__init__();self.rows={}
    def _query(self,sql,args=(),many=False,write=False):
        if 'INSERT INTO access_tokens' in sql:
            keys=['id','label','brain_id','brain_resource_id','token_prefix','token_hash','permissions','created_by','expires_at']
            row=dict(zip(keys,args));row.update(created_at=datetime.now(timezone.utc),last_used_at=None,revoked_at=None,legacy=False)
            self.rows[row['id']]=row;return row.copy()
        if sql.startswith('SELECT'):
            rows=list(self.rows.values())
            if 'token_hash=' in sql: rows=[r for r in rows if r['token_hash']==args[0]]
            elif 'id=' in sql: rows=[r for r in rows if r['id']==args[0]]
            elif 'brain_id=' in sql: rows=[r for r in rows if r['brain_id']==args[0]]
            return [r.copy() for r in rows] if many else rows[0].copy() if rows else None
        row=self.rows.get(args[0])
        if row is None:return None
        row['revoked_at' if 'revoked_at' in sql else 'last_used_at']=datetime.now(timezone.utc)
        return row.copy()

class Registry(MemoryRepository):
    def get_brain_by_pat(self,pat): return next((b for b in self.brains.values() if b.pat==pat),None)

@pytest.fixture
def http(monkeypatch):
    monkeypatch.setenv('SYSTEM_PAT','canonical-admin');monkeypatch.delenv('BRAINPAT_TOKEN',raising=False)
    monkeypatch.delenv('PUBLIC_BASE_URL',raising=False)
    registry=Registry([Brain(name_key='alpha'),Brain(name_key='beta')]);store=Store();sessions=ConsoleSessions(Redis())
    from src.services.workspaces import WorkspaceService
    WorkspaceService(registry).bootstrap()
    service=AuthService(registry,store)
    for module in [middleware,auth,access_tokens]: monkeypatch.setattr(module,'get_auth_service',lambda *args:service)
    monkeypatch.setattr(middleware,'get_console_sessions',lambda:sessions);monkeypatch.setattr(auth,'get_console_sessions',lambda:sessions)
    for module in [brains,workspaces,workspace_api,system_controller,agent]: monkeypatch.setattr(module,'data_adapter',registry)
    monkeypatch.setattr(access_tokens,'token_repository',store)
    app=FastAPI();install_error_handlers(app);app.add_middleware(middleware.BrainPATMiddleware);app.add_middleware(brains.BrainMiddleware)
    for router in [auth.auth_router,access_tokens.access_token_router,meta.meta_router,workspaces.workspace_router,workspace_api.workspace_api_router,agent.agent_router,ingest.ingest_router,public.public_router,system.system_router]:app.include_router(router)
    return TestClient(app,base_url='https://testserver'),registry,store,sessions

ADMIN={'Authorization':'Bearer canonical-admin'}

def issue(client,permissions=None):
    response=client.post('/system/access-tokens',headers=ADMIN,json={'brain_id':'alpha','label':'agent-prod','permissions':permissions or ['memory:read','memory:write']})
    assert response.status_code==201,response.text
    return response.json()

def test_system_cookie_login_csrf_and_logout(http):
    client,registry,store,sessions=http
    response=client.post('/auth/session',json={'token':'canonical-admin'})
    assert response.status_code==200
    cookie=response.headers['set-cookie'];assert 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=strict' in cookie
    assert 'canonical-admin' not in response.text
    assert client.get('/auth/me').json()['brain_id'] is None
    assert client.get('/meta/login-info').json()=={'is_system_pat':True,'brain_id':None}
    assert client.get('/system/workspaces').status_code==200
    assert client.post('/system/brains',json={'brain_id':'third'}).status_code==403
    csrf=response.json()['csrf_token']
    assert client.post('/system/brains',json={'brain_id':'third'},headers={'X-CSRF-Token':csrf}).status_code==200
    assert client.post('/auth/logout',headers={'X-CSRF-Token':csrf}).status_code==200
    assert client.get('/auth/me').status_code==401

def test_token_lifecycle_and_session_revocation(http):
    client,registry,store,sessions=http
    issued=issue(client);token=issued['token'];headers={'Authorization':'Bearer '+token}
    assert token not in repr(store.rows)
    assert client.get('/system/access-tokens',headers=headers).status_code==403
    listing=client.get('/system/access-tokens',headers=ADMIN).json()['tokens']
    assert 'token' not in listing[0] and 'token_hash' not in listing[0]
    assert client.get('/brains/alpha/agent/capabilities',headers=headers).status_code==200
    assert client.get('/brains/beta/agent/capabilities',headers=headers).status_code==403
    login=client.post('/auth/session',json={'token':token});assert login.status_code==200
    assert client.get('/auth/me').json()['brain_id']=='alpha'
    assert {w['brain_id'] for w in client.get('/system/workspaces').json()['workspaces']}=={'alpha'}
    assert client.delete('/system/access-tokens/'+issued['id'],headers=ADMIN).status_code==200
    assert client.get('/brains/alpha/agent/capabilities',headers=headers).status_code==401
    assert client.get('/auth/me').status_code==401

@pytest.mark.parametrize('path',['/auth/me','/meta/login-info','/system/workspaces'])
def test_invalid_and_missing_token_401(http,path):
    client,*_=http
    assert client.get(path).status_code==401
    assert client.get(path,headers={'Authorization':'Bearer unknown'}).status_code==401

def test_credential_free_creation_and_listings(http):
    client,registry,store,sessions=http
    assert 'pat' not in client.post('/system/brains',headers=ADMIN,json={'brain_id':'new-brain'}).json()
    assert 'pat' not in client.post('/system/workspaces',headers=ADMIN,json={'slug':'new-workspace','display_name':'New workspace'}).json()
    assert all(brain.pat is None for brain in registry.brains.values())
    assert 'pat' not in client.get('/system/brains-list',headers=ADMIN).text
    assert 'pat' not in client.get('/system/workspaces',headers=ADMIN).text

@pytest.mark.parametrize('path',['/ingest/structured','/brains/alpha/api/ingest/structured'])
def test_exception_containing_structured_validation_is_422(http,path):
    client,*_=http
    response=client.post(path,headers={**ADMIN,'X-Brain-ID':'alpha'},json={'data':[{'object':{'name':'only-object','type':'ENTITY'}}]})
    assert response.status_code==422,response.text
    body=response.json();assert body['error']['code']=='VALIDATION_ERROR'
    assert isinstance(body['detail'][0]['ctx']['error'],str)
    assert 'input' not in body['detail'][0]

def test_read_only_token_cannot_ingest(http):
    client,*_=http;issued=issue(client,['memory:read'])
    response=client.post('/brains/alpha/api/ingest/',headers={'Authorization':'Bearer '+issued['token']},json={'data':{'data_type':'text','text_data':'example'}})
    assert response.status_code==403

def test_invalid_login_and_cross_origin_creates_no_session(http):
    client,registry,store,sessions=http
    response=client.post('/auth/session',json={'token':'unknown'})
    assert response.status_code==401 and 'set-cookie' not in response.headers
    response=client.post('/auth/session',json={'token':'canonical-admin'},headers={'Origin':'https://evil.example'})
    assert response.status_code==403 and 'set-cookie' not in response.headers
    assert not sessions.redis.values


def test_auth_openapi_responses_are_typed_and_token_listing_has_no_secret_fields(http):
    client,*_=http
    schema=client.get('/openapi.json').json()
    for path,method in [('/auth/session','post'),('/auth/me','get'),('/auth/logout','post'),('/system/access-tokens','get'),('/system/access-tokens','post')]:
        status='201' if path=='/system/access-tokens' and method=='post' else '200'
        assert schema['paths'][path][method]['responses'][status]['content']['application/json']['schema'].get('$ref')
    metadata=schema['components']['schemas']['AccessTokenMetadataResponse']['properties']
    assert 'token' not in metadata and 'token_hash' not in metadata

def test_cookie_csrf_origin_failure_is_forbidden_not_service_unavailable(http):
    client,*_=http
    login=client.post('/auth/session',json={'token':'canonical-admin'})
    response=client.post('/auth/logout',headers={'X-CSRF-Token':login.json()['csrf_token'],'Origin':'https://evil.example'})
    assert response.status_code==403
    assert client.get('/auth/me').status_code==200
