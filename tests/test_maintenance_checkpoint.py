"""Real formatter, cross-process admission, Git handoff and explicit RPO gates."""
import json
import logging
import threading
import time
from unittest.mock import patch

import pytest
from uvicorn.logging import AccessFormatter
from src.services.redaction import install_log_redaction, redact, access_logging_healthy
from src.services.brain_backup import maintenance as gate
from src.services.brain_backup.git import BackupError
from src.services.brain_backup.state import public_status, write_status, mark_dirty
from src.services.persistence import persistence_state
from tests.test_brain_backup import checkpoint, add_brain, Database


def test_real_uvicorn_record_preserves_format_and_redacts_encoded_credentials(monkeypatch):
    monkeypatch.setenv('SYSTEM_PAT', 'synthetic-secret-value')
    install_log_redaction()
    record = logging.getLogRecordFactory()('uvicorn.access', logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d', ('127.0.0.1:42', 'POST',
        '/brains/demo/agent/memory?token=opaque-value&Authorization=Bearer%20hidden-value&safe=yes', '1.1', 202), None)
    assert isinstance(record.args, tuple) and len(record.args) == 5 and record.args[-1] == 202
    result = AccessFormatter('{client_addr} "{request_line}" {status_code}', style='{', use_colors=False).format(record)
    assert 'POST /brains/demo/agent/memory' in result and '202' in result and '?[REDACTED]' in result
    assert 'opaque-value' not in result and 'hidden-value' not in result
    assert access_logging_healthy()
    assert 'synthetic-secret-value' not in redact('/tokens/synthetic-secret-value')


def test_mapping_args_and_exception_redaction_preserve_interpolation():
    record = logging.getLogRecordFactory()('app', logging.INFO, __file__, 1,
        '%(method)s %(Authorization)s %(status)d', ({'method': 'GET', 'Authorization': 'Bearer hidden', 'status': 200},), None)
    assert isinstance(record.args, dict)
    assert 'GET' in record.getMessage() and '200' in record.getMessage() and 'hidden' not in record.getMessage()


@pytest.fixture
def enabled(checkpoint, monkeypatch):
    monkeypatch.setenv('BRAIN_BACKUP_ENABLED', 'true')
    monkeypatch.setenv('BRAIN_BACKUP_STARTUP_ID', 'current-container')
    manager, db, remote = checkpoint
    manager.startup()
    add_brain(db)
    return manager, db, remote


def test_barrier_waits_for_admitted_request_and_child_task(enabled):
    manager, _, _ = enabled
    entered, release = threading.Event(), threading.Event()
    def request():
        with gate.admission():
            entered.set();release.wait(2)
            gate.register_task('accepted-child')
    thread = threading.Thread(target=request);thread.start();assert entered.wait(1)
    gate.quiesce()
    with pytest.raises(BackupError, match='QUIESCED'):
        with gate.admission():pass
    with pytest.raises(BackupError, match='QUIESCED'):gate.register_task('new-root')
    output=[]
    barrier=threading.Thread(target=lambda:output.append(gate.checkpoint(manager, 3)));barrier.start()
    release.set();thread.join(1)
    # Accepted children can extend the graph until the drain has sealed.
    gate.register_task('chain-child', parent='accepted-child')
    gate.finish_task('accepted-child');time.sleep(.05);assert not output
    gate.finish_task('chain-child');barrier.join(3)
    assert output[0]['state']=='durable' and output[0]['remote_verified']
    assert gate.status()['quiesced'] and not gate.status()['pending_tasks']
    assert json.loads((manager.repo.path/'manifest.json').read_text())['maintenance']['quiesced']
    with pytest.raises(BackupError, match='QUIESCED'):gate.register_task('late-child', parent='chain-child')
    gate.resume(manager)
    with gate.admission():pass
    assert json.loads((manager.repo.path/'manifest.json').read_text())['maintenance']=={}


def test_handoff_hold_survives_fresh_restore(enabled):
    manager, db, _ = enabled
    result=gate.checkpoint(manager)
    db.registry={'brains':[],'workspaces':[]}
    gate.initialize()
    manager.startup()
    assert public_status()['restored_checkpoint']==result['checkpoint']
    assert gate.status()['quiesced']
    with pytest.raises(BackupError, match='QUIESCED'):
        with gate.admission():pass
    gate.resume(manager)
    assert not gate.status()['quiesced']


