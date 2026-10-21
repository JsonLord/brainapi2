import test from 'node:test';
import assert from 'node:assert/strict';
import { requireAcceptedTask } from './ingestContract.mjs';
for (const [status, payload] of [[422, {detail:'invalid'}], [500, {}], [200, {task_id:'valid'}], [202, {}], [202, {task_id:null}], [202, {task_id:'None'}], [202, {task_id:'null'}], [202, {task_id:''}]]) {
  test(`no task requests after ${status}/${JSON.stringify(payload)}`, () => {
    const requests = [];
    assert.throws(() => {
      const id = requireAcceptedTask(status, payload);
      requests.push(`/tasks/${id}`);
    }, /INGESTION_CONTRACT_ERROR/);
    assert.deepEqual(requests, []);
  });
}
test('accepted task is pollable', () => assert.equal(requireAcceptedTask(202, {task_id:'task-123'}), 'task-123'));
