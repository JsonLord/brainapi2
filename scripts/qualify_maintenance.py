"""Disposable maintenance qualification; rebuild externally between create/restore.

Uses SYSTEM_PAT in memory. State contains only resource IDs, markers and SHA.
Never point cleanup at an unowned/preexisting namespace.
"""
import argparse
import json
import os
import time
from pathlib import Path
from uuid import uuid4
import requests


def qualify(action, path):
    base=os.getenv('QUALIFY_API_URL','http://127.0.0.1:7860').rstrip('/')
    session=requests.Session()
    if base.startswith(('http://127.0.0.1:', 'http://localhost:')):
        session.trust_env=False
    session.headers['BrainPAT']=os.environ['SYSTEM_PAT']
    def call(method,route,body=None,expected=200):
        r=session.request(method,base+route,json=body,timeout=180)
        if r.status_code!=expected:
            raise RuntimeError(f'Qualification HTTP {r.status_code} at {route}; response suppressed')
        return r.json()
    def save(state):
        path.write_text(json.dumps(state,indent=2)+'\n')
    def retrieve(state):
        for route in ('search','context'):
            result=call('POST',f"/brains/{state['slug']}/agent/{route}",{'query':state['marker']})
            assert state['marker'] in json.dumps(result),'Qualification fact absent from '+route
    def policy(health):
        storage=health['storage'];assert storage['zero_loss'] is False
        assert isinstance(storage['rpo_seconds'],int) and storage['rpo_seconds']>0
        if not storage['checkpointed_production_accepted'] and storage['local']=='ephemeral':
            assert storage['production_ready'] is False
    if action=='create':
        assert not path.exists(),'State already exists; refusing a second namespace'
        suffix=uuid4().hex[:12];slug='maintenance-qa-'+suffix
        before=call('GET','/system/workspaces')['workspaces']
        assert not any(w['slug']==slug for w in before)
        workspace=call('POST','/system/workspaces',{'slug':slug,'display_name':'Disposable maintenance qualification'},201)
        state={'slug':slug,'workspace_id':workspace['id'],'brain_id':workspace['brain_id'],
               'marker':'MAINTENANCE_FACT_'+uuid4().hex,'baseline_slugs':[w['slug'] for w in before]}
        brain=next(b for b in call('GET','/system/brains-list') if b['name_key']==state['brain_id'])
        state['canonical_brain_id']=brain['id'];save(state)
        accepted=call('POST',f'/brains/{slug}/agent/memory',{'text':'The maintenance qualification compass is copper. '+state['marker']},202)
        state['task_id']=accepted['task_id'];assert isinstance(state['task_id'],str) and state['task_id']
        save(state)
        # Request barrier immediately, before polling: accepted work must drain.
        result=call('POST','/system/backup/checkpoint?timeout_seconds=120')
        assert result['state']=='durable' and result['remote_verified'] and result['dirty'] is False
        state['checkpoint_a']=result['checkpoint'];save(state)
        task=call('GET',f"/brains/{slug}/agent/tasks/{state['task_id']}")
        assert str(task['status']).lower() in {'completed','success'}
        retrieve(state)
        rejected=call('POST',f'/brains/{slug}/agent/memory',{'text':'Must be rejected during maintenance'},503)
        assert not rejected.get('task_id')
        control=call('GET','/system/backup/status');assert control['quiesced'] and control['pending_tasks']==0
        health=call('GET','/health');policy(health)
        state['pre_rebuild_startup_id']=health['backup']['startup_id'];save(state)
        return {'checkpoint':state['checkpoint_a'],'accepted_work_drained':True,'retrieval':'passed','new_writes':'blocked'}
    state=json.loads(path.read_text());slug=state['slug']
    workspace=call('GET','/system/workspaces/'+slug);assert workspace['id']==state['workspace_id']
    if action=='restored':
        health=call('GET','/health');policy(health)
        assert health['backup']['startup_id']!=state['pre_rebuild_startup_id']
        assert health['restored_checkpoint']==state['checkpoint_a']
        assert health['remote_checkpoint']==state['checkpoint_a'] and health['checkpoint_lag']==0
        assert health['backup']['restored'] is True
        assert call('GET','/system/backup/status')['quiesced']
        brain=next(b for b in call('GET','/system/brains-list') if b['name_key']==state['brain_id'])
        assert brain['id']==state['canonical_brain_id']
        retrieve(state)
        assert call('POST',f'/brains/{slug}/agent/memory',{'text':'Rejected until explicit resume'},503)
        state['restoration_passed']=True;save(state)
        resumed=call('POST','/system/backup/resume');assert resumed['quiesced'] is False
        call('PATCH','/system/workspaces/'+slug,{'display_name':'Disposable qualification resumed'})
        state['resume_checkpoint']=resumed['checkpoint'];save(state)
        return {'restored_checkpoint':health['restored_checkpoint'],'remote_checkpoint':health['remote_checkpoint'],
                'first_replacement_retrieval':'passed','identities':'preserved','explicit_resume':'passed','storage':health['storage'],'checks':health['checks']}
    if action=='cleanup':
        assert state.get('restoration_passed'),'Cleanup only after restore success'
        call('DELETE','/system/brains/'+state['brain_id'])
        call('PATCH','/system/workspaces/'+slug,{'archived':True})
        result=call('POST','/system/backup/checkpoint');assert result['remote_verified']
        state['deletion_checkpoint']=result['checkpoint'];save(state)
        assert not any(b['name_key']==state['brain_id'] for b in call('GET','/system/brains-list'))
        released=call('POST','/system/backup/resume');assert released['quiesced'] is False
        state['cleanup_release_checkpoint']=released['checkpoint'];save(state)
        return {'deleted_only':slug,'deletion_checkpoint':result['checkpoint'],'final_checkpoint':released['checkpoint'],'writes_resumed':True}
    raise RuntimeError('Unknown qualification action')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['create','restored','cleanup']);parser.add_argument('--state',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(qualify(args.action,args.state)))