def test_failed_remote_verification_keeps_hold(enabled, monkeypatch):
    manager, _, _ = enabled
    monkeypatch.setattr(manager, 'verify_checkpoint', lambda _: (_ for _ in ()).throw(BackupError('BACKUP_REMOTE_CONFLICT')))
    with pytest.raises(BackupError, match='CONFLICT'):gate.checkpoint(manager)
    assert gate.status()['quiesced'] and gate.status()['state']=='degraded'
    assert public_status()['state']=='degraded'
    with pytest.raises(BackupError):gate.resume(manager)
    assert gate.status()['quiesced']


def test_drain_timeout_never_claims_durability(enabled):
    manager, _, _ = enabled
    gate.register_task('unsettled')
    with pytest.raises(BackupError, match='DRAIN_TIMEOUT'):gate.checkpoint(manager, 0)
    assert gate.status()['quiesced'] and gate.status()['pending_tasks']==1


def test_remote_advance_during_restore_prevents_readiness(enabled, monkeypatch):
    manager, db, _ = enabled
    first, _=manager.backup();db.registry={'brains':[],'workspaces':[]}
    original=manager.remote;count=0
    def racing():
        nonlocal count
        count+=1
        head,m,r=original()
        return ('f'*40 if count>1 else head),m,r
    monkeypatch.setattr(manager,'remote',racing)
    with pytest.raises(BackupError, match='STARTUP_REMOTE_ADVANCED'):manager.startup()
    assert gate.status()['quiesced'] and public_status()['state']=='degraded'


def test_remote_advance_after_bootstrap_fails_startup(enabled, monkeypatch):
    manager, _, _=enabled
    manager.backup()
    original=manager.remote
    def advanced():
        _,m,r=original();return 'f'*40,m,r
    monkeypatch.setattr(manager,'remote',advanced)
    with pytest.raises(BackupError,match='STARTUP_REMOTE_ADVANCED'):manager.verify_startup()
    assert gate.status()['quiesced']


@pytest.mark.parametrize('accepted,age,remote,error,qualified,expected',[
    (False,10,True,None,True,False),(True,10,True,None,True,True),
    (True,301,True,None,True,False),(True,10,False,None,True,False),
    (True,10,True,'BACKUP_REMOTE_CONFLICT',True,False),(True,10,True,None,False,False)])
def test_explicit_production_rpo_policy(tmp_path,accepted,age,remote,error,qualified,expected):
    env={'BRAINAPI_DURABLE_ROOT':str(tmp_path/'absent'),'ALLOW_CHECKPOINTED_PRODUCTION':str(accepted).lower(),
         'BRAIN_BACKUP_MAX_RPO_SECONDS':'300'}
    backup={'enabled':True,'restore_qualified':qualified,'state':'ready','error':error,
            'remote_verified':remote,'remote_checkpoint':'a'*40,'commit':'a'*40,'backup_age_seconds':age}
    checks={k:True for k in ('auth','database','embedding_profile','access_logging')}
    result=persistence_state(env,backup=backup,checks=checks)
    assert result['production_ready'] is expected
    assert result['zero_loss'] is False and result['rpo_seconds']==300
    for name in checks:
        assert not persistence_state(env,backup=backup,checks={**checks,name:False})['production_ready']


def test_age_and_dirty_are_observed_not_static(enabled):
    from datetime import datetime,timedelta,timezone
    write_status(state='ready',last_success=(datetime.now(timezone.utc)-timedelta(seconds=40)).isoformat())
    assert 39 <= public_status()['backup_age_seconds'] <= 42
    mark_dirty();assert public_status()['backup_dirty']


