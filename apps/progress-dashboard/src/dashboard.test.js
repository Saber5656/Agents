import test from 'node:test';
import assert from 'node:assert/strict';
import { filterActiveTasks, formatRecordAge, safeExternalUrl, summarizeSnapshot, createRefreshController } from './dashboard.js';

test('selects fresh active work in stable freshness order and caps first display at three', () => {
  const tasks = [
    { id: 'old', category: 'active', freshness: 'old', updated_at: '2026-10-29T10:00:00Z' },
    { id: 'two', category: 'active', freshness: 'fresh', updated_at: '2026-10-30T09:00:00Z' },
    { id: 'one', category: 'active', freshness: 'fresh', updated_at: '2026-10-30T10:30:00Z' },
    { id: 'three', category: 'attention', freshness: 'fresh', updated_at: '2026-10-30T08:00:00Z' },
    { id: 'four', category: 'active', freshness: 'fresh', updated_at: '2026-10-30T07:00:00Z' },
  ];
  assert.deepEqual(filterActiveTasks(tasks, 3, Date.parse('2026-10-30T11:00:00Z')).map((task) => task.id), ['one', 'two', 'four']);
});

test('saved active work ages out without a successful refresh and retains its original record', () => {
  const record = {id:'running', category:'active', lifecycle:'running', freshness:'fresh', updated_at:'2026-10-29T11:00:00Z'};
  const snapshot = {tasks:[record],sources:[{name:'tasks',state:'available'}],stale:true};
  const after = summarizeSnapshot(snapshot, Date.parse('2026-10-30T11:00:01Z'));
  assert.equal(after.active.length, 0);
  assert.equal(after.all[0].freshness, 'old');
  assert.equal(record.freshness, 'fresh');
  assert.equal(after.all[0].updated_at, record.updated_at);
});

test('does not convert unknown or future dates into misleading ages', () => {
  assert.equal(formatRecordAge('not-a-date', Date.parse('2026-10-30T10:00:00Z')), 'Age unknown');
  assert.equal(formatRecordAge('2026-10-30T11:00:00Z', Date.parse('2026-10-30T10:00:00Z')), 'Recorded in the future');
});

test('only accepts external HTTPS evidence URLs', () => {
  assert.equal(safeExternalUrl('https://example.com/evidence'), 'https://example.com/evidence');
  assert.equal(safeExternalUrl('http://example.com'), null);
  assert.equal(safeExternalUrl('/api/evidence/foo'), null);
  assert.equal(safeExternalUrl('javascript:alert(1)'), null);
  assert.equal(safeExternalUrl('https://user:secret@example.com'), null);
});

test('real pending-user states stay visible; missing sources never imply no decisions', () => {
  const base = {tasks: [{id: 'user', category: 'attention', lifecycle: 'awaiting_user'},
    {id: 'login', category: 'attention', lifecycle: 'auth_failed'},
    {id: 'fix', category: 'backlog', lifecycle: 'quality_failed'}],
    sources: [{name:'tasks',state:'available'}], warnings:[], stale:false};
  assert.deepEqual(summarizeSnapshot(base).attention.map(t => t.id), ['user', 'login']);
  assert.equal(summarizeSnapshot({...base,tasks:[]}).decisionsKnown, true);
  assert.equal(summarizeSnapshot({...base,tasks:[],sources:[{name:'tasks',state:'unavailable'}]}).decisionsKnown, false);
  assert.equal(summarizeSnapshot({...base,stale:true}).decisionsKnown, false);
});

test('refresh failure and timeout retain actual prior observation time, and concurrent reads share one request', async () => {
  let calls = 0;
  const prior = {generated_at:'2026-10-30T10:00:00Z',tasks:[],sources:[]};
  const changes = [];
  const loader = createRefreshController({timeoutMs:15,onChange:s => changes.push(s),fetcher:async () => {
    calls++;
    if (calls === 1) return prior;
    return new Promise(() => {});
  }});
  await loader.refresh();
  const one = loader.refresh(); const two = loader.refresh();
  assert.equal(one, two);
  await one;
  assert.equal(calls, 2);
  assert.equal(changes.at(-1).snapshot.generated_at, prior.generated_at);
  assert.ok(changes.at(-1).error);
  assert.equal(changes.at(-1).loading, false);
  loader.dispose();
});

test('stale server snapshot keeps its timestamp and is not called a successful fresh check', async () => {
  const changes=[];
  const stale={generated_at:'2026-09-01T00:00:00Z',tasks:[],sources:[],stale:true};
  const loader=createRefreshController({fetcher:async()=>stale,onChange:s=>changes.push(s)});
  await loader.refresh();
  assert.equal(changes.at(-1).snapshot.generated_at,stale.generated_at);
  assert.equal(summarizeSnapshot(changes.at(-1).snapshot).decisionsKnown,false);
  loader.dispose();
});

test('disposed requests do not update an unmounted view', async () => {
  let resolve; const changes=[];
  const loader=createRefreshController({fetcher:()=>new Promise(r=>resolve=r),onChange:s=>changes.push(s)});
  const pending=loader.refresh(); loader.dispose();
  resolve({tasks:[],sources:[]}); await pending;
  assert.equal(changes.length,1);
});
