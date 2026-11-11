// College basketball live engine (docs/cbb/engine.js): node --test tests/cbb_engine.test.js
// Fixture: Florida @ Florida State, Nov 10 2026 (ESPN summary, trimmed), FLA won 81-72.
'use strict';
const test = require('node:test');
const assert = require('node:assert');
const E = require('../docs/cbb/engine.js');
const S = require('./fixtures/cbb_fla_fsu_2026-11-10.json');
const ctx = { fav: 'FLA', date: '2026-11-10', league: { opp_efg: [40, 45, 50, 55], def: [90, 100, 110] }, favB2B: false };

test('halves, not quarters: clock labels and the final', () => {
  const r = E.analyze(S, ctx);
  assert.strictEqual(r.state, 'post');
  assert.strictEqual(r.decision.headline, 'Final: FLA won');
  assert.match(E.analyze(S, ctx, { uptoT: 900 }).clockLabel, /1st half$/);
  assert.match(E.analyze(S, ctx, { uptoT: 2000 }).clockLabel, /2nd half$/);
});

test('timing windows: scouting, lean until 8:00, bet window 8:00-4:00, read only after', () => {
  const at = t => E.analyze(S, ctx, { uptoT: t }).phase;
  assert.strictEqual(at(600).key, 'scout');
  assert.strictEqual(at(1500).key, 'q3');
  assert.strictEqual(at(1500).canBet, false);
  const p = at(1980);                         // ~7:00 left in the 2nd half
  assert.strictEqual(p.key, 'primary');
  assert.strictEqual(p.canBet, true);
  assert.strictEqual(at(2150).key, 'confirm');
  assert.strictEqual(at(2300).key, 'under4');
  assert.strictEqual(at(2300).canBet, false);
});

test('no BET outside the 8:00-4:00 window', () => {
  for (const t of [600, 1200, 1700, 1850, 2300]) {
    assert.notStrictEqual(E.analyze(S, ctx, { uptoT: t }).decision.action, 'BET', `t=${t}`);
  }
});

test('checklist is taken at 10:00 of the 2nd half with college cutoffs', () => {
  const r = E.analyze(S, ctx, { uptoT: 2100 });
  assert.ok(r.checklist);
  assert.strictEqual(r.checklist.total, 7);
  assert.ok(r.checklist.items[0].label.includes('10:00 of the 2nd half'));
  assert.ok(r.checklist.items[1].label.includes('top 50'));
  assert.strictEqual(typeof r.favorite.leadEnd3, 'number');
  assert.strictEqual(E.CHECK_T, 1800);
});

test('log line uses 1H/2H', () => {
  assert.match(E.analyze(S, ctx, { uptoT: 2000 }).logLine, / 2H \|/);
});
