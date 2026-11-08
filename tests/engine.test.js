// Live engine rules.  Run: node --test tests/
const test = require('node:test');
const assert = require('node:assert');
const E = require('../docs/live/engine.js');

// ---- tiny synthetic game builder -------------------------------------------
// Away AAA (id 1, players a1..a8), home HHH (id 2, players h1..h8); starters 1-5.
function game(script, status) {
  const plays = [];
  let away = 0, home = 0, n = 0;
  const T = side => (side === 'A' ? '1' : '2');
  const push = (period, clock, side, type, extra) => {
    plays.push(Object.assign({
      id: String(++n), type: { text: type }, text: extra.text || type, period: { number: period },
      clock: { displayValue: clock }, team: side ? { id: T(side) } : undefined,
      awayScore: away, homeScore: home, scoringPlay: false, scoreValue: 0, shootingPlay: false,
      participants: (extra.ath || []).map(id => ({ athlete: { id } })), coordinate: extra.coordinate,
    }, extra.raw || {}));
  };
  for (const s of script) {
    const [period, clock, side, what, who] = s;
    const p = who || (side === 'A' ? 'a1' : 'h1');
    if (what === 2 || what === 3 || what === 'L') {
      const pts = what === 'L' ? 2 : what;
      if (side === 'A') away += pts; else home += pts;
      push(period, clock, side, what === 'L' ? 'Layup Shot' : (pts === 3 ? 'Jump Shot' : 'Jump Shot'), {
        text: `${p} makes ${pts === 3 ? '26-foot three point jumper' : (what === 'L' ? 'layup' : '15-foot jumper')}`,
        ath: [p], coordinate: what === 'L' ? { x: 25, y: 1 } : (pts === 3 ? { x: 25, y: 27 } : { x: 25, y: 16 }),
        raw: { scoringPlay: true, scoreValue: pts, shootingPlay: true, pointsAttempted: pts },
      });
    } else if (what === 'OREB' || what === 'DREB') {
      push(period, clock, side, what === 'OREB' ? 'Offensive Rebound' : 'Defensive Rebound', { ath: [p] });
    } else if (what === 'TO') {
      push(period, clock, side, 'Lost Ball Turnover', { ath: [p] });
    } else if (what === 'SUB') {
      const [inn, out] = who;
      push(period, clock, side, 'Substitution', { text: `${inn} enters the game for ${out}`, ath: [inn, out] });
    } else if (what === 'END') {
      push(period, '0.0', null, 'End Period', {});
    }
  }
  const players = side => {
    const pre = side === 'A' ? 'a' : 'h';
    return { team: { id: T(side), abbreviation: side === 'A' ? 'AAA' : 'HHH' },
      statistics: [{ labels: ['MIN', 'PTS'], athletes: [1, 2, 3, 4, 5, 6, 7, 8].map(i => ({
        athlete: { id: `${pre}${i}`, displayName: `${pre}${i}`, shortName: `${pre}${i}` }, starter: i <= 5, stats: ['20', '10'] })) }] };
  };
  return {
    header: { id: '99', competitions: [{ id: '99', status: status || { period: 4, displayClock: '9:00', type: { state: 'in' } },
      competitors: [
        { homeAway: 'away', team: { id: '1', abbreviation: 'AAA', name: 'Aways', location: 'Away' }, score: String(away) },
        { homeAway: 'home', team: { id: '2', abbreviation: 'HHH', name: 'Homes', location: 'Home' }, score: String(home) },
      ] }] },
    boxscore: { players: [players('A'), players('H')] },
    plays,
  };
}
const at = (period, clock) => ({ period, displayClock: clock, type: { state: 'in' } });
// Control helper: n offensive + defensive rebounds and layups for a side in Q4
const dominate = (side, n, clock = '11:00') =>
  Array.from({ length: n }, () => [[4, clock, side, 'OREB'], [4, clock, side, 'DREB']]).flat();

