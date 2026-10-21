import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import ts from 'typescript';

test('actual client exchanges token once, persists no credential, restores cookie session', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'brainapi-auth-client-'));
  const storage = () => { const values = new Map(); return { values, getItem: (k) => values.get(k) ?? null, setItem: (k,v) => values.set(k,v), removeItem: (k) => values.delete(k) }; };
  globalThis.localStorage = storage(); globalThis.sessionStorage = storage();
  sessionStorage.setItem('brainapi-console-session', JSON.stringify({pat:'old-sensitive-value'}));
  globalThis.window = { location: {origin:'https://brain.example',pathname:'/console/login'} };
  const requests = [];
  let ingestStatus = 422;
  globalThis.fetch = async (url, options={}) => {
    requests.push({url,options});
    if (url.endsWith('/auth/session') || url.endsWith('/auth/me')) return new Response(JSON.stringify({is_system:true,brain_id:null,csrf_token:'csrf-test-value'}),{status:200});
    if (url.endsWith('/ingest/')) return new Response(JSON.stringify({detail:'bad request'}),{status:ingestStatus});
    return new Response(JSON.stringify({workspaces:[]}),{status:200});
  };
  try {
    for (const name of ['auth','api']) {
      const input = fs.readFileSync(new URL(`./${name}.ts`,import.meta.url),'utf8');
      const compiled = ts.transpileModule(input,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText.replace('"./auth"','"./auth.mjs"');
      fs.writeFileSync(path.join(dir,`${name}.mjs`),compiled);
    }
    fs.copyFileSync(new URL('./ingestContract.mjs',import.meta.url),path.join(dir,'ingestContract.mjs'));
    const auth = await import(pathToFileURL(path.join(dir,'auth.mjs')));
    const api = await import(pathToFileURL(path.join(dir,'api.mjs')));
    const session = await api.connectSession('https://brain.example','submitted-private-example');
    auth.saveSession(session);
    assert.equal(session.brainId,null);
    assert.ok(!JSON.stringify(session).includes('submitted-private-example'));
    assert.equal(sessionStorage.getItem('brainapi-console-session'),null);
    assert.ok(!JSON.stringify([...localStorage.values]).includes('submitted-private-example'));
    await api.fetchWorkspaces();
    assert.deepEqual(requests.map((r)=>r.url),['https://brain.example/auth/session','https://brain.example/system/workspaces']);
    assert.ok(!requests[1].options.headers.has('Authorization'));
    assert.ok(!requests[1].options.headers.has('BrainPAT'));
    assert.equal(requests[1].options.credentials,'include');
    for (const status of [422,500,202,200]) {
      ingestStatus=status;
      await assert.rejects(api.apiFetch('/ingest/',{method:'POST',body:'{}'}));
    }
    assert.ok(requests.every((r)=>!r.url.includes('/tasks/') && !r.url.includes('/meta/entity-labels')));
    assert.equal((await api.restoreSession()).brainId,null);
  } finally { fs.rmSync(dir,{recursive:true,force:true}); }
});
