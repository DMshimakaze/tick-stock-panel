import assert from 'node:assert/strict';
import test from 'node:test';
import { statusModel } from '../src/status_view.mjs';

const at = 1795680120000;
const view = { result_id: '1', focus: { revision: 4, taxonomy: { run: 'dc-a' } } };
const coverage = { expected: 5, ready_15m: 2, unready_15m: 3, candidate_symbols: 1,
  ready_all: 0, ready_by_timeframe: { '15m': 2, daily: 4 } };
const state = { focus_revision: 4, taxonomy_run: 'dc-a', result_id: '1', worker_alive: true, status_at_ms: at,
  scope_count: 5, saved_sectors: 2, active_symbols: 3, coverage };

test('live A/B/C/F/D/W and analysis have their own status without hiding coverage', () => {
  const stages = [
    ['A', 'COLLECTING_NATIVE15', '更新行情'], ['B', 'COLLECTING_NATIVE15', '补齐历史'],
    ['C', 'COLLECTING_NATIVE15', '补充历史'], ['F', 'CHECKING_FACTORS', '核验复权'],
    ['D', 'COLLECTING_DAILY', '更新日线'], ['W', 'COLLECTING_DAILY', '补齐日线'],
  ];
  for (const [phase, active_operation, label] of stages) {
    const live = { ...state, phase, work_stage: phase, active_operation };
    const current = statusModel(live, view, { now: at });
    assert.equal(current.label, label);
    assert.equal(current.running, true);
    assert.equal(current.taskNote, '本批 3 只');
    assert.equal(current.coverage, coverage);
    const analysis = statusModel({ ...live, active_operation: 'ANALYZING_MA55' }, view, { now: at });
    assert.equal(analysis.label, '计算 MA55');
    assert.match(analysis.taskNote, new RegExp(label));
  }
});

test('idle, quota, host, failure and stale status have no running indicator', () => {
  for (const [phase, label] of [['WAITING_NEXT_15M', '等待下一根'], ['QUOTA_WAIT', '等待额度'],
    ['RATE_LIMITED', '限流等待'], ['HOST_BUSY', '等待宿主'], ['ERROR', '运行异常'],
    ['SOURCE_UNAVAILABLE', '行情不可用'], ['CREDENTIAL_REQUIRED', '凭据异常']]) {
    const result = statusModel({ ...state, phase, work_stage: 'C', active_operation: 'IDLE' }, view, { now: at });
    assert.equal(result.label, label);
    assert.equal(result.running, false);
    assert.equal(result.coverage, coverage);
    if (phase === 'QUOTA_WAIT') assert.equal(result.taskNote, '补充历史');
  }
  const collecting = { ...state, phase: 'A', active_operation: 'COLLECTING_NATIVE15' };
  for (const [options, label] of [[{ now: at + 46_000 }, '状态过期'],
    [{ now: at, statusError: '超时' }, '读取失败'], [{ now: at, viewError: '超时' }, '读取失败']]) {
    const result = statusModel(collecting, view, options);
    assert.equal(result.label, label);
    assert.equal(result.running, false);
    assert.equal(result.coverage, coverage);
  }
});

test('unknown, changed focus and changed result IDs never borrow old coverage', () => {
  assert.equal(statusModel(null, null).label, '状态待确认');
  assert.equal(statusModel(null, null).total, undefined);
  for (const changes of [{ focus_revision: 5 }, { taxonomy_run: 'dc-b' }, { result_id: '2' }, { coverage: null }]) {
    const result = statusModel({ ...state, ...changes }, view, { now: at });
    assert.equal(result.coverage, null);
  }
  const empty = statusModel({ ...state, scope_count: 0, saved_sectors: 0, phase: 'SCOPE_PAUSED',
    result_id: null, focus_revision: 0 }, null, { now: at });
  assert.equal(empty.label, '未启用监控');
  assert.equal(empty.total, 0);
  assert.equal(empty.coverage, null);
});