// ---- runs and answers --------------------------------------------------------
test('6-0 and 8-2 are runs; 6-3 is not', () => {
  const ev = s => E._internal.scoringEvents(E._internal.normPlays(s, E._internal.teamsOf(s)));
  const a = game([[1, '11:00', 'A', 2], [1, '10:40', 'A', 2], [1, '10:20', 'A', 2]]);
  assert.strictEqual(E.findRuns(ev(a))[0].label, '6-0');
  const b = game([[1, '11:00', 'A', 2], [1, '10:50', 'H', 2], [1, '10:40', 'A', 3], [1, '10:20', 'A', 3]]);
  assert.strictEqual(E.findRuns(ev(b)).filter(r => r.side === 'away')[0].label, '8-2');
  const c = game([[1, '11:00', 'A', 3], [1, '10:50', 'H', 3], [1, '10:40', 'A', 3]]);
  assert.strictEqual(E.findRuns(ev(c)).filter(r => r.kind === 'run').length, 0);
});

test('lead change followed by 2 straight scores is a run', () => {
  const s = game([[1, '11:00', 'H', 2], [1, '10:50', 'A', 3], [1, '10:40', 'A', 2], [1, '10:30', 'A', 2]]);
  const ev = E._internal.scoringEvents(E._internal.normPlays(s, E._internal.teamsOf(s)));
  const r = E.findRuns(ev).find(x => x.side === 'away');
  assert.ok(r);
});

