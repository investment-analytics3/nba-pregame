// NFL live engine tests: node --test tests/nfl_engine.test.js
// Fixture: CIN @ ATL, 2026 Week 9 (real ESPN play-by-play, trimmed). nflverse reference
// numbers for the same game are hard-coded below.
'use strict';
const test = require('node:test');
const assert = require('node:assert');
const path = require('path');
const E = require('../docs/nfl/engine.js');
const ep = require('../docs/nfl/ep.json');
const game = require('./fixtures/nfl_cin_atl_2026w9.json');

const teams = { away: { id: '4', abbr: 'CIN' }, home: { id: '1', abbr: 'ATL' } };
const ctx = { fav: 'CIN', dog: 'ATL', fav_defense_top10: true, ep,
  qbs: { CIN: { regular: 'J.Burrow', rank: 4 }, ATL: { regular: 'M.Penix', rank: 15 } }, kalshi: { CIN: 0.9, ATL: 0.1 } };

test('estimated EPA and success track nflverse for the same game', () => {
  const nflverse = { 'ATL H1': [33, 0.156, 0.485], 'ATL H2': [29, 0.015, 0.448], 'CIN H1': [31, 0.645, 0.581], 'CIN H2': [25, 0.023, 0.520] };
  const agg = {};
  E.normPlays(game, teams, ep).filter(p => p.scrim && p.epa != null).forEach(p => {
    const k = teams[p.off].abbr + (p.period <= 2 ? ' H1' : ' H2');
    agg[k] = agg[k] || { n: 0, epa: 0, s: 0 }; agg[k].n++; agg[k].epa += p.epa; agg[k].s += p.success ? 1 : 0;
  });
  for (const [k, [n, epa, sr]] of Object.entries(nflverse)) {
    assert.ok(Math.abs(agg[k].n - n) <= 2, `${k} plays ${agg[k].n} vs ${n}`);
    assert.ok(Math.abs(agg[k].epa / agg[k].n - epa) <= 0.06, `${k} EPA ${agg[k].epa / agg[k].n} vs ${epa}`);
    assert.ok(Math.abs(agg[k].s / agg[k].n - sr) <= 0.05, `${k} SR`);
  }
});

test('runs: 10+ unanswered on two scores; answers: TD is strong, FG is not', () => {
  const mk = (side, pts, i) => ({ side, pts, td: pts >= 6, t: i * 100, period: 1, remain: 900 - i * 100, away: 0, home: 0 });
  let a = 0, h = 0;
  const ev = [mk('away', 7, 1), mk('away', 3, 2), mk('home', 3, 3), mk('home', 7, 4)].map(e => {
    if (e.side === 'away') a += e.pts; else h += e.pts; return Object.assign(e, { away: a, home: h });
  });
  const runs = E.findRuns(ev);
  assert.deepStrictEqual(runs.map(r => [r.side, r.pts]), [['away', 10], ['home', 10]]);
  const ans = E.findAnswer(ev, runs[0]);
  assert.strictEqual(ans.side, 'home'); assert.strictEqual(ans.pts, 10); assert.ok(ans.strong);
  const fgOnly = E.findAnswer(ev.slice(0, 3), runs[0]);
  assert.strictEqual(fgOnly.pts, 3); assert.ok(!fgOnly.strong);
});

test('timing windows', () => {
  assert.strictEqual(E.phaseOf('in', 2, 0, 'STATUS_HALFTIME').key, 'scout');
  assert.strictEqual(E.phaseOf('in', 3, 0).key, 'end3');
  assert.strictEqual(E.phaseOf('in', 4, 700).key, 'primary');
  assert.strictEqual(E.phaseOf('in', 4, 500).key, 'confirm');
  assert.strictEqual(E.phaseOf('in', 4, 320).key, 'late');
  assert.strictEqual(E.phaseOf('in', 4, 299).key, 'under5');
});

test('replay: halftime is scouting; CIN 17-0 run; ATL answers with a TD in the 3rd', () => {
  const half = E.analyze(game, ctx, { upto: { period: 2, remain: 0 } });
  assert.strictEqual(half.decision.action, 'SCOUT');
  assert.strictEqual(half.score, 'CIN 24 – ATL 10');
  assert.match(half.answerNote, /CIN's 17-0 run .* is unanswered/);
  assert.ok(half.earlyPath.full, 'CIN up 14 at the half with control and QB edge (defense given as top 10)');
  const q4 = E.analyze(game, ctx, { upto: { period: 4, remain: 600 } });
  assert.strictEqual(q4.whoAnswered, 'ATL');
  assert.strictEqual(q4.sides.home.answered, true);
  assert.notStrictEqual(q4.decision.action, 'BET');           // ATL trails: an A is a comeback signal, not a bet
  assert.ok(q4.sides.away.gates.includes('No answered run = B maximum'));
  const fin = E.analyze(game, ctx);
  assert.strictEqual(fin.decision.action, 'FINAL');
  assert.match(fin.logLine, /CIN @ ATL \| Final \| 34-17/);
});

test('QB read: backup in = 0; elite starter playing well = +2', () => {
  const q4 = E.analyze(game, Object.assign({}, ctx, { qbs: { CIN: { regular: 'J.Flacco', rank: 4 }, ATL: ctx.qbs.ATL } }), { upto: { period: 3, remain: 0 } });
  assert.strictEqual(q4.qb.away.score, 0);
  const ok = E.analyze(game, ctx, { upto: { period: 2, remain: 0 } });
  assert.strictEqual(ok.qb.away.score, 2);
});

test('price check never changes the grade', () => {
  const a = E.analyze(game, ctx, { upto: { period: 4, remain: 500 } });
  const b = E.analyze(game, Object.assign({}, ctx, { kalshi: null }), { upto: { period: 4, remain: 500 } });
  assert.strictEqual(a.decision.grade, b.decision.grade);
  assert.ok(a.price === null || typeof a.price.pricedIn === 'boolean');
});
