from pathlib import Path
import json
from unittest.mock import patch
import pytest
from src.services.task_contract import accepted_task_id
from src.services.persistence import persistence_state, enforce_persistence
from src.services.redaction import redact
from src.lib.tracing.tracker import TraceTracker, LocalTraceQueue
from src.lib.tracing.runtime import default_health_probes
from src.config import config

@pytest.mark.parametrize('status,payload', [(422,{}),(500,{}),(200,{'task_id':'id'}),(202,{}),(202,{'task_id':None}),(202,{'task_id':'None'}),(202,{'task_id':'null'}),(202,{'task_id':''})])
def test_rejected_ingest_cannot_start_polling(status,payload):
    requests=[]
    with pytest.raises(ValueError, match='INGESTION_CONTRACT_ERROR'):
        task=accepted_task_id(status,payload);requests.append('/tasks/'+task)
    assert requests == []

def test_accepted_ingest_task_contract():
    assert accepted_task_id(202, {'task_id':'job'}) == 'job'

def test_hf_probes_only_active_dependencies(monkeypatch):
    monkeypatch.setattr(config,'data_db','postgresql');monkeypatch.setattr(config,'vector_db','postgresql');monkeypatch.setattr(config,'graph_db','networkx')
    monkeypatch.setattr(config,'embeddings_provider','ollama');monkeypatch.setenv('CELERY_BACKEND','redis')
    assert {p.name for p in default_health_probes()} == {'postgresql','redis','ollama'}

def test_log_mode_never_accumulates_events(monkeypatch):
    monkeypatch.setenv('TRACE_MODE','log')
    tracker=TraceTracker(enabled=True)
    for _ in range(10010): tracker.publish('heartbeat','runtime.heartbeat')
    assert tracker.queue.size == 0
    assert tracker.dropped_events == 0

def test_queue_saturation_is_bounded_and_rate_limited(caplog):
    tracker=TraceTracker(queue=LocalTraceQueue(max_size=2),enabled=True)
    for _ in range(100): tracker.publish('heartbeat','runtime.heartbeat')
    assert tracker.queue.size == 2
    assert tracker.dropped_events == 98
    assert caplog.text.count('Trace queue saturated') == 1

def test_persistence_modes_and_explicit_override(tmp_path,monkeypatch):
    durable=tmp_path/'durable';durable.mkdir()
    assert persistence_state({'BRAINAPI_DURABLE_ROOT':str(durable)})['mode']=='durable'
    monkeypatch.setenv('BRAINAPI_DURABLE_ROOT',str(tmp_path/'missing'))
    monkeypatch.delenv('PERSISTENCE_MODE',raising=False);monkeypatch.delenv('ALLOW_EPHEMERAL_STORAGE',raising=False)
    monkeypatch.setenv('ENV','production')
    with pytest.raises(RuntimeError,match='EPHEMERAL_STORAGE_REQUIRES_OVERRIDE'): enforce_persistence()
    monkeypatch.setenv('ALLOW_EPHEMERAL_STORAGE','true')
    assert enforce_persistence()['production_ready'] is False

def test_redaction_covers_headers_urls_and_nested_payloads(monkeypatch):
    monkeypatch.setenv('SYSTEM_PAT','private-system-example')
    payload={'Authorization':'Bearer anything','nested':{'password':'sensitive'},'message':'private-system-example postgres://user:password@localhost bpat_another-secret'}
    safe=json.dumps(redact(payload))
    for value in ['anything','sensitive','private-system-example','user:password','bpat_another-secret']: assert value not in safe