test('answer = 4-0 after the run', () => {
  const s = game([[3, '5:00', 'H', 2], [3, '4:40', 'H', 2], [3, '4:20', 'H', 2], [3, '4:00', 'A', 2], [3, '3:40', 'A', 2]],
    at(3, '3:30'));
  const r = E.analyze(s, {});
  assert.strictEqual(r.whoAnswered, 'AAA');
  assert.match(r.answerNote, /AAA answered HHH's 6-0 with 4-0/);
});

test('an earlier answer does not carry forward once the opponent makes a new run', () => {
  const s = game([
    [3, '8:00', 'H', 2], [3, '7:40', 'H', 2], [3, '7:20', 'H', 2],          // HHH 6-0
    [3, '7:00', 'A', 2], [3, '6:40', 'A', 2],                                // AAA answers 4-0
    [3, '6:00', 'H', 3], [3, '5:40', 'H', 3], [3, '5:20', 'H', 2],          // HHH new 8-0
  ], at(3, '5:00'));
  const r = E.analyze(s, {});
  assert.strictEqual(r.sides.away.answered, false);   // AAA's 4-0 answer no longer counts
  assert.strictEqual(r.whoAnswered, null);            // HHH's new 8-0 is unanswered
  assert.match(r.answerNote, /HHH's 8-0 run is unanswered/);
});

// ---- grade gates -------------------------------------------------------------
test('no answered run = B maximum, even with control and a closer edge', () => {
  const s = game([[4, '11:30', 'A', 'L'], [4, '11:20', 'A', 'L'], ...dominate('A', 4)], at(4, '9:30'));
  const r = E.analyze(s, { closers: { AAA: { closers: ['a1', 'a2'], elite: ['a1'] } } });
  assert.ok(['B', 'C'].includes(r.sides.away.grade));
  assert.ok(r.sides.away.gates.includes('No answered run = B maximum'));
  assert.notStrictEqual(r.decision.action, 'BET');
});

test('A+: answered + control + closer edge + lead stable or growing -> BET in the window', () => {
  const s = game([
    [3, '6:00', 'A', 2], [3, '5:00', 'A', 2], [3, '4:00', 'A', 2], [3, '3:00', 'A', 2], [3, '2:00', 'A', 2], // AAA up 10
    [3, '1:40', 'H', 3], [3, '1:20', 'H', 3], [3, '1:00', 'H', 2],                                            // HHH 8-0 run
    [4, '11:50', 'A', 'L'], [4, '11:30', 'A', 'L'], [4, '11:10', 'A', 'L'],                                   // AAA answers 6-0
    ...dominate('A', 4, '10:30'), [4, '9:40', 'A', 'L', 'a2'],
  ], at(4, '9:30'));
  const ctx = { closers: { AAA: { closers: ['a1', 'a2'], elite: ['a1'] }, HHH: { closers: ['h7', 'h8'] } } };
  const r = E.analyze(s, ctx);
  assert.strictEqual(r.sides.away.answered, true);
  assert.strictEqual(r.sides.away.grade, 'A+');
  assert.strictEqual(r.decision.action, 'BET');
  assert.strictEqual(r.decision.headline, 'BET AAA ML');
});

test('closer picture unclear at 10:00-8:00 caps the grade at B', () => {
  const s = game([
    [3, '6:00', 'A', 2], [3, '5:00', 'A', 2], [3, '4:00', 'A', 2], [3, '3:00', 'A', 2], [3, '2:00', 'A', 2],
    [3, '1:40', 'H', 3], [3, '1:20', 'H', 3], [3, '1:00', 'H', 2],
    [4, '11:50', 'A', 'L'], [4, '11:30', 'A', 'L'], [4, '11:10', 'A', 'L'], ...dominate('A', 4, '10:30'),
  ], at(4, '9:30'));
  // listed closer who isn't in the box score -> on-floor status unknown
  const r = E.analyze(s, { closers: { AAA: { closers: ['Nobody Here'], elite: [] } } });
  assert.strictEqual(r.sides.away.grade, 'B');
  assert.ok(r.sides.away.gates.some(g => /unclear/.test(g)));
});

test('a trailing team with an A grade is not a bet', () => {
  const s = game([
    [3, '6:00', 'H', 3], [3, '5:30', 'H', 3], [3, '5:00', 'H', 3], [3, '4:30', 'H', 3], [3, '4:00', 'H', 3], // HHH 15-0
    [4, '11:50', 'A', 'L'], [4, '11:30', 'A', 'L'], ...dominate('A', 4, '10:30'),                           // AAA answers
  ], at(4, '9:30'));
  const r = E.analyze(s, { closers: { AAA: { closers: ['a1', 'a2'], elite: ['a1'] } } });
  assert.notStrictEqual(r.decision.action, 'BET');
});

test('no-bet zone: close game, no closer edge, teams trading runs = C', () => {
  const s = game([
    [4, '11:50', 'A', 2], [4, '11:40', 'A', 2], [4, '11:30', 'A', 2],
    [4, '11:00', 'H', 2], [4, '10:50', 'H', 2], [4, '10:40', 'H', 2], [4, '10:30', 'H', 2],
    [4, '10:00', 'A', 2], [4, '9:50', 'A', 2], [4, '9:40', 'A', 2],
  ], at(4, '9:30'));
  const r = E.analyze(s, {});
  assert.strictEqual(r.sides.away.grade, 'C');
  assert.ok(r.sides.away.gates.some(g => /No-bet zone/.test(g)));
});

test('under 4:00 -> no new bets, read only', () => {
  const s = game([[4, '5:00', 'A', 2]], at(4, '3:30'));
  assert.strictEqual(E.analyze(s, {}).decision.action, 'NO NEW BET');
});

test('halftime is scouting only', () => {
  const s = game([[2, '1:00', 'A', 2]], at(2, '0.0'));
  assert.strictEqual(E.analyze(s, {}).decision.action, 'SCOUT');
});

// ---- price check -------------------------------------------------------------
test('implied probability from moneyline', () => {
  assert.strictEqual(E.impliedFromAmerican(-900).toFixed(3), '0.900');
  assert.strictEqual(E.impliedFromAmerican(150).toFixed(3), '0.400');
  assert.strictEqual(E.toAmerican(0.9), -900);
  assert.strictEqual(E.toAmerican(0.4), 150);
});

test('Kalshi price above 85% is flagged as priced in but never changes the grade', () => {
  const s = game([[4, '11:50', 'A', 2]], at(4, '9:30'));
  const a = E.analyze(s, {}), b = E.analyze(s, { kalshi: { AAA: 0.9, HHH: 0.1 } });
  assert.strictEqual(a.decision.grade, b.decision.grade);
  if (b.price) assert.strictEqual(b.price.pricedIn, b.price.prob >= 0.85);
});

// ---- lineups -------------------------------------------------------------------
test('substitutions move players on and off the floor', () => {
  const s = game([[1, '11:00', 'A', 'SUB', ['a6', 'a1']], [1, '10:00', 'A', 2, 'a6']], at(1, '9:00'));
  const r = E.analyze(s, { closers: { AAA: { closers: ['a1', 'a6'] } } });
  const byName = Object.fromEntries(r.closers.away.list.map(c => [c.name, c.onFloor]));
  assert.strictEqual(byName.a1, false);
  assert.strictEqual(byName.a6, true);
});

// ---- the favorite: end-of-3rd lead and defense ---------------------------------
test('favorite up 10+ at the end of the 3rd, with tonight\'s defense ranked', () => {
  const plays = [];
  for (let i = 0; i < 6; i++) plays.push([3, `${10 - i}:00`, 'A', 2]);           // AAA (fav) 12-0
  plays.push([3, '2:00', 'H', 2]);                                                // HHH 2
  const s = game(plays, at(3, '0.0'));
  const r = E.analyze(s, { fav: 'AAA', league: { opp_efg: [45, 50, 55], def: [100, 110, 120] },
                           favDefense: { last3: { opp_efg: { v: 49, r: 5 }, def: { v: 103, r: 6 } } } });
  assert.strictEqual(r.favorite.abbr, 'AAA');
  assert.strictEqual(r.favorite.lead, 10);
  assert.strictEqual(r.favorite.up10, true);
  assert.strictEqual(r.favorite.last3.def.r, 6);
  assert.ok(r.favorite.tonight.opp_efg != null);
});

test('no favorite signal when the underdog leads', () => {
  const s = game([[3, '5:00', 'H', 3], [3, '4:00', 'H', 3], [3, '3:00', 'H', 3], [3, '2:00', 'H', 3]], at(3, '0.0'));
  const r = E.analyze(s, { fav: 'AAA' });
  assert.strictEqual(r.favorite.up10, false);
});

// ---- end-of-3rd checklist -------------------------------------------------------
test('end-of-3rd checklist: seven items, a verdict, and the price ceiling', () => {
  const plays = [];
  for (let i = 0; i < 6; i++) plays.push([3, `${10 - i}:00`, 'A', 2, i % 2 ? 'a1' : 'a2']);   // AAA (fav) 12-0 in the 3rd
  plays.push([3, '2:00', 'H', 2]);
  const s = game(plays, at(3, '0.0'));
  const ctx = { fav: 'AAA', favB2B: false, closers: { AAA: { closers: ['a1', 'a2'], elite: [] } },
                league: { opp_efg: [10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 99], def: [10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 99] },
                favDefense: { last3: { opp_efg: { r: 3 }, def: { r: 4 } } } };
  const r = E.analyze(s, Object.assign({ kalshi: { AAA: 0.9 } }, ctx));
  assert.strictEqual(r.checklist.total, 7);
  assert.strictEqual(r.checklist.items.find(i => i.key === 'lead').pass, true);
  assert.strictEqual(r.checklist.items.find(i => i.key === 'b2b').pass, true);
  assert.match(r.checklist.verdict, /^\d of 7, (parlay-ready|skip)$/);
  const thin = E.analyze(s, Object.assign({ kalshi: { AAA: 0.97 } }, ctx));
  assert.strictEqual(thin.checklist.price.tooThin, true);
  assert.strictEqual(thin.checklist.ready, false);
  const b2b = E.analyze(s, Object.assign({}, ctx, { favB2B: true }));
  assert.strictEqual(b2b.checklist.items.find(i => i.key === 'b2b').pass, false);
});
