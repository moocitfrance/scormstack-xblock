const test = require('node:test');
const assert = require('node:assert/strict');
const Engine = require('../scormstack_xblock/static/runtime.js');

test('1.2 lifecycle, resume, read-only identity and independent blocks', () => {
  const writes = [];
  const one = new Engine({version: '1.2', data: {'cmi.suspend_data': 'saved'}, learner_id: '1', changed: (x) => writes.push(x)});
  const two = new Engine({version: '1.2'});
  assert.equal(one.api.LMSGetValue('cmi.suspend_data'), '');
  assert.equal(one.api.LMSGetLastError(), '301');
  assert.equal(one.api.LMSInitialize(''), 'true');
  assert.equal(two.api.LMSInitialize(''), 'true');
  assert.equal(one.api.LMSGetValue('cmi.core.entry'), 'resume');
  assert.equal(one.api.LMSGetValue('cmi.suspend_data'), 'saved');
  assert.equal(one.api.LMSSetValue('cmi.core.student_id', '2'), 'false');
  assert.equal(one.api.LMSSetValue('cmi.suspend_data', 'answer'), 'true');
  assert.equal(two.api.LMSGetValue('cmi.suspend_data'), '');
  assert.equal(one.api.LMSCommit(''), 'true');
  assert.equal(one.api.LMSFinish(''), 'true');
  assert.equal(one.api.LMSSetValue('cmi.suspend_data', 'too late'), 'false');
  assert.equal(writes.at(-1)['cmi.suspend_data'], 'answer');
});

test('2004 completion, score validation and interactions count', () => {
  const engine = new Engine({version: '2004'}), api = engine.api;
  api.Initialize('');
  assert.equal(api.SetValue('cmi.completion_status', 'passed'), 'false');
  assert.equal(api.SetValue('cmi.completion_status', 'completed'), 'true');
  assert.equal(api.SetValue('cmi.score.scaled', 'NaN'), 'false');
  assert.equal(api.SetValue('cmi.score.scaled', '1.5'), 'false');
  assert.equal(api.SetValue('cmi.score.scaled', '.8'), 'true');
  api.SetValue('cmi.interactions.0.id', 'question-1');
  assert.equal(api.GetValue('cmi.interactions._count'), '1');
  assert.equal(api.Terminate(''), 'true');
});

test('state limit prevents unsavable snapshots', () => {
  const engine = new Engine({version: '1.2'});
  engine.api.LMSInitialize('');
  assert.equal(engine.api.LMSSetValue('cmi.suspend_data', 'x'.repeat(1024 * 1024)), 'false');
  assert.equal(engine.api.LMSGetValue('cmi.suspend_data'), '');
});
