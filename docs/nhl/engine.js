/* NHL live engine: period readouts, control vs momentum, runs and answers, goalie, BET / LEAN / NO BET.
 *
 * Rules (owner Nov 12-13, 2026; spec "NHL Board"; numbers from the 2024-26 backtest, data/nhl/backtest.json):
 *   - End of the 1st and end of the 2nd: short readouts (score, shots, expected goals, who controls play)
 *   - 3rd period before 10:00 left: lean only
 *   - 3rd period 10:00-5:00 left: the decision window. BET needs a lead of 2+ goals (range favorites up 2+ at
 *     10:00 won 74 of 74; up 1 won 94.5%, and late empty-net pushes make one goal shaky)
 *   - under 5:00 and overtime: read only, no new bets
 *   - control = expected-goals share (and shot-attempt share) over the last 20 game minutes
 *   - run = 2+ unanswered goals; answer = the other team scores next
 * Expected goals: a small model fitted on 2025-26 NHL shots (distance, angle, shot type, power play) applied to
 * ESPN's live shot coordinates (log loss 0.227 vs 0.218 for the full release model).
 *
 * One file, two users: the Live page (window.NhlEngine.analyze(summary, ctx)) and the checkpoint logger
 * (scripts/nhl_engine_cli.js, Node).
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.NhlEngine = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';
  const PLEN = 1200, OTLEN = 300;
  const DECIDE_FROM = 600, DECIDE_TO = 300;   // 3rd period, seconds left
  const MIN_LEAD = 2;
  const CONTROL_WINDOW = 1200;                  // last 20 game minutes
  const XG = { c: -0.97679, dist: -0.06226, ldist: 0.19682, ang: -0.0157, tip: -0.92067, wrap: -1.06997, slap: 0.32029, back: -0.51162, pp: 0.22408 };
  const SHOTS = { 'Shot': 'sog', 'Goal': 'goal', 'Missed': 'miss', 'Blocked': 'block' };

  const secs = s => { const m = String(s || '0:00').split(':'); return (+m[0] || 0) * 60 + (+m[1] || 0); };
  const fmt = t => { t = Math.max(0, Math.round(t)); return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`; };
  const pName = p => (p <= 3 ? ['1st', '2nd', '3rd'][p - 1] : (p === 4 ? 'OT' : 'SO'));
  const pct = (a, b) => (a + b ? Math.round(100 * a / (a + b)) : null);
  const toAmerican = p => (p == null ? null : (p >= 0.5 ? -Math.round(100 * p / (1 - p)) : Math.round(100 * (1 - p) / p)));

  function xg(play, ownPP) {
    const c = play.coordinate || {};
    if (c.x == null || c.y == null) return 0.05;
    const ax = Math.min(Math.abs(c.x), 99), dx = 89 - ax;
    const dist = Math.hypot(dx, c.y), ang = Math.atan2(Math.abs(c.y), Math.max(dx, 0.5)) * 180 / Math.PI;
    const t = String(play.text || '').toLowerCase();
    const z = XG.c + XG.dist * dist + XG.ldist * Math.log1p(dist) + XG.ang * ang + XG.tip * /tip|deflect/.test(t)
      + XG.wrap * /wrap/.test(t) + XG.slap * /slap/.test(t) + XG.back * /backhand/.test(t) + XG.pp * (ownPP ? 1 : 0);
    return 1 / (1 + Math.exp(-z));
  }

  function parse(summary) {
    const comp = ((summary.header || {}).competitions || [])[0] || {};
    const teams = {};
    (comp.competitors || []).forEach(c => {
      teams[c.homeAway] = { id: String(c.team.id), abbr: c.team.abbreviation, name: c.team.displayName || c.team.abbreviation,
                            score: +c.score || 0 };
    });
    const side = id => (teams.home && String(id) === teams.home.id ? 'home' : (teams.away && String(id) === teams.away.id ? 'away' : null));
    const plays = [];
    (summary.plays || []).forEach(p => {
      const per = ((p.period || {}).number) || 1;
      const el = secs((p.clock || {}).displayValue);
      const t = (per <= 3 ? (per - 1) * PLEN : 3 * PLEN + (per - 4) * OTLEN) + el;
      const type = (p.type || {}).text || '';
      const s = p.team ? side(p.team.id) : null;
      const st = ((p.strength || {}).abbreviation) || 'even-strength';
      plays.push({ t, per, el, type, side: s, text: p.text || '', kind: SHOTS[type] || null, st,
                   home: +p.homeScore || 0, away: +p.awayScore || 0, en: /empty net/i.test(p.text || ''),
                   xg: SHOTS[type] && SHOTS[type] !== 'block' ? xg(p, st === 'power-play') : 0 });
    });
    plays.sort((a, b) => a.t - b.t);
    const st = (comp.status || {}).type || {};
    const period = (comp.status || {}).period || (plays.length ? plays[plays.length - 1].per : 1);
    const state = st.state || 'pre';
    const plen = period <= 3 ? PLEN : OTLEN;
    // ESPN's status clock counts DOWN; plays count up. Use the status clock when live.
    const remain = state === 'in' ? secs((comp.status || {}).displayClock) : 0;
    const nowT = state === 'post' ? (plays.length ? plays[plays.length - 1].t : 3600)
      : (period <= 3 ? (period - 1) * PLEN : 3600 + (period - 4) * OTLEN) + (plen - remain);
    return { teams, plays, period, remain, state, nowT, detail: st.shortDetail || st.detail || '',
             intermission: /end|intermission/i.test(st.shortDetail || st.detail || '') && state === 'in' };
  }

  function tally(plays, t0, t1) {
    const z = () => ({ sog: 0, att: 0, xg: 0, goals: 0, hd: 0, pen: 0 });
    const r = { home: z(), away: z() };
    plays.forEach(p => {
      if (p.t <= t0 || p.t > t1 || !p.side) return;
      const x = r[p.side];
      if (p.kind) {
        x.att += 1;
        if (p.kind === 'sog' || p.kind === 'goal') x.sog += 1;
        if (p.kind === 'goal') x.goals += 1;
        x.xg += p.xg;
        if (p.xg >= 0.15) x.hd += 1;
      } else if (!/Face Off|Hit|Giveaway|Takeaway|Stoppage|Period|End of|Shootout|Delay|Challenge/i.test(p.type)) {
        x.pen += 1;                  // ESPN files penalties under the infraction name
      }
    });
    return r;
  }

  function goalsSeq(plays) {
    return plays.filter(p => p.kind === 'goal' && p.side && p.per <= 4).map(p => ({ t: p.t, side: p.side, per: p.per, clock: fmt(p.el), en: p.en }));
  }

  // most recent run (2+ unanswered goals) and whether it was answered
  function runs(goals) {
    let last = null, i = 0;
    while (i < goals.length) {
      let j = i;
      while (j + 1 < goals.length && goals[j + 1].side === goals[i].side) j++;
      if (j - i + 1 >= 2) last = { side: goals[i].side, n: j - i + 1, start: goals[i], end: goals[j], answeredBy: goals[j + 1] ? goals[j + 1].side : null };
      i = j + 1;
    }
    return last;
  }

  function phase(S) {
    if (S.state === 'pre') return { key: 'pre', label: 'Not started', canBet: false };
    if (S.state === 'post') return { key: 'final', label: 'Final', canBet: false };
    const left = S.remain;
    if (S.period === 1) return S.intermission || left === 0 ? { key: 'end1', label: 'End of the 1st · readout', canBet: false }
                                                          : { key: 'p1', label: '1st period · scouting', canBet: false };
    if (S.period === 2) return S.intermission || left === 0 ? { key: 'end2', label: 'End of the 2nd · readout, lean only', canBet: false }
                                                          : { key: 'p2', label: '2nd period · scouting', canBet: false };
    if (S.period === 3) {
      if (left > DECIDE_FROM) return { key: 'p3early', label: '3rd period · lean only (decision window opens at 10:00)', canBet: false };
      if (left >= DECIDE_TO) return { key: 'decide', label: '3rd 10:00–5:00 · DECISION window', canBet: true };
      return { key: 'late', label: 'Under 5:00 · no new bets, read only', canBet: false };
    }
    return { key: 'ot', label: 'Overtime · read only', canBet: false };
  }

  function analyze(summary, ctx = {}, opts = {}) {
    const S = parse(summary);
    if (opts.uptoT != null) {                 // replay: cut the game at a moment (tests, backtest)
      S.plays = S.plays.filter(p => p.t <= opts.uptoT);
      S.nowT = opts.uptoT;
      S.period = opts.uptoT >= 3600 ? 4 : Math.floor(opts.uptoT / PLEN) + 1;
      S.remain = opts.uptoT >= 3600 ? OTLEN : PLEN - (opts.uptoT % PLEN);
      if (opts.uptoT % PLEN === 0 && opts.uptoT > 0 && opts.uptoT <= 3600) { S.period = opts.uptoT / PLEN; S.remain = 0; S.intermission = true; }
      S.state = 'in';
      const last = S.plays[S.plays.length - 1];
      if (S.teams.home) S.teams.home.score = last ? last.home : 0;
      if (S.teams.away) S.teams.away.score = last ? last.away : 0;
    }
    const T = S.teams, H = T.home || { abbr: 'HOME', score: 0 }, A = T.away || { abbr: 'AWAY', score: 0 };
    const ph = phase(S);
    const clockLabel = S.state === 'post' ? 'Final' : (S.state === 'pre' ? 'Pregame'
      : (ph.key === 'end1' ? 'End of 1st' : (ph.key === 'end2' ? 'End of 2nd' : `${fmt(S.remain)} ${pName(S.period)}`)));
    const favSide = ctx.fav ? (ctx.fav === H.abbr ? 'home' : (ctx.fav === A.abbr ? 'away' : null)) : null;
    const lead = H.score - A.score;
    const leader = lead > 0 ? 'home' : (lead < 0 ? 'away' : null);
    const focus = leader || favSide || 'home';
    const other = focus === 'home' ? 'away' : 'home';
    const focusLead = focus === 'home' ? lead : -lead;

    const game = tally(S.plays, -1, S.nowT);
    const cw = tally(S.plays, Math.max(-1, S.nowT - CONTROL_WINDOW), S.nowT);
    const per = [1, 2, 3].map(p => tally(S.plays, (p - 1) * PLEN - (p === 1 ? 1 : 0), p * PLEN));
    const last5 = tally(S.plays, S.nowT - 300, S.nowT);
    const xgShare = pct(cw[focus].xg, cw[other].xg), attShare = pct(cw[focus].att, cw[other].att);
    const control = xgShare != null && xgShare >= 55 && attShare != null && attShare >= 50 ? focus
      : (xgShare != null && xgShare <= 45 && attShare != null && attShare <= 50 ? other : null);
    const goals = goalsSeq(S.plays);
    const run = runs(goals);
    const lastGoal = goals[goals.length - 1] || null;
    const oppRunOpen = run && run.side === other && !run.answeredBy;
    const answered = run ? (run.answeredBy ? (run.answeredBy === 'home' ? H.abbr : A.abbr) : 'NONE') : 'No run yet';
    const pressure = last5[other].xg >= 0.6 || last5[other].sog >= 5;
    const abbr = s => (s === 'home' ? H.abbr : A.abbr);

    // ---- grade ------------------------------------------------------------------
    let grade = 'C', action = 'NO BET', gates = [], reasons = [];
    if (S.state === 'post') { grade = '–'; action = 'FINAL'; }
    else if (S.state === 'pre') { grade = '–'; action = 'PREGAME'; }
    else {
      if (focusLead >= MIN_LEAD) {
        if (control === focus && !oppRunOpen && !pressure) { grade = 'A+'; reasons.push(`Up ${focusLead}, ${abbr(focus)} controls play (${xgShare}% of xG, last 20 min)`); }
        else if (control !== other && !oppRunOpen) { grade = 'A'; reasons.push(`Up ${focusLead}; play is ${control ? 'theirs' : 'even'} (${xgShare ?? '–'}% of xG, last 20 min)`); }
        else { grade = 'B'; reasons.push(control === other ? `${abbr(other)} is controlling play despite trailing` : `${abbr(other)} has an unanswered run`); }
      } else if (focusLead === 1) {
        grade = control === focus ? 'B' : 'C';
        gates.push(`One-goal lead: BET needs ${MIN_LEAD}+ (late empty-net pushes)`);
      } else {
        grade = 'C';
        reasons.push(lead === 0 ? 'Tied' : 'No lead');
      }
      if (oppRunOpen) gates.push(`${abbr(other)} scored ${run.n} straight, not answered: B maximum`);
      if (pressure && grade.startsWith('A')) { grade = 'B'; gates.push(`${abbr(other)} pressing: ${last5[other].sog} shots, ${last5[other].xg.toFixed(2)} xG in the last 5 min`); }
      if (ctx.fav && favSide && focus !== favSide && grade.startsWith('A')) gates.push(`${abbr(focus)} is the underdog: read only, the system bets favorites`);
      const isFavFocus = !ctx.fav || focus === favSide;
      if (ph.canBet && grade.startsWith('A') && isFavFocus) action = 'BET';
      else if ((ph.key === 'p3early' || ph.key === 'end2' || ph.key === 'decide') && focusLead >= 1 && grade !== 'C' && isFavFocus) action = 'LEAN';
      else action = 'NO BET';
    }
    const pick = action === 'BET' || action === 'LEAN' ? abbr(focus) : null;
    const headline = S.state === 'post' ? `Final: ${H.score > A.score ? H.abbr : A.abbr} won`
      : (S.state === 'pre' ? 'Not started' : (action === 'BET' ? `BET ${pick} ML` : (action === 'LEAN' ? `LEAN ${pick}` : 'NO BET')));

    // ---- price check -------------------------------------------------------------
    let price = null;
    const k = ctx.kalshi || null;
    if (k && pick && k[pick] != null) price = { abbr: pick, prob: k[pick], american: toAmerican(k[pick]), pricedIn: k[pick] >= 0.85 };
    else if (k && k[abbr(focus)] != null) price = { abbr: abbr(focus), prob: k[abbr(focus)], american: toAmerican(k[abbr(focus)]), pricedIn: k[abbr(focus)] >= 0.85 };

    // ---- readouts ------------------------------------------------------------------
    const line = (lab, t) => `${lab}: ${A.abbr} ${t.away.goals}-${t.home.goals} ${H.abbr} · shots ${t.away.sog}-${t.home.sog} · xG ${t.away.xg.toFixed(1)}-${t.home.xg.toFixed(1)}`;
    const readouts = [];
    if (S.nowT >= PLEN) readouts.push({ key: 'end1', text: line('1st period', per[0]), trend: trendText(per[0], H, A) });
    if (S.nowT >= 2 * PLEN) readouts.push({ key: 'end2', text: line('2nd period', per[1]), trend: trendText(tally(S.plays, -1, 2 * PLEN), H, A, true) });

    // ---- checklist at 10:00 of the 3rd (the hockey version of the NBA's end-of-3rd checklist) ----
    let checklist = null;
    if (favSide && S.nowT >= 3000) {
      const p10 = S.plays.filter(p => p.t <= 3000);
      const lastAt = p10[p10.length - 1] || { home: 0, away: 0 };
      const leadAt = favSide === 'home' ? lastAt.home - lastAt.away : lastAt.away - lastAt.home;
      const fo = favSide === 'home' ? 'away' : 'home';
      const w = tally(S.plays, 1800, 3000);
      const xs = pct(w[favSide].xg, w[fo].xg), as = pct(w[favSide].att, w[fo].att);
      const gAt = goalsSeq(p10), rAt = runs(gAt);
      const items = [
        { key: 'lead', label: `Lead ${MIN_LEAD}+ at 10:00 of the 3rd`, ok: leadAt >= MIN_LEAD, detail: `lead ${leadAt >= 0 ? '+' : ''}${leadAt}` },
        { key: 'xg', label: 'Expected-goals share 50%+ (last 20 min)', ok: xs != null && xs >= 50, detail: `${xs ?? '–'}%` },
        { key: 'att', label: 'Shot-attempt share 45%+ (last 20 min)', ok: as != null && as >= 45, detail: `${as ?? '–'}%` },
        { key: 'run', label: `${abbr(fo)} has no unanswered run`, ok: !(rAt && rAt.side === fo && !rAt.answeredBy), detail: rAt ? `${abbr(rAt.side)} ${rAt.n} straight${rAt.answeredBy ? ', answered' : ''}` : 'no run' },
        { key: 'goalie', label: `${abbr(favSide)} regular goalie in net`, ok: ctx.favGoalieOk == null ? null : !!ctx.favGoalieOk, detail: ctx.favGoalieName || 'unknown' },
        { key: 'b2b', label: `${abbr(favSide)} didn't play yesterday`, ok: ctx.favB2B == null ? null : !ctx.favB2B, detail: ctx.favB2B ? 'second night of a back-to-back' : 'rested' },
      ];
      const passed = items.filter(i => i.ok === true).length;
      const kp = k && k[abbr(favSide)];
      const verdict = passed === items.length && (kp == null || kp <= 0.94) ? `${passed} of ${items.length}, bet-ready`
        : `${passed} of ${items.length}${kp != null && kp > 0.94 ? `, priced ${Math.round(kp * 100)}¢ (too thin)` : ''}`;
      checklist = { items, passed, total: items.length, verdict, reasons: items.filter(i => i.ok === false).map(i => i.label) };
    }

    const nextCheck = S.state !== 'in' ? '' : (S.period < 3 ? `End of the ${pName(S.period)}` : (S.period === 3 && S.remain > DECIDE_FROM ? '10:00 of the 3rd'
      : (S.period === 3 && S.remain >= DECIDE_TO ? 'Every minute until 5:00' : 'Final')));
    const date = ctx.date || '';
    const logLine = `${date} | ${A.abbr}@${H.abbr} | ${clockLabel} | ${A.score}-${H.score} | ${grade} | ${pick || action} | ${price ? (price.american > 0 ? '+' : '') + price.american : '–'} | ${reasons.concat(gates).join('; ')}`;
    return {
      state: S.state, period: S.period, remain: S.remain, clockLabel, phase: ph,
      teams: { home: H, away: A }, lead, focus: abbr(focus), favSide,
      decision: { grade, action, pick, headline, focusSide: focus, gates, reasons },
      control: { team: control ? abbr(control) : null, window: 'last 20 min', xgShare, attShare,
                 rows: [['Shots on goal', 'sog'], ['Shot attempts', 'att'], ['Expected goals', 'xg'], ['High-danger chances', 'hd'], ['Penalties', 'pen']]
                   .map(([label, key]) => ({ label, away: key === 'xg' ? cw.away[key].toFixed(2) : cw.away[key], home: key === 'xg' ? cw.home[key].toFixed(2) : cw.home[key],
                                             winner: key === 'pen' ? (cw.away[key] < cw.home[key] ? 'away' : (cw.home[key] < cw.away[key] ? 'home' : null))
                                               : (cw.away[key] > cw.home[key] ? 'away' : (cw.home[key] > cw.away[key] ? 'home' : null)) })) },
      game, periods: per, goals: goals.map(g => ({ ...g, team: abbr(g.side) })), run, whoAnswered: answered,
      momentum: { last5, pressure, side: abbr(other) }, price, readouts, checklist, next: nextCheck, logLine,
    };
  }

  function trendText(t, H, A, cumulative) {
    const xs = pct(t.home.xg, t.away.xg), as = pct(t.home.att, t.away.att);
    const lead = xs >= 55 ? H.abbr : (xs <= 45 ? A.abbr : null);
    const sc = t.home.goals - t.away.goals;
    const bits = [];
    bits.push(lead ? `${lead} controlling play (${lead === H.abbr ? xs : 100 - xs}% of expected goals, ${lead === H.abbr ? as : 100 - as}% of shot attempts)` : `Even play (${xs ?? '–'}% / ${100 - (xs ?? 50)}% of expected goals)`);
    if (lead && sc !== 0 && ((sc > 0 && lead === A.abbr) || (sc < 0 && lead === H.abbr))) bits.push(`the scoreboard runs against play: ${sc > 0 ? H.abbr : A.abbr} leads on fewer chances`);
    return (cumulative ? 'Through two: ' : '') + bits.join('; ') + '.';
  }

  return { analyze, toAmerican, xg, DECIDE_FROM, DECIDE_TO, MIN_LEAD };
});
