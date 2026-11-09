/* NFL live-game engine: runs and answers, control vs momentum, QB (closer), A+/A/B/C grade,
 * plus the early-path checkpoint read (shadow).
 *
 * One file, used by the NFL Live page (browser: window.NflEngine) and by the checkpoint
 * logger / tests (Node: require('./engine.js')), so they always agree.
 *
 *   analyze(summary, ctx, opts)
 *     summary  ESPN NFL game summary (drives, scoringPlays, header)
 *     ctx      { fav, dog: ESPN abbrs; qbs: {ABBR: {regular: "J.Allen", rank: 7}};
 *                fav_defense_top10: bool; ep: docs/nfl/ep.json; kalshi: {ABBR: 0.62} | null }
 *     opts     { upto: {period, remain} }  replay the game up to a moment (tests, logger)
 *
 * Rules: spec "NFL Stat Edge v2" section "Live grading (NFL, decided)". ESPN has no EPA, so
 * EPA is estimated from nflverse expected points by down, distance and field position
 * (docs/nfl/ep.json); success = estimated EPA > 0. No randomness, no AI.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.NflEngine = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const GRADES = ['C', 'B', 'A', 'A+'];
  const gIdx = g => GRADES.indexOf(g);
  const QLEN = 900;
  const other = s => (s === 'away' ? 'home' : 'away');
  const clockSec = s => { s = String(s ?? '0'); if (s.includes(':')) { const [m, x] = s.split(':'); return (+m) * 60 + (+x); } return +s || 0; };
  const fmt = sec => `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, '0')}`;
  const elapsed = (period, remain) => (period - 1) * QLEN + (QLEN - remain);
  const qName = p => (p <= 4 ? ['1st', '2nd', '3rd', '4th'][p - 1] : 'OT');

  // ------------------------------------------------------------- expected points --
  function epOf(table, down, dist, ytez) {
    if (!table || !(down >= 1 && down <= 4) || !(ytez >= 1 && ytez <= 99)) return null;
    const b = (v, arr) => { for (let i = 0; i < arr.length; i++) if (v <= arr[i]) return i; return arr.length - 1; };
    return table.ep[down - 1][b(Math.max(1, dist || 10), table.dist_bounds)][b(ytez, table.yard_bounds)];
  }

  // ------------------------------------------------------------------- plays --
  const SCRIM = /rush|pass|sack|interception|fumble|scramble/i;
  const NOT_SCRIM = /penalty|timeout|kickoff|punt|field goal|extra point|two-point|two point|end period|end of|two-minute|coin toss|kneel|spike/i;
  const DEF_SCORE = /interception return touchdown|fumble return touchdown|kickoff return touchdown|punt return touchdown|blocked|defensive|safety/i;

  function teamsOf(summary) {
    const comp = summary.header.competitions[0], out = {};
    comp.competitors.forEach(c => { out[c.homeAway] = { id: String(c.team.id), abbr: c.team.abbreviation, score: c.score != null ? +c.score : 0 }; });
    return out;
  }

  function normPlays(summary, teams, ep) {
    const sideOf = id => (String(id) === teams.home.id ? 'home' : (String(id) === teams.away.id ? 'away' : null));
    const drives = [].concat((summary.drives || {}).previous || [], (summary.drives || {}).current ? [summary.drives.current] : []);
    const plays = [], seen = new Set();
    let prevA = 0, prevH = 0, driveNo = 0;
    drives.forEach(d => {
      driveNo++;
      let firstScrim = true;
      (d.plays || []).forEach(p => {
        if (seen.has(p.id)) return; seen.add(p.id);
        const period = (p.period || {}).number || 1, remain = clockSec((p.clock || {}).displayValue);
        const st = p.start || {}, en = p.end || {};
        const off = sideOf((st.team || {}).id) || sideOf((d.team || {}).id);
        const type = (p.type || {}).text || '';
        // a snap with a penalty still counts when a pass or run happened (nflverse's pass/rush flags do the
        // same); pre-snap fouls (false start, delay of game) have no pass or run in the text
        const penSnap = /penalty/i.test(type) &&
          / pass |sacked|scrambles|(left|right) (end|tackle|guard)|up the middle/.test(p.text || '');
        const scrim = (penSnap || (SCRIM.test(type) && !NOT_SCRIM.test(type))) && st.down >= 1 && st.down <= 4 && !!off;
        const a = p.awayScore ?? prevA, h = p.homeScore ?? prevH;
        const dA = a - prevA, dH = h - prevH;
        prevA = a; prevH = h;
        const q = { id: p.id, i: plays.length, drive: driveNo, period, remain, t: elapsed(period, remain), type, text: p.text || '',
                    off, scrim, yards: +p.statYardage || 0, turnover: !!p.isTurnover, scoring: !!p.scoringPlay,
                    away: a, home: h, down: st.down, dist: st.distance, ytez: st.yardsToEndzone, firstOfDrive: false };
        if (scrim) {
          if (firstScrim) { q.firstOfDrive = true; firstScrim = false; }
          const before = epOf(ep, st.down, st.distance, st.yardsToEndzone);
          let after = null;
          const offPts = off === 'away' ? dA : dH, defPts = off === 'away' ? dH : dA;
          if (offPts >= 6) after = 7;
          else if (defPts >= 6) after = -7;
          else if (defPts === 2) after = -2;
          else if (en.team && sideOf(en.team.id) === off) after = epOf(ep, en.down, en.distance, en.yardsToEndzone);
          else if (en.team && sideOf(en.team.id)) { const e2 = epOf(ep, en.down, en.distance, en.yardsToEndzone); after = e2 == null ? null : -e2; }
          if (before != null && after == null && q.turnover) after = 0;
          q.epa = before != null && after != null ? Math.max(-10, Math.min(10, after - before)) : null;
          q.success = q.epa != null ? q.epa > 0 : null;
          q.dropback = /pass|sack|scramble|interception/i.test(type) || /\bpass\b|sacked|scrambles/.test(q.text);
          q.explosive = q.yards >= 20 && !/sack/i.test(type);
          q.sack = /sack/i.test(type) || / sacked /.test(q.text);
          q.thirdConv = st.down === 3 ? (offPts >= 6 || (en.team && sideOf(en.team.id) === off && en.down === 1)) : null;
          const m = q.text.match(/([A-Z][A-Za-z]{0,2}\.[A-Za-z'\-]+(?: (?:Jr\.|II|III))?) (?:pass|sacked|scrambles)/);
          q.passer = q.dropback && m ? m[1].trim() : null;
          q.int = /interception/i.test(type) || / INTERCEPTED /.test(q.text);
          q.fumLost = /fumble/i.test(type) && q.turnover && !q.int;
          if (q.firstOfDrive) q.startOwn = 100 - st.yardsToEndzone;
        }
        plays.push(q);
      });
    });
    return plays;
  }

  // ------------------------------------------------------------- scoring/runs --
  function scoringEvents(summary, teams, uptoT) {
    let pa = 0, ph = 0;
    const out = [];
    (summary.scoringPlays || []).forEach(s => {
      const period = (s.period || {}).number || 1, remain = clockSec((s.clock || {}).displayValue);
      const t = elapsed(period, remain);
      if (uptoT != null && t > uptoT) return;
      const a = +s.awayScore, h = +s.homeScore, dA = a - pa, dH = h - ph;
      pa = a; ph = h;
      const side = dA > dH ? 'away' : (dH > dA ? 'home' : (String((s.team || {}).id) === teams.home.id ? 'home' : 'away'));
      const pts = Math.max(dA, dH);
      out.push({ side, pts, td: pts >= 6, t, period, remain, away: a, home: h, type: (s.type || {}).text || '', text: s.text || '',
                 defensive: DEF_SCORE.test((s.type || {}).text || '') });
    });
    return out;
  }
  const marginOf = (e, side) => (side === 'away' ? e.away - e.home : e.home - e.away);

  // Run = two scores in a row for 10+ unanswered points, or taking the lead and scoring again.
  function findRuns(ev) {
    const runs = [];
    for (let s = 0; s < ev.length; s++) {
      const side = ev[s].side;
      if (s > 0 && ev[s - 1].side === side) continue;          // start of a streak
      let e = s, pts = 0;
      while (e < ev.length && ev[e].side === side) { pts += ev[e].pts; e++; }
      e--;
      const n = e - s + 1;
      const before = s ? marginOf(ev[s - 1], side) : 0;
      let leadChangeAt = null;
      for (let k = s; k <= e; k++) { const prev = k === s ? before : marginOf(ev[k - 1], side); if (prev <= 0 && marginOf(ev[k], side) > 0) { leadChangeAt = k; break; } }
      if (n >= 2 && pts >= 10) runs.push({ side, s, e, pts, kind: `${pts}-0`, label: `${pts}-0` });
      else if (leadChangeAt != null && leadChangeAt < e) runs.push({ side, s, e, pts, kind: 'took the lead and scored again', label: `${pts}-0` });
    }
    return runs.map(r => Object.assign(r, { startT: ev[r.s].t, endT: ev[r.e].t,
      end: { period: ev[r.e].period, clock: fmt(ev[r.e].remain) } }));
  }

  // Answer = any score by the other team after the run. Strong = a touchdown, or 10+ unanswered.
  function findAnswer(ev, run) {
    const ans = other(run.side);
    let k = run.e + 1;
    while (k < ev.length && ev[k].side !== ans) k++;
    if (k >= ev.length) return null;
    let pts = 0, j = k, td = false;
    while (j < ev.length && ev[j].side === ans) { pts += ev[j].pts; td = td || ev[j].td; j++; }
    const strong = td || pts >= 10;
    return { side: ans, k, t: ev[k].t, pts, strong, how: td ? `touchdown${pts > 7 ? ` (${pts}-0)` : ''}` : `${pts}-0${pts >= 10 ? '' : ' (field goal)'}`,
             at: { period: ev[k].period, clock: fmt(ev[k].remain) } };
  }

  // ----------------------------------------------------------------- control --
  function tally(plays, t0, t1) {
    const z = () => ({ n: 0, epa: 0, succ: 0, third: 0, thirdConv: 0, sacks: 0, starts: [], tov: 0, expl: 0 });
    const T = { away: z(), home: z() };
    plays.forEach(p => {
      if (p.t < t0 || p.t > t1 || !p.scrim || !p.off) return;
      const o = T[p.off], d = T[other(p.off)];
      if (p.epa != null) { o.n++; o.epa += p.epa; o.succ += p.success ? 1 : 0; }
      if (p.down === 3) { o.third++; o.thirdConv += p.thirdConv ? 1 : 0; }
      if (p.sack) d.sacks++;
      if (p.firstOfDrive && p.startOwn != null) o.starts.push(p.startOwn);
      if (p.turnover) d.tov++;                  // takeaways
      if (p.explosive) o.expl++;
    });
    return T;
  }
  const CATS = [
    { key: 'epa', label: 'EPA per play (est.)', val: x => (x.n >= 5 ? x.epa / x.n : null), fmt: v => (v >= 0 ? '+' : '') + v.toFixed(2) },
    { key: 'sr', label: 'Success rate', val: x => (x.n >= 5 ? x.succ / x.n : null), fmt: v => Math.round(v * 100) + '%' },
    { key: 'third', label: '3rd-down rate', val: x => (x.third >= 2 ? x.thirdConv / x.third : null), fmt: v => Math.round(v * 100) + '%' },
    { key: 'sacks', label: 'Sacks (pressure)', val: x => x.sacks, fmt: v => String(v) },
    { key: 'field', label: 'Avg start field pos.', val: x => (x.starts.length ? x.starts.reduce((a, b) => a + b, 0) / x.starts.length : null), fmt: v => 'own ' + Math.round(v) },
  ];
  function control(plays, nowT, t0) {
    const T = tally(plays, t0, nowT), rows = [], wins = { away: 0, home: 0 };
    CATS.forEach(c => {
      const a = c.val(T.away), h = c.val(T.home);
      let w = null;
      if (a != null && h != null && a !== h) w = a > h ? 'away' : 'home';
      if (w) wins[w]++;
      rows.push({ key: c.key, label: c.label, away: a == null ? '–' : c.fmt(a), home: h == null ? '–' : c.fmt(h), winner: w });
    });
    const team = wins.away >= 3 ? 'away' : (wins.home >= 3 ? 'home' : null);
    return { rows, wins, team, T };
  }

  // ---------------------------------------------------------------------- QB --
  function qbRead(side, abbr, plays, ctx, nowT) {
    const info = ((ctx.qbs || {})[abbr]) || {};
    const mine = plays.filter(p => p.scrim && p.off === side && p.dropback && p.t <= nowT);
    const named = mine.filter(p => p.passer);
    const cur = named.length ? named[named.length - 1].passer : null;
    const byQb = named.filter(p => p.passer === cur);
    const tos = byQb.filter(p => p.int || p.fumLost).length;
    const ep = byQb.filter(p => p.epa != null);
    const epaDb = ep.length ? ep.reduce((s, p) => s + p.epa, 0) / ep.length : null;
    const regular = info.regular || null;
    const backup = !!(regular && cur && cur !== regular);
    // unclear = another passer took 2+ of the last 8 dropbacks (injury check, benching), not a one-off trick play
    const recentQ = {}; named.slice(-8).forEach(p => { recentQ[p.passer] = (recentQ[p.passer] || 0) + 1; });
    const switched = Object.entries(recentQ).some(([n, c]) => n !== cur && c >= 2);
    let score, why;
    if (!cur) { score = 1; why = 'no dropbacks yet'; }
    else if (backup) { score = 0; why = `backup in (${cur}; regular ${regular})`; }
    else if (tos >= 2) { score = 0; why = `${tos} turnovers`; }
    else if (info.rank != null && info.rank <= 10 && epaDb != null && epaDb > 0) { score = 2; why = `top-10 QB (rank ${info.rank}) playing well`; }
    else { score = 1; why = info.rank != null && info.rank <= 10 ? 'top-10 QB, not playing well today' : 'starter in, not top 10'; }
    return { side, abbr, name: cur || regular || '?', regular, score, why, dropbacks: byQb.length, epaDb, turnovers: tos,
             unclear: switched || (!cur && nowT > QLEN) };
  }

  // ------------------------------------------------------------------ timing --
  function phaseOf(state, period, remain, status) {
    if (state === 'pre') return { key: 'pre', label: 'Pregame', canBet: false };
    if (state === 'post') return { key: 'final', label: 'Final', canBet: false };
    if (period <= 2) return { key: 'scout', label: (period === 2 && (remain === 0 || /HALF/.test(status || ''))) ? 'Halftime · scouting only' : 'First half · scouting only', canBet: false };
    if (period === 3) return remain === 0 ? { key: 'end3', label: 'End of 3rd · first signal, lean only', canBet: false }
                                          : { key: 'q3', label: '3rd quarter · lean only', canBet: false };
    if (period === 4) {
      if (remain > 600) return { key: 'primary', label: '4th 15:00–10:00 · PRIMARY decision window', canBet: true };
      if (remain > 360) return { key: 'confirm', label: '4th 10:00–6:00 · confirm, upgrade or downgrade', canBet: true };
      if (remain >= 300) return { key: 'late', label: '4th 6:00–5:00 · last bet window', canBet: true };
      return { key: 'under5', label: 'Under 5:00 · no new bets, read only', canBet: false };
    }
    return { key: 'ot', label: 'Overtime · no new bets, read only', canBet: false };
  }
  function nextCheck(ph) {
    return { scout: 'Next update: halftime, then the end of the 3rd', end3: 'Next update: start of the 4th (primary window 15:00–10:00)',
             q3: 'Next update: end of the 3rd', primary: 'Next update: 4th 10:00 (confirm window)', confirm: 'Next update: 4th 6:00',
             late: 'Last window closes at 5:00', under5: 'Read only from here', ot: 'Read only', final: '', pre: 'Opens at kickoff' }[ph] || '';
  }

  // ------------------------------------------------------------------- grade --
  function gradeSide(side, S) {
    const o = other(side), abbr = S.teams[side].abbr, oabbr = S.teams[o].abbr;
    const reasons = [], gates = [];
    const oRun = [...S.runs].reverse().find(r => r.side === o) || null;
    const ans = oRun ? findAnswer(S.ev, oRun) : null;
    const answered = !!(ans && ans.side === side);
    const strong = answered && ans.strong;
    const ctrl = S.control.team === side, oppCtrl = S.control.team === o;
    const qs = S.qb[side], qo = S.qb[o], qbEdge = qs.score > qo.score;
    const lead = S.margin[side];
    const leadStable = lead > 0 && lead >= S.marginAgo[side];
    const m = S.momentum[side];
    const momentumOnly = !ctrl && (m.takeaways >= 2 || m.explosive >= 3 || m.defTD >= 1);

    if (oRun) reasons.push(answered ? `Answered ${oabbr}'s ${oRun.label} run with a ${ans.how}${strong ? '' : ' (not a strong answer)'}` : `${oabbr}'s ${oRun.label} run is unanswered`);
    else reasons.push(`${oabbr} has not had a run to answer`);
    reasons.push(ctrl ? `Control (${S.control.wins[side]} of 5)` : (oppCtrl ? `${oabbr} has control` : 'No clear control'));
    reasons.push(qbEdge ? `QB edge (+${qs.score} vs +${qo.score})` : `No QB edge (+${qs.score} vs +${qo.score})`);

    let g;
    if (answered) {
      if (strong && ctrl && qbEdge && leadStable) g = 'A+';
      else if (strong && (ctrl || qbEdge)) g = 'A';
      else { g = 'B'; if (!strong) gates.push('An A needs a strong answer: a touchdown or 10+ unanswered'); }
    } else {
      g = (ctrl || qbEdge) ? 'B' : 'C';
      if (momentumOnly) g = 'C';
      gates.push('No answered run = B maximum');
    }
    if ((S.phase.key === 'confirm' || S.phase.key === 'late') && g === 'A' && lead > 8 && S.ev.slice(ans.k + 1).every(e => e.side === side)) {
      g = 'A+'; reasons.push(`Lead extended to ${lead} (two scores), ${oabbr} has not responded`);
    }
    const tov4 = S.q4tov[side];
    if (tov4 >= 2 && gIdx(g) > 0) { g = GRADES[gIdx(g) - 1]; gates.push(`Turnover spike (${tov4} in the 4th): down one grade`); }
    const recent = S.runs.filter(r => r.endT > S.nowT - 900);
    const trading = recent.some(r => r.side === 'away') && recent.some(r => r.side === 'home');
    if (Math.abs(lead) <= 8 && !qbEdge && trading && g !== 'C') { g = 'C'; gates.push('No-bet zone: one-score game, no QB edge, teams trading runs'); }
    if ((S.phase.key === 'primary' || S.phase.key === 'confirm') && (qs.unclear || qo.unclear) && gIdx(g) > gIdx('B')) {
      g = 'B'; gates.push('QB status unclear in the decision window = B maximum');
    }
    const lock = answered && lead > 8 && ctrl && lead > (S.marginAt(ans.t)[side]);
    return { side, abbr, grade: g, answered, strong, answer: ans, oppRun: oRun, control: ctrl, qbEdge, lead, leadStable, momentumOnly, reasons, gates, lock };
  }

  // ---------------------------------------------------------- early path read --
  function earlyPath(S, ctx) {
    const favSide = S.teams.home.abbr === ctx.fav ? 'home' : (S.teams.away.abbr === ctx.fav ? 'away' : null);
    if (!favSide) return null;
    const full = control(S.plays, S.nowT, 0);                 // backtest rule: the whole game so far
    const lead = S.margin[favSide];
    const qf = S.qb[favSide].score, qd = S.qb[other(favSide)].score;
    const items = [
      { ok: lead >= 10, text: `Favorite ${ctx.fav} up 10+ (now ${lead > 0 ? '+' : ''}${lead})` },
      { ok: !!ctx.fav_defense_top10, text: 'Both defensive metrics top 10 (season and last 3)' },
      { ok: full.wins[favSide] >= 3, text: `Control 3 of 5 so far (${full.wins[favSide]} of 5)` },
      { ok: qf >= qd, text: `QB edge holds (+${qf} vs +${qd})` },
    ];
    const window = S.phase.key === 'scout' && /Halftime/.test(S.phase.label) || ['end3', 'q3'].includes(S.phase.key);
    return { fav: ctx.fav, items, full: items.every(i => i.ok), anyD: items[0].ok && items[2].ok && items[3].ok, inWindow: window };
  }

  // ------------------------------------------------------------------ analyze --
  function analyze(summary, ctx, opts) {
    ctx = ctx || {}; opts = opts || {};
    const teams = teamsOf(summary);
    const comp = summary.header.competitions[0], stType = ((comp.status || {}).type) || {};
    let state = stType.state || 'pre', period = (comp.status || {}).period || 1, remain = clockSec((comp.status || {}).displayClock);
    let plays = normPlays(summary, teams, ctx.ep);
    if (opts.upto) { const ut = elapsed(opts.upto.period, opts.upto.remain); plays = plays.filter(p => p.t <= ut); state = 'in'; period = opts.upto.period; remain = opts.upto.remain; }
    const nowT = state === 'post' ? Math.max(4 * QLEN, ...plays.map(p => p.t)) : elapsed(period, remain);
    const ev = scoringEvents(summary, teams, opts.upto ? nowT : null);
    const last = ev[ev.length - 1] || { away: 0, home: 0 };
    if (opts.upto || state !== 'post') { teams.away.score = last.away; teams.home.score = last.home; }
    const out = { teams, state, period, remain, clockLabel: state === 'post' ? 'Final' : `${qName(period)} ${fmt(remain)}`,
                  score: `${teams.away.abbr} ${teams.away.score} – ${teams.home.abbr} ${teams.home.score}` };
    out.phase = phaseOf(state, period, remain, stType.name);
    if (state === 'pre') { out.decision = { grade: '–', action: 'PREGAME', pick: null, headline: 'Not started' }; return out; }

    const margin = { away: teams.away.score - teams.home.score, home: teams.home.score - teams.away.score };
    const marginAt = t => { const e = [...ev].reverse().find(x => x.t <= t) || { away: 0, home: 0 }; return { away: e.away - e.home, home: e.home - e.away }; };
    const ago = marginAt(nowT - 300);
    const runs = findRuns(ev);
    const t0 = period >= 3 ? 2 * QLEN : 0;                         // second half weighted: control from the 3rd on
    const ctrl = control(plays, nowT, t0);
    const T = ctrl.T;
    const defTD = s => ev.filter(e => e.side === s && e.defensive).length;
    const momentum = { away: { takeaways: T.away.tov, explosive: T.away.expl, defTD: defTD('away') },
                       home: { takeaways: T.home.tov, explosive: T.home.expl, defTD: defTD('home') } };
    const qT = tally(plays, 3 * QLEN, nowT);
    const qb = { away: qbRead('away', teams.away.abbr, plays, ctx, nowT), home: qbRead('home', teams.home.abbr, plays, ctx, nowT) };
    const S = { teams, ev, runs, plays, nowT, control: ctrl, margin, marginAgo: ago, marginAt, momentum, qb, phase: out.phase,
                q4tov: { away: qT.home.tov, home: qT.away.tov } };
    const sides = { away: gradeSide('away', S), home: gradeSide('home', S) };

    // fake comeback: trailing team closed 7+ in the window mostly on takeaways / big plays, still losing success rate and control
    let fake = null;
    ['away', 'home'].forEach(s => {
      const closed = marginAt(t0)[s] < 0 ? margin[s] - marginAt(t0)[s] : 0;
      const winSR = ctrl.rows.find(r => r.key === 'sr').winner;
      if (closed >= 7 && margin[s] <= 0 && ctrl.team !== s && winSR !== s && (momentum[s].takeaways + momentum[s].explosive + momentum[s].defTD >= 2)) {
        fake = { side: s, text: `Fake comeback risk: ${teams[s].abbr} closed ${closed} on takeaways/big plays but still loses success rate and control` };
      }
    });

    const lastRun = runs[runs.length - 1] || null;
    let whoAnswered = null, answerNote = 'No run yet';
    if (lastRun) {
      const a = findAnswer(ev, lastRun);
      whoAnswered = a ? teams[a.side].abbr : null;
      answerNote = a ? `${teams[a.side].abbr} answered ${teams[lastRun.side].abbr}'s ${lastRun.label} with a ${a.how} (${qName(a.at.period)} ${a.at.clock})${a.strong ? '' : ' — not a strong answer'}`
                     : `${teams[lastRun.side].abbr}'s ${lastRun.label} run (${qName(lastRun.end.period)} ${lastRun.end.clock}) is unanswered`;
    }

    const rank = x => gIdx(x.grade) * 100 + (x.answered ? 10 : 0) + (x.control ? 10 : 0) + (x.lead > 0 ? 5 : 0);
    const best = rank(sides.away) >= rank(sides.home) ? sides.away : sides.home;
    const aSide = gIdx(best.grade) >= gIdx('A');
    if (aSide && best.lead <= 0) best.gates.push('Trailing team: an A grade is a comeback signal, not a bet');
    const pick = aSide && best.lead > 0 ? best : null;
    const ph = out.phase.key;
    let action;
    if (ph === 'final') action = 'FINAL';
    else if (ph === 'scout') action = 'SCOUT';
    else if (ph === 'under5' || ph === 'ot') action = 'NO NEW BET';
    else if (ph === 'end3' || ph === 'q3') action = pick ? 'LEAN' : 'NO BET';
    else action = pick ? 'BET' : 'NO BET';
    const pickAbbr = pick ? pick.abbr : null;
    const headline = action === 'BET' ? `BET ${pickAbbr} ML` : action === 'LEAN' ? `LEAN ${pickAbbr}`
      : action === 'FINAL' ? `Final: ${teams.away.score > teams.home.score ? teams.away.abbr : teams.home.abbr} won`
      : action === 'SCOUT' ? 'Scouting only' : action === 'NO NEW BET' ? 'No new bets (read only)' : 'NO BET';

    let price = null;
    const focus = pick || best, k = ctx.kalshi || null;
    if (k && k[focus.abbr] != null) { const p = +k[focus.abbr]; price = { abbr: focus.abbr, prob: p, american: toAmerican(p), pricedIn: p >= 0.85 }; }

    const risk = [], fs = focus.side, fo = other(fs);
    if (qT[fo].tov >= 1 && period >= 4) risk.push(`${teams[fs].abbr} turnovers in the 4th: ${qT[fo].tov}`);
    if (Math.abs(margin[fs]) <= 8) risk.push('One-score game');
    if (fs === 'away') risk.push(`${teams[fs].abbr} on the road`);
    if (momentum[fo].explosive >= 3) risk.push(`${teams[fo].abbr} hitting big plays (${momentum[fo].explosive} of 20+ yards since ${t0 ? 'halftime' : 'kickoff'})`);
    if (qb[fs].turnovers >= 1) risk.push(`${qb[fs].name} has ${qb[fs].turnovers} turnover${qb[fs].turnovers > 1 ? 's' : ''}`);
    if (fake) risk.push(fake.text);

    const changes = [], fbo = teams[other(best.side)].abbr;
    if (best.answered) changes.push(`Downgrade if ${fbo} scores again before ${best.abbr} does`);
    else if (best.oppRun) changes.push(`${best.abbr} must answer ${fbo}'s ${best.oppRun.label} run (a touchdown for an A)`);
    else changes.push('Wait for a run and an answer: no answered run = B maximum');
    if (!best.control) changes.push(`${best.abbr} needs 3 of 5 control stats`);
    if (!best.qbEdge) changes.push('A QB edge would lift it');
    if (best.lead <= 0) changes.push(`${best.abbr} has to lead before it can be a bet`);

    Object.assign(out, {
      runs: runs.map(r => ({ team: teams[r.side].abbr, label: r.label, kind: r.kind, end: r.end })), lastRun: lastRun ? { team: teams[lastRun.side].abbr, label: lastRun.label } : null,
      whoAnswered, answerNote, scoring: ev.map(e => ({ team: teams[e.side].abbr, pts: e.pts, at: `${qName(e.period)} ${fmt(e.remain)}`, type: e.type, score: `${e.away}-${e.home}` })),
      control: { window: t0 ? 'second half' : 'game', rows: ctrl.rows, wins: ctrl.wins, team: ctrl.team ? teams[ctrl.team].abbr : null },
      momentum, fake, qb, sides, price, risk, changes, next: nextCheck(ph),
      decision: { grade: best.grade, action, pick: pickAbbr, side: pick ? pick.side : null, focus: best.abbr, focusSide: best.side, headline, lock: !!(pick && pick.lock) },
    });
    out.earlyPath = earlyPath(Object.assign(S, { phase: out.phase }), ctx);
    out.logLine = logLine(out, ctx);
    return out;
  }

  function toAmerican(p) { if (!(p > 0 && p < 1)) return null; return p >= 0.5 ? Math.round(-100 * p / (1 - p)) : Math.round(100 * (1 - p) / p); }
  function logLine(r, ctx) {
    const d = ctx.date || '', g = `${r.teams.away.abbr} @ ${r.teams.home.abbr}`;
    const pick = r.decision.action === 'BET' ? `${r.decision.pick} ML` : (r.decision.action === 'LEAN' ? `lean ${r.decision.pick}` : 'no bet');
    const price = r.price ? `${Math.round(r.price.prob * 100)}c` : '–';
    const note = (r.sides[r.decision.focusSide] || {}).gates?.[0] || '';
    return `${d} | ${g} | ${r.clockLabel} | ${r.teams.away.score}-${r.teams.home.score} | ${r.decision.grade} | ${pick} | ${price} | ${note}`;
  }

  return { analyze, epOf, normPlays, scoringEvents, findRuns, findAnswer, phaseOf, toAmerican };
}));
