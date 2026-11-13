// NHL live engine (docs/nhl/engine.js): node --test tests/nhl_engine.test.js
// Fixture: Carolina @ Seattle, Nov 12 2026 (ESPN summary, trimmed). CAR won 4-1 (2-0 after one, 3-0 after two).
'use strict';
const test = require('node:test');
const assert = require('node:assert');
const E = require('../docs/nhl/engine.js');
const S = require('./fixtures/nhl_car_sea_2026-11-12.json');
const ctx = { fav: 'CAR', date: '2026-11-12', favB2B: false, favGoalieOk: true, kalshi: { CAR: 0.9 } };
const at = t => E.analyze(S, ctx, { uptoT: t });

test('final and period readouts', () => {
  const r = E.analyze(S, ctx);
  assert.strictEqual(r.state, 'post');
  assert.strictEqual(r.decision.headline, 'Final: CAR won');
  assert.strictEqual(r.readouts.length, 2);
  assert.match(r.readouts[0].text, /^1st period: CAR 2-0 SEA/);
  assert.match(r.readouts[0].trend, /CAR controlling play/);
});

test('windows: readouts, lean, decide 10:00-5:00, read only after', () => {
  assert.strictEqual(at(1200).phase.key, 'end1');
  assert.strictEqual(at(2400).phase.key, 'end2');
  assert.strictEqual(at(2700).phase.key, 'p3early');
  assert.strictEqual(at(3000).phase.key, 'decide');
  assert.strictEqual(at(3300).phase.key, 'decide');
  assert.strictEqual(at(3400).phase.key, 'late');
});

test('BET only in the window, only with a 2+ goal lead', () => {
  assert.strictEqual(at(2400).decision.action, 'LEAN');
  assert.strictEqual(at(2700).decision.action, 'LEAN');
  const r = at(3060);
  assert.strictEqual(r.decision.action, 'BET');
  assert.strictEqual(r.decision.pick, 'CAR');
  assert.notStrictEqual(at(3400).decision.action, 'BET');
});

test('checklist at 10:00 of the 3rd', () => {
  const r = at(3100);
  assert.ok(r.checklist);
  assert.strictEqual(r.checklist.total, 6);
  assert.ok(r.checklist.items[0].ok);
  assert.match(r.checklist.verdict, /of 6/);
});

test('one-goal lead is never a bet', () => {
  // cut the fixture after the first goal (CAR 1-0)
  const g1 = S.plays.findIndex(p => p.type.text === 'Goal');
  const g = S.plays[g1];
  const rest = S.plays.slice(g1 + 1).filter(p => p.type.text !== 'Goal').map(p => ({ ...p, awayScore: g.awayScore, homeScore: g.homeScore }));
  const S1 = { header: S.header, plays: S.plays.slice(0, g1 + 1).concat(rest) };
  const r = E.analyze(S1, ctx, { uptoT: 3100 });
  assert.notStrictEqual(r.decision.action, 'BET');
  assert.ok(r.decision.gates.some(x => /One-goal lead/.test(x)));
});

test('expected goals: close shots worth more than far ones', () => {
  const near = E.xg({ coordinate: { x: 80, y: 2 }, text: 'Wrist Shot' }, false);
  const far = E.xg({ coordinate: { x: 30, y: 20 }, text: 'Slap Shot' }, false);
  assert.ok(near > 0.1 && far < 0.05, `${near} ${far}`);
});

test('1st-period hold check: one-goal leads only, information only', () => {
  assert.strictEqual(at(1200).hold1.level, 'up2');               // CAR 2-0 after one
  assert.strictEqual(at(1200).hold1.applies, false);
  assert.strictEqual(at(600).hold1, null);                      // before the end of the 1st
  const g1 = S.plays.findIndex(p => p.type.text === 'Goal');
  const g = S.plays[g1];
  const rest = S.plays.slice(g1 + 1).filter(p => p.type.text !== 'Goal').map(p => ({ ...p, awayScore: g.awayScore, homeScore: g.homeScore }));
  const S1 = { header: S.header, plays: S.plays.slice(0, g1 + 1).concat(rest) };
  const run = c => E.analyze(S1, { ...ctx, ...c }, { uptoT: 1200 }).hold1;
  const h = run({ score: 24, favDefRank: 3, dogDefRank: 27 });
  assert.strictEqual(h.lead, 1);
  assert.strictEqual(h.level, 'strong');
  assert.match(h.text, /84%/);
  assert.match(h.text, /never a bet/);
  assert.strictEqual(run({ score: 24, favDefRank: 12, dogDefRank: 27 }).level, 'meets');
  assert.strictEqual(run({ score: 20, favDefRank: 3, dogDefRank: 27 }).level, 'none');
  const unknown = run({});
  assert.strictEqual(unknown.level, 'none');
  assert.strictEqual(unknown.items[0].ok, null);
  // the read itself is unchanged: a one-goal lead never becomes a bet
  assert.notStrictEqual(E.analyze(S1, { ...ctx, score: 30, favDefRank: 1, dogDefRank: 32 }, { uptoT: 3100 }).decision.action, 'BET');
});

test('hold check after two: met check, lead still 1 or 2', () => {
  assert.strictEqual(at(2400).hold2, null);                     // CAR led 2-0 after one: check didn't apply
  const g1 = S.plays.findIndex(p => p.type.text === 'Goal');
  const g = S.plays[g1];
  const rest = S.plays.slice(g1 + 1).filter(p => p.type.text !== 'Goal').map(p => ({ ...p, awayScore: g.awayScore, homeScore: g.homeScore }));
  const S1 = { header: S.header, plays: S.plays.slice(0, g1 + 1).concat(rest) };
  const c = { ...ctx, score: 24, favDefRank: 3, dogDefRank: 27 };
  assert.strictEqual(E.analyze(S1, c, { uptoT: 1200 }).hold2, null);   // not before the end of the 2nd
  const h = E.analyze(S1, c, { uptoT: 2400 }).hold2;
  assert.strictEqual(h.lead, 1);
  assert.strictEqual(h.holds, true);
  assert.strictEqual(h.backtest.pct, 90);
  assert.strictEqual(h.shots2.length, 2);
  assert.match(h.text, /Still holding/);
  assert.strictEqual(E.analyze(S1, { ...c, score: 18 }, { uptoT: 2400 }).hold2, null);   // check not met: no follow-up
});