def test_http_and_mcp_mutations_block_but_reads_remain_available(enabled):
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.responses import JSONResponse
    from starlette.testclient import TestClient
    from starlette.middleware import Middleware
    from src.services.brain_backup.middleware import ContentAdmissionMiddleware
    calls=[]
    async def endpoint(request):
        calls.append(request.url.path)
        return JSONResponse({'ok':True})
    app=Starlette(routes=[Route('/ingest/',endpoint,methods=['POST']),
        Route('/retrieve/context',endpoint,methods=['POST']),Route('/mcp',endpoint,methods=['POST'])],
        middleware=[Middleware(ContentAdmissionMiddleware)])
    client=TestClient(app)
    assert client.post('/ingest/').status_code==200
    gate.quiesce()
    assert client.post('/ingest/').status_code==503
    assert calls.count('/ingest/')==1
    assert client.post('/retrieve/context').status_code==200
    assert client.post('/mcp',json={'method':'tools/call','params':{'name':'memory_store'}}).status_code==503
    assert client.post('/mcp',json={'method':'tools/call','params':{'name':'memory_search'}}).status_code==200


def test_real_celery_base_tracks_accepted_nested_work(enabled):
    from celery import Celery
    from src.services.brain_backup.tasks import ContentTask
    app=Celery('maintenance-unit',broker='memory://',backend='cache+memory://',task_cls=ContentTask)
    app.conf.update(task_always_eager=True,task_eager_propagates=True)
    @app.task
    def child():return 42
    @app.task
    def root():
        gate.quiesce()
        assert gate.status()['pending_tasks']>=1
        return child.delay().get(disable_sync_subtasks=False)
    assert root.delay().get()==42
    assert gate.status()['pending_tasks']==0
    with pytest.raises(BackupError,match='QUIESCED'):child.delay()
    app.close()


def test_backup_controls_reject_non_admin(enabled):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.services.api.routes.backup import backup_router
    app=FastAPI();app.include_router(backup_router)
    client=TestClient(app)
    for method,path in [('GET','status'),('POST','quiesce'),('POST','checkpoint'),('POST','resume')]:
        assert client.request(method,'/system/backup/'+path).status_code==403


@pytest.mark.parametrize('template,args',[
    ('Authorization=%s method=%s',('Bearer opaque-auth-value','GET')),
    ('BrainPAT: "%s" method=%s',('opaque-brain-value','POST')),
    ('%s=%s method=%s',('token','opaque-token-value','PUT')),
])
def test_percent_templates_never_lose_arity_or_leak(template,args):
    record=logging.getLogRecordFactory()('app',logging.INFO,__file__,1,template,args,None)
    assert isinstance(record.args,tuple) and len(record.args)==len(args)
    text=record.getMessage()
    assert 'opaque-' not in text and args[-1] in text


def test_redelivered_active_tasks_cannot_disappear_from_drain(enabled):
    manager, _, _=enabled
    gate.register_task('delivery')
    first=gate.begin_task('delivery');second=gate.begin_task('delivery')
    gate.finish_task('delivery',first)
    with pytest.raises(BackupError,match='DRAIN_TIMEOUT'):gate.checkpoint(manager,0)
    gate.finish_task('delivery',second)
    assert gate.checkpoint(manager)['remote_verified']


def test_missing_credentials_at_checkpoint_still_closes_admission(enabled,monkeypatch):
    from fastapi import FastAPI,Request
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from src.services.api.routes.backup import backup_router
    from src.services.brain_backup import __main__ as cli
    app=FastAPI()
    @app.middleware('http')
    async def system(request,call_next):
        request.state.principal=SimpleNamespace(is_system=True)
        return await call_next(request)
    app.include_router(backup_router)
    monkeypatch.setattr(cli,'configured',lambda:(_ for _ in ()).throw(BackupError('BACKUP_GITHUB_PAT_REQUIRED')))
    result=TestClient(app).post('/system/backup/checkpoint')
    assert result.status_code==503 and gate.status()['quiesced']


def test_retained_local_restart_preserves_accepted_tasks_and_hold(enabled):
    manager,_,_=enabled
    manager.backup();gate.register_task('accepted-before-restart');gate.quiesce()
    manager.startup()
    assert gate.status()['quiesced'] and gate.status()['pending_tasks']==1
    gate.finish_task('accepted-before-restart');assert gate.checkpoint(manager)['remote_verified']


def test_sealed_handoff_pins_periodic_and_shutdown_publication(enabled,monkeypatch):
    manager,_,_=enabled
    head=gate.checkpoint(manager)['checkpoint']
    monkeypatch.setattr(manager.pg,'snapshot',lambda _:pytest.fail('Sealed daemon must not publish a successor'))
    assert manager.reconcile()==(head,False)
    assert manager.repo.fetch()==head
