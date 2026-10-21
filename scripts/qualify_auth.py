"""Offline production-image auth gate; only unique disposable resources are mutated."""
import json
import os
import secrets
import subprocess
from pathlib import Path
import sys
import time
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from src.config import config
from src.services.credentials import token_repository
from src.services.task_contract import accepted_task_id
from src.lib.postgresql._provisioning import borrow,get_system_pool
from src.lib.tracing.runtime import default_health_probes
from src.lib.tracing.tracker import tracer


def check(condition,message):
    if not condition:raise RuntimeError(message)


def qualify():
    suffix=uuid4().hex[:12]
    names=['auth-qa-'+suffix+'-a','auth-qa-'+suffix+'-b']
    admin={'Authorization':'Bearer '+config.system_pat}
    issued=[];created=[];results={}
    base=os.getenv('QUALIFY_API_URL','http://127.0.0.1:7860')
    # This models the HTTP hop *after* HF TLS termination, including its host
    # and protocol headers. Secure-cookie replay stays entirely inside this
    # disposable, network-isolated image; no browser TLS checks are disabled.
    with httpx.Client(base_url=base,headers={'Host':'auth-qualification.local','X-Forwarded-Proto':'https'},timeout=120,trust_env=False,follow_redirects=True) as api:
        before=api.get('/system/brains-list',headers=admin);before.raise_for_status()
        identities={(b['name_key'],b['id']) for b in before.json()}
        check(not any(name in {b[0] for b in identities} for name in names),'Disposable namespace collision')
        try:
            for name in names:
                response=api.post('/system/workspaces',headers=admin,json={'slug':name,'display_name':'Auth qualification'})
                check(response.status_code==201,'Workspace creation failed');created.append(name)
                check('pat' not in response.json(),'Workspace exposes PAT')
            brains=api.get('/system/brains-list',headers=admin).json()
            check(all('pat' not in b for b in brains),'Brain listing exposes PAT')
            results['credential_free_resources']='passed'
            def issue(permissions):
                response=api.post('/system/access-tokens',headers=admin,json={'brain_id':names[0],'label':'qualification','permissions':permissions})
                check(response.status_code==201,'Explicit token creation failed')
                value=response.json();issued.append(value['id']);return value
            scoped=issue(['memory:read','memory:write']);read_only=issue(['memory:read'])
            headers={'Authorization':'Bearer '+scoped['token']}
            own='/brains/'+names[0];other='/brains/'+names[1]
            for credential in (admin,headers):
                for path in ('/retrieve/entities',own+'/api/retrieve/entities',own+'/agent/capabilities'):
                    response=api.get(path,headers={**credential,'X-Brain-ID':names[0]})
                    check(response.status_code==200,'HTTP/Agent authentication parity failed')
            check(api.get(other+'/agent/capabilities',headers=headers).status_code==403,'Cross-workspace scope accepted')
            check(api.get(own+'/agent/capabilities',headers={'Authorization':'Bearer bpat_unknown'}).status_code==401,'Unknown token accepted')
            check(api.get(own+'/agent/capabilities').status_code==401,'Missing token accepted')
            results['http_agent_auth_parity']='passed'
            listing=api.get('/system/access-tokens',headers=admin).json()['tokens']
            check(all('token' not in item and 'token_hash' not in item for item in listing),'Credential listing exposes secrets')
            with borrow(get_system_pool()) as conn,conn.cursor() as cursor:
                cursor.execute('SELECT token_hash FROM access_tokens WHERE id=%s',(scoped['id'],))
                stored=cursor.fetchone()[0]
                check(stored != scoped['token'] and len(stored)==64,'Token was not stored as a hash')
                cursor.execute('SELECT pat,document FROM data_brains WHERE name_key=ANY(%s)',(names,))
                check(all(not pat and 'pat' not in doc for pat,doc in cursor.fetchall()),'Resource identity persisted a credential')
            results['hash_at_rest_metadata_only']='passed'
            # Emulate an existing legacy row only in our disposable brain. Then
            # exercise the shipped atomic migration and prove the secret is gone.
            legacy_secret=secrets.token_urlsafe(36)
            with borrow(get_system_pool()) as conn,conn.cursor() as cursor:
                cursor.execute("UPDATE data_brains SET pat=%s,document=jsonb_set(document,'{pat}',to_jsonb(%s::text)) WHERE name_key=%s",(legacy_secret,legacy_secret,names[0]))
                conn.commit()
            legacy_headers={'Authorization':'Bearer '+legacy_secret}
            check(api.get(own+'/agent/capabilities',headers=legacy_headers).status_code==200,'Legacy scoped credential rejected')
            check(api.get(other+'/agent/capabilities',headers=legacy_headers).status_code==403,'Legacy credential escaped scope')
            migration=subprocess.run([sys.executable,'-m','scripts.migrate_legacy_credentials','--migrate',names[0]],capture_output=True,text=True)
            check(migration.returncode==0,'Atomic legacy migration failed')
            check(legacy_secret not in migration.stdout+ migration.stderr,'Migration printed a credential')
            check(api.get(own+'/agent/capabilities',headers=legacy_headers).status_code==200,'Migrated legacy credential rejected')
            with borrow(get_system_pool()) as conn,conn.cursor() as cursor:
                cursor.execute('SELECT pat,document FROM data_brains WHERE name_key=%s',(names[0],))
                pat,document=cursor.fetchone()
                check(not pat and 'pat' not in document,'Migration retained plaintext')
            for row in api.get('/system/access-tokens',headers=admin).json()['tokens']:
                if row['brain_id']==names[0] and row['legacy']:
                    issued.append(row['id'])
                    check(api.delete('/system/access-tokens/'+row['id'],headers=admin).status_code==200,'Legacy rotation revocation failed')
            check(api.get(own+'/agent/capabilities',headers=legacy_headers).status_code==401,'Revoked migrated credential accepted')
            results['legacy_scope_atomic_migration_rotation']='passed'

            for credential,is_system in ((admin,True),(headers,False)):
                login=api.post('/auth/session',json={'token':credential['Authorization'][7:]})
                check(login.status_code==200,'Console session creation failed')
                cookie=login.headers.get('set-cookie','')
                check(all(flag in cookie for flag in ['HttpOnly','Secure','SameSite=strict']),'Session cookie flags invalid')
                check('token' not in {key for key in login.json() if key!='csrf_token'},'Console echoed credential')
                sid=login.cookies.get('__Host-brainapi-session')
                browser={'Cookie':'__Host-brainapi-session='+sid}
                me=api.get('/auth/me',headers=browser);check(me.status_code==200,'Console session resolution failed')
                check(me.json()['brain_id']==(None if is_system else names[0]),'Console selected a fake/default brain')
                accessible=api.get('/system/workspaces',headers=browser);check(accessible.status_code==200,'Console discovery failed')
                if not is_system:
                    check({w['brain_id'] for w in accessible.json()['workspaces']}=={names[0]},'Scoped console sees another workspace')
                check(api.post('/auth/logout',headers=browser).status_code==403,'Cookie mutation bypassed CSRF')
                browser['X-CSRF-Token']=login.json()['csrf_token']
                check(api.post('/auth/logout',headers=browser).status_code==200,'Logout failed')
                check(api.get('/auth/me',headers=browser).status_code==401,'Session survived logout')
            api.cookies.clear()
            results['console_session_csrf_logout']='passed'
            rpc_headers={'Accept':'application/json, text/event-stream'}
            initialize={'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'offline-auth-qualification','version':'1'}}}
            for credential in (admin,headers):
                response=api.post(own+'/mcp',headers={**rpc_headers,**credential},json=initialize)
                check(response.status_code==200 and 'protocolVersion' in response.text,'MCP initialize authentication failed')
            check(api.post('/mcp',headers={**rpc_headers,**admin},json=initialize).status_code==200,'Retained MCP rejects canonical administrator')
            check(api.post(other+'/mcp',headers={**rpc_headers,**headers},json=initialize).status_code==403,'MCP cross-workspace scope accepted')
            check(api.post(own+'/mcp',headers={**rpc_headers,'Authorization':'Bearer bpat_unknown'},json=initialize).status_code==401,'MCP unknown token accepted')
            check(api.post(own+'/mcp',headers=rpc_headers,json=initialize).status_code==401,'MCP missing token accepted')
            readonly_headers={'Authorization':'Bearer '+read_only['token']}
            store_call={'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'memory_store','arguments':{'text':'must not write'}}}
            check(api.post(own+'/mcp',headers={**rpc_headers,**readonly_headers},json=store_call).status_code==403,'Read-only MCP credential can write')
            results['mcp_auth_parity_permissions']='passed'
            malformed={'data':[{'object':{'name':'only-object','type':'ENTITY'}}]}
            for path in ('/ingest/structured',own+'/api/ingest/structured'):
                response=api.post(path,headers={**headers,'X-Brain-ID':names[0]},json=malformed)
                check(response.status_code==422 and response.json()['error']['code']=='VALIDATION_ERROR','Structured validation did not return 422')
                try:accepted_task_id(response.status_code,response.json())
                except ValueError:pass
                else:raise RuntimeError('Rejected ingestion may start task polling')
            results['validation_422_no_polling']='passed'
            login=api.post('/auth/session',json={'token':scoped['token']});login.raise_for_status()
            browser={'Cookie':'__Host-brainapi-session='+login.cookies.get('__Host-brainapi-session')}
            check(api.delete('/system/access-tokens/'+scoped['id'],headers=admin).status_code==200,'Revocation failed')
            check(api.get(own+'/agent/capabilities',headers=headers).status_code==401,'Revoked API credential accepted')
            check(api.post(own+'/mcp',headers={**rpc_headers,**headers},json=initialize).status_code==401,'Revoked MCP credential accepted')
            check(api.get('/auth/me',headers=browser).status_code==401,'Session survived originating token revocation')
            results['revocation_all_surfaces']='passed'
            check({p.name for p in default_health_probes()}=={'postgresql','redis','ollama'},'Monitoring inactive backends')
            for _ in range(11000):tracer.publish('heartbeat','qualification.heartbeat')
            check(tracer.queue.size==0 and tracer.dropped_events==0,'Default trace sink accumulates events')
            results['active_probes_bounded_tracing']='passed'
            health=api.get('/health').json()
            check(health['persistence']['mode'] in {'durable','ephemeral'},'Persistence state absent')
            if health['persistence']['mode']=='ephemeral':
                check(health['status']=='degraded' and not health['persistence']['production_ready'],'Ephemeral storage claims production readiness')
            results['persistence']=health['persistence']
        finally:
            for token_id in issued:
                api.delete('/system/access-tokens/'+token_id,headers=admin).raise_for_status()
            for name in created:
                api.delete('/system/brains/'+name,headers=admin).raise_for_status()
                api.patch('/system/workspaces/'+name,headers=admin,json={'archived':True}).raise_for_status()
        after=api.get('/system/brains-list',headers=admin);after.raise_for_status()
        check({(b['name_key'],b['id']) for b in after.json()}==identities,'Qualification changed legitimate resources')
    results['result']='passed'
    print(json.dumps(results,sort_keys=True))

if __name__=='__main__':qualify()
