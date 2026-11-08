/* NBA live-game engine: run detection, control vs momentum, closers, A+/A/B/C grade.
 *
 * One file, used in two places so they always agree:
 *   - the Live page (browser): window.LiveEngine.analyze(summary, ctx)
 *   - the live tracker / backtest (Node): require('./engine.js').analyze(...)
 *
 * Input: an ESPN "summary" response (plays, boxscore, header) and a context:
 *   ctx = { closers: { BOS: { closers: ["Name", ...], elite: ["Name"] } },
 *           fav: "BOS",                    // pregame favorite (context only)
 *           kalshi: { BOS: 0.88, MIL: 0.12 } | null,
 *           date: "2026-11-08" }
 * Rules: see the "Live mode" section of the design spec. No randomness, no AI.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.LiveEngine = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const GRADES = ['C', 'B', 'A', 'A+'];
  const gIdx = g => GRADES.indexOf(g);
  const QLEN = 720, OTLEN = 300;

  // ---------------------------------------------------------------- clock --
  function clockSec(s) {
    if (s == null) return 0;
    s = String(s);
    if (s.includes(':')) { const [m, x] = s.split(':'); return (+m) * 60 + (+x); }
    return +s || 0;
  }
  const periodLen = p => (p <= 4 ? QLEN : OTLEN);
  function elapsed(period, remain) {
    let t = 0;
    for (let p = 1; p < period; p++) t += periodLen(p);
    return t + periodLen(period) - remain;
  }
  function fmtClock(sec) {
    if (sec < 60) return (Math.round(sec * 10) / 10).toFixed(1);
    const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
    return `${m}:${String(s).padStart(2, '0')}`;
  }
  const qName = p => (p <= 4 ? ['1st', '2nd', '3rd', '4th'][p - 1] : (p === 5 ? 'OT' : `${p - 4}OT`));

  // --------------------------------------------------------------- teams ---
  function teamsOf(summary) {
    const comp = summary.header.competitions[0];
    const out = {};
    comp.competitors.forEach(c => {
      out[c.homeAway] = {
        id: String(c.team.id), abbr: c.team.abbreviation, name: c.team.name || c.team.displayName,
        location: c.team.location, score: c.score != null ? +c.score : null,
      };
    });
    return out;
  }

  function roster(summary) {
    const byId = {}, starters = {};
    ((summary.boxscore || {}).players || []).forEach(tp => {
      const tid = String(tp.team.id);
      starters[tid] = [];
      const st = tp.statistics[0] || {}, labels = st.labels || [];
      (st.athletes || []).forEach(a => {
        const id = String(a.athlete.id);
        const stat = {};
        labels.forEach((l, i) => { stat[l] = (a.stats || [])[i]; });
        byId[id] = { id, team: tid, name: a.athlete.displayName, short: a.athlete.shortName,
                     starter: !!a.starter, dnp: !!a.didNotPlay, box: stat };
        if (a.starter) starters[tid].push(id);
      });
    });
    return { byId, starters };
  }

  // --------------------------------------------------------------- plays ---
  const RIM = /(layup|dunk|tip shot|tip-in|alley oop|putback|finger roll)/i;
  function shotInfo(p) {
    const txt = p.text || '';
    const dist = (txt.match(/(\d+)-foot/) || [])[1];
    const c = p.coordinate || {};
    const hasXY = c.x != null && c.x > -1000 && c.y > -1000;
    const three = p.pointsAttempted === 3 || /three point/i.test(txt);
    const rim = RIM.test(txt) || RIM.test((p.type || {}).text || '') || (dist != null && +dist <= 4);
    const paint = !three && (rim || (hasXY ? (c.x >= 17 && c.x <= 33 && c.y <= 14) : (dist != null && +dist <= 8)));
    const corner3 = three && hasXY && c.y <= 9 && (c.x <= 3 || c.x >= 47);
    return { three, rim, paint, corner3, dist: dist != null ? +dist : null };
  }

  function normPlays(summary, teams) {
    const awayId = teams.away.id, homeId = teams.home.id;
    let lastA = 0, lastH = 0;
    return (summary.plays || []).map((p, i) => {
      const type = (p.type || {}).text || '';
      const period = (p.period || {}).number || 1;
      const remain = clockSec((p.clock || {}).displayValue);
      const team = p.team ? String(p.team.id) : null;
      const a = p.awayScore != null ? +p.awayScore : lastA, h = p.homeScore != null ? +p.homeScore : lastH;
      lastA = a; lastH = h;
      const ft = /free throw/i.test(type) || /free throw/i.test(p.text || '');
      const fga = !!p.shootingPlay && !ft;
      const made = !!p.scoringPlay && (p.scoreValue || 0) > 0;
      const o = {
        i, type, text: p.text || '', period, remain, t: elapsed(period, remain), team,
        side: team === awayId ? 'away' : (team === homeId ? 'home' : null),
        away: a, home: h, made, pts: made ? (p.scoreValue || 0) : 0, fga, ft,
        ftTrip: ft && /(1 of [123])/i.test(type + ' ' + (p.text || '')) && !/technical/i.test(type),
        oreb: /offensive (team )?rebound/i.test(type) || (/rebound/i.test(type) && /offensive/i.test(type)),
        dreb: /defensive (team )?rebound/i.test(type) || (/rebound/i.test(type) && /defensive/i.test(type)),
        tov: /turnover/i.test(type) && !/no turnover/i.test(type),
        foul: /foul/i.test(type) && !/technical/i.test(type),
        sub: /substitution/i.test(type),
        assist: made && /assists\)/i.test(p.text || ''),
        ath: (p.participants || []).map(x => String((x.athlete || {}).id)).filter(Boolean),
      };
      if (fga) Object.assign(o, shotInfo(p));
      return o;
    });
  }

  // ------------------------------------------------------------ lineups ----
  // Who is on the floor: starters in Q1; later periods are rebuilt from evidence
  // (anyone in a play before being subbed in was on at the start of the period).
  function lineups(plays, ros, teams, uptoIdx) {
    const res = {};
    ['away', 'home'].forEach(side => {
      const tid = teams[side].id;
      const onTeam = id => (ros.byId[id] || {}).team === tid;
      let current = new Set(ros.starters[tid] || []);
      let confident = current.size === 5;
      let seenNow = new Set();
      const periods = [...new Set(plays.filter(p => p.i <= uptoIdx).map(p => p.period))].sort((a, b) => a - b);
      periods.forEach(per => {
        const pp = plays.filter(p => p.period === per && p.i <= uptoIdx);
        seenNow = new Set();
        pp.forEach(p => p.ath.forEach(id => { if (onTeam(id)) seenNow.add(id); }));
        if (per > 1) {
          const start = new Set(), seen = new Set();
          pp.forEach(p => {
            if (p.sub) {
              const [inn, out] = p.ath;
              if (out && onTeam(out) && !seen.has(out)) start.add(out);
              if (out) seen.add(out);
              if (inn) seen.add(inn);
            } else {
              p.ath.forEach(id => { if (onTeam(id) && !seen.has(id)) { start.add(id); seen.add(id); } });
            }
          });
          if (start.size >= 5) { current = new Set([...start].slice(0, 5)); confident = start.size === 5; }
          else {
            // fill the gaps with the end-of-last-period lineup
            const prev = [...current].filter(id => !start.has(id) && !seen.has(id));
            current = new Set([...start, ...prev].slice(0, 5));
            confident = false;
          }
        }
        pp.forEach(p => {
          if (!p.sub) return;
          const [inn, out] = p.ath;
          if (!inn || !onTeam(inn)) return;
          if (out && current.has(out)) current.delete(out); else if (out) confident = false;
          current.add(inn);
          if (current.size > 5) confident = false;
        });
      });
      // a player's status is certain if the lineup is, or if he shows up in this period's plays
      res[side] = { ids: [...current], confident, certain: id => confident || seenNow.has(id) };
    });
    return res;
  }

  // ------------------------------------------------------------- scoring ---
  function scoringEvents(plays) {
    return plays.filter(p => p.made && p.side).map(p => ({ side: p.side, pts: p.pts, t: p.t, i: p.i,
      period: p.period, remain: p.remain, away: p.away, home: p.home, three: !!p.three }));
  }
  const margin = (e, side) => (side === 'away' ? e.away - e.home : e.home - e.away);
  const other = s => (s === 'away' ? 'home' : 'away');

  // Run = 6-0 or better, an 8-2 type stretch (6+ points, opponent 2 or fewer,
  // net +6), or a lead change followed by 2+ consecutive scores by that team.
  function findRuns(ev) {
    const runs = [];
    ['away', 'home'].forEach(side => {
      let i = 0;
      while (i < ev.length) {
        if (ev[i].side !== side) { i++; continue; }
        let xs = 0, os = 0, best = null, x3 = 0;
        for (let j = i; j < ev.length; j++) {
          if (ev[j].side === side) {
            xs += ev[j].pts; if (ev[j].three) x3 += 3;
            if (xs >= 6 && xs - os >= 6) best = { j, xs, os, x3 };
          } else {
            if (os + ev[j].pts > 2) break;
            os += ev[j].pts;
          }
        }
        if (best) {
          runs.push({ side, pts: best.xs, opp: best.os, from3: best.x3, kind: 'run', s: i, e: best.j });
          i = best.j + 1;
        } else i++;
      }
      // lead change + 2 straight scores
      for (let k = 0; k + 2 < ev.length; k++) {
        const prev = k ? margin(ev[k - 1], side) : 0;
        if (ev[k].side === side && prev <= 0 && margin(ev[k], side) > 0 &&
            ev[k + 1].side === side && ev[k + 2].side === side) {
          if (!runs.some(r => r.side === side && r.s <= k + 2 && r.e >= k)) {
            const pts = ev[k].pts + ev[k + 1].pts + ev[k + 2].pts;
            runs.push({ side, pts, opp: 0, from3: [k, k + 1, k + 2].reduce((n, x) => n + (ev[x].three ? 3 : 0), 0),
                        kind: 'lead change', s: k, e: k + 2 });
          }
        }
      }
    });
    runs.sort((a, b) => a.e - b.e || a.s - b.s);
    return runs.map(r => Object.assign(r, {
      startT: ev[r.s].t, endT: ev[r.e].t, start: { period: ev[r.s].period, clock: fmtClock(ev[r.s].remain) },
      end: { period: ev[r.e].period, clock: fmtClock(ev[r.e].remain) },
      label: `${r.pts}-${r.opp}`,
    }));
  }

  // Answer = the other team scores 4+ straight points, or (re)takes the lead,
  // after the run ends.
  function findAnswer(ev, run) {
    const ans = other(run.side);
    let streak = 0, startK = null;
    const ledAtEnd = margin(ev[run.e], run.side) > 0;
    for (let k = run.e + 1; k < ev.length; k++) {
      if (ev[k].side === ans) {
        if (streak === 0) startK = k;
        streak += ev[k].pts;
        if (streak >= 4) return { side: ans, how: `${streak}-0`, k, t: ev[k].t, startK,
          at: { period: ev[k].period, clock: fmtClock(ev[k].remain) } };
        if (ledAtEnd && margin(ev[k], ans) > 0) return { side: ans, how: 'retook the lead', k, t: ev[k].t, startK,
          at: { period: ev[k].period, clock: fmtClock(ev[k].remain) } };
      } else { streak = 0; }
    }
    return null;
  }

  // -------------------------------------------------------------- control --
  const CATS = [
    { key: 'reb', label: 'Rebounds', min: 2 },
    { key: 'oreb', label: 'Off. rebounds', min: 1 },
    { key: 'ast', label: 'Assists', min: 2 },
    { key: 'paint', label: 'Points in paint', min: 4 },
    { key: 'quality', label: 'Shot quality', min: 0.08 },
  ];
  function tally(plays, t0, t1) {
    const z = () => ({ reb: 0, oreb: 0, ast: 0, paint: 0, fga: 0, good: 0, trips: 0, tov: 0, fg3m: 0, fg3a: 0,
                       fgm: 0, pts: 0, quality: null });
    const s = { away: z(), home: z() };
    plays.forEach(p => {
      if (!p.side || p.t <= t0 || p.t > t1) return;
      const x = s[p.side];
      if (p.oreb) { x.oreb++; x.reb++; } else if (p.dreb) x.reb++;
      if (p.assist) x.ast++;
      if (p.tov) x.tov++;
      if (p.fga) {
        x.fga++;
        if (p.rim || p.corner3) x.good++;
        if (p.three) { x.fg3a++; if (p.made) x.fg3m++; }
        if (p.made) { x.fgm++; if (p.paint) x.paint += p.pts; }
      }
      if (p.ftTrip) { x.trips++; x.good++; }
      x.pts += p.pts;
    });
    ['away', 'home'].forEach(k => {
      const d = s[k].fga + s[k].trips;
      s[k].quality = d >= 4 ? s[k].good / d : null;
    });
    return s;
  }
  function catWinner(a, b, min) {
    if (a == null || b == null) return null;
    if (a - b >= min) return 'away';
    if (b - a >= min) return 'home';
    return null;
  }
  function control(plays, nowT, period, remain) {
    // Weight the 4th: use the current quarter once 4+ minutes are played in it,
    // otherwise the last 12 game minutes; a tie in that window falls back to the full game.
    const inQ = periodLen(period) - remain;
    let t0, label;
    if (period >= 3 && inQ >= 240) { t0 = nowT - inQ; label = period === 3 ? '3rd quarter' : (period === 4 ? '4th quarter' : qName(period)); }
    else if (nowT >= 720) { t0 = nowT - 720; label = 'last 12 min'; }
    else { t0 = 0; label = 'full game'; }
    const win = tally(plays, t0, nowT), full = tally(plays, 0, nowT);
    const rows = CATS.map(c => {
      let w = catWinner(win.away[c.key], win.home[c.key], c.min), from = label;
      if (!w && t0 > 0) { w = catWinner(full.away[c.key], full.home[c.key], c.min); from = w ? 'full game' : label; }
      return { key: c.key, label: c.label, away: win.away[c.key], home: win.home[c.key],
               fullAway: full.away[c.key], fullHome: full.home[c.key], winner: w, from };
    });
    const wins = { away: rows.filter(r => r.winner === 'away').length, home: rows.filter(r => r.winner === 'home').length };
    let team = null;
    if (wins.away >= 2 && wins.away > wins.home) team = 'away';
    if (wins.home >= 2 && wins.home > wins.away) team = 'home';
    return { window: label, t0, rows, wins, team, win, full };
  }

  // -------------------------------------------------------------- closers --
  const norm = s => String(s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  function playerLine(plays, id, t0) {
    const L = { pts: 0, fgm: 0, fga: 0, tov: 0, pf: 0, ast: 0 };
    plays.forEach(p => {
      if (p.t <= t0) return;
      if (p.ath[0] === id) {
        if (p.fga) { L.fga++; if (p.made) L.fgm++; }
        if (p.made) L.pts += p.pts;
        if (p.tov) L.tov++;
        if (p.foul) L.pf++;
      }
      if (p.assist && p.ath[1] === id) L.ast++;
    });
    return L;
  }
  function closerSide(side, ctx, ros, plays, lu, teams, nowT, period) {
    const abbr = teams[side].abbr, tid = teams[side].id;
    const listed = (ctx.closers || {})[abbr] || null;
    const names = listed ? (listed.closers || []) : [];
    const elite = new Set(((listed || {}).elite || []).map(norm));
    const findId = n => Object.values(ros.byId).find(a => a.team === tid && (norm(a.name) === norm(n) || norm(a.short) === norm(n)));
    // who is initiating now: shots, trips and assists in the last 6 game minutes
    const t0 = Math.max(0, nowT - 360), use = {};
    plays.forEach(p => {
      if (p.t <= t0 || p.side !== side) return;
      if ((p.fga || p.ftTrip) && p.ath[0]) use[p.ath[0]] = (use[p.ath[0]] || 0) + 1;
      if (p.assist && p.ath[1]) use[p.ath[1]] = (use[p.ath[1]] || 0) + 1;
    });
    const totalUse = Object.values(use).reduce((a, b) => a + b, 0);
    const initiators = Object.entries(use).sort((a, b) => b[1] - a[1]).slice(0, 3)
      .map(([id, n]) => ({ id, name: (ros.byId[id] || {}).name || 'unknown', share: totalUse ? n / totalUse : 0 }));
    let list = names.map(n => ({ name: n, id: (findId(n) || {}).id || null, elite: elite.has(norm(n)), source: 'list' }));
    let source = listed ? (listed.source || 'list') : 'auto';
    if (!list.length) {
      // no list for this team: the game's two biggest creators (never "elite")
      const game = {};
      plays.forEach(p => {
        if (p.side !== side) return;
        if ((p.fga || p.ftTrip) && p.ath[0]) game[p.ath[0]] = (game[p.ath[0]] || 0) + 1;
        if (p.assist && p.ath[1]) game[p.ath[1]] = (game[p.ath[1]] || 0) + 1;
      });
      list = Object.entries(game).sort((a, b) => b[1] - a[1]).slice(0, 2)
        .map(([id]) => ({ name: (ros.byId[id] || {}).name || 'unknown', id, elite: false, source: 'auto' }));
      source = 'auto';
    }
    const onIds = new Set(lu[side].ids);
    const q4t0 = period >= 4 ? 3 * QLEN : Infinity;
    list.forEach(c => {
      c.onFloor = c.id ? onIds.has(c.id) : null;
      c.game = c.id ? playerLine(plays, c.id, -1) : null;
      c.q4 = c.id && period >= 4 ? playerLine(plays, c.id, q4t0) : null;
      c.box = c.id ? (ros.byId[c.id] || {}).box : null;
      const sp = ((ctx.closers || {})[abbr] || {}).season || {};
      c.season = sp[c.name] || null;
      c.form = form(c, nowT);
      c.initiating = initiators.some(x => x.id === c.id && x.share >= 0.25);
    });
    const on = list.filter(c => c.onFloor);
    const score = on.length >= 2 ? 2 : (on.length === 1 && on[0].elite ? 1 : 0);
    list.forEach(c => { c.certain = c.id ? lu[side].certain(c.id) : false; });
    const unclear = list.some(c => c.onFloor === null || !c.certain) || (source === 'auto' && totalUse < 4);
    return { abbr, list, score, unclear, confident: lu[side].confident, source, initiators };
  }
  function form(c, nowT) {
    if (!c.game) return null;
    const pf = c.game.pf, mins = c.box && c.box.MIN != null ? +c.box.MIN : null;
    if (pf >= 5) return 'Foul trouble';
    if (c.season && c.season.ppm && mins) {
      const exp = c.season.ppm * mins;
      if (c.game.pts >= exp * 1.2 && c.game.pts >= 6) return 'Hot';
      if (c.game.pts <= exp * 0.8) return 'Cold';
      return 'Normal';
    }
    if (c.game.fga >= 6) {
      const pct = c.game.fgm / c.game.fga;
      return pct >= 0.55 ? 'Hot' : (pct <= 0.35 ? 'Cold' : 'Normal');
    }
    return 'Normal';
  }

  // ---------------------------------------------------------------- phase --
  function phaseOf(state, period, remain) {
    if (state === 'pre') return { key: 'pre', label: 'Pregame', canBet: false };
    if (state === 'post') return { key: 'final', label: 'Final', canBet: false };
    if (period <= 2) return { key: 'scout', label: period === 2 && remain === 0 ? 'Halftime · scouting only' : 'First half · scouting only', canBet: false };
    if (period === 3) return remain === 0 ? { key: 'end3', label: 'End of 3rd · first signal, lean only', canBet: false }
                                          : { key: 'q3', label: '3rd quarter · lean only', canBet: false };
    if (period === 4) {
      if (remain > 600) return { key: 'q4early', label: '4th · window opens at 10:00', canBet: false };
      if (remain > 480) return { key: 'primary', label: '4th 10:00–8:00 · PRIMARY decision window', canBet: true };
      if (remain > 360) return { key: 'confirm', label: '4th 8:00–6:00 · confirm, upgrade or downgrade', canBet: true };
      if (remain >= 240) return { key: 'late', label: '4th 6:00–4:00 · late window', canBet: true };
      return { key: 'under4', label: 'Under 4:00 · no new bets, read only', canBet: false };
    }
    return { key: 'ot', label: 'Overtime · no new bets, read only', canBet: false };
  }

  // ---------------------------------------------------------------- grade --
  function gradeSide(side, S) {
    const o = other(side), abbr = S.teams[side].abbr, oabbr = S.teams[o].abbr;
    const reasons = [], gates = [];
    const oRun = [...S.runs].reverse().find(r => r.side === o) || null;
    const ans = oRun ? findAnswer(S.ev, oRun) : null;
    const answered = !!(ans && ans.side === side);
    const ctrl = S.control.team === side;
    const oppCtrl = S.control.team === o;
    const cs = S.closers[side], co = S.closers[o];
    const closerEdge = cs.score > co.score;
    const lead = S.margin[side];
    const leadStable = lead > 0 && lead >= S.marginAgo[side];
    const momentum = S.momentum[side];
    const momentumOnly = !ctrl && (momentum.fg3m >= 3 || momentum.forced >= 3 || momentum.runs >= 1);

    if (oRun) reasons.push(answered ? `Answered ${oabbr}'s ${oRun.label} with ${ans.how}` : `${oabbr}'s ${oRun.label} run is unanswered`);
    else reasons.push(`${oabbr} has not had a run to answer`);
    reasons.push(ctrl ? `Control (${S.control.wins[side]} of 5)` : (oppCtrl ? `${oabbr} has control` : 'No clear control'));
    reasons.push(closerEdge ? `Closer edge (+${cs.score} vs +${co.score})` : `No closer edge (+${cs.score} vs +${co.score})`);

    let g;
    if (answered) {
      if (ctrl && closerEdge && leadStable) g = 'A+';
      else if (ctrl || closerEdge) g = 'A';
      else g = 'B';
    } else {
      g = (ctrl || closerEdge) ? 'B' : 'C';
      if (momentumOnly && !ctrl) g = 'C';
      gates.push('No answered run = B maximum');
    }

    // Confirmation window: upgrade on a 2+ possession lead the opponent hasn't answered
    if (S.phase.key === 'confirm' && g === 'A' && answered && lead >= 4 && S.marginAt(ans.t)[side] < lead) {
      g = 'A+'; reasons.push(`Lead extended to ${lead}, ${oabbr} has not responded (8:00–6:00 upgrade)`);
    }
    // Turnover spike: 3+ in the last 3 game minutes
    const tovRecent = tally(S.plays, S.nowT - 180, S.nowT)[side].tov;
    if (tovRecent >= 3 && gIdx(g) > 0) { g = GRADES[gIdx(g) - 1]; gates.push(`Turnover spike (${tovRecent} in last 3 min): down one grade`); }
    // No-bet zone
    const recent = S.runs.filter(r => r.endT > S.nowT - 720);
    const trading = recent.some(r => r.side === 'away') && recent.some(r => r.side === 'home');
    if (Math.abs(lead) <= 6 && !closerEdge && trading && g !== 'C') {
      g = 'C'; gates.push('No-bet zone: within 2 possessions, no closer edge, teams trading runs');
    }
    // Closer picture unclear in the primary window
    if (S.phase.key === 'primary' && cs.unclear && gIdx(g) > gIdx('B')) {
      g = 'B'; gates.push('Closer picture unclear at 10:00–8:00 = B maximum');
    }

    // Lock: answered + lead extended 4+ to 6+ + control held 2+ minutes
    let lock = false;
    if (answered && lead >= 6 && lead - S.marginAt(ans.t)[side] >= 4 && ctrl && S.controlAgo.team === side) lock = true;

    return { side, abbr, grade: g, answered, answer: ans, oppRun: oRun, control: ctrl, closerEdge, lead, leadStable,
             momentumOnly, reasons, gates, lock };
  }

  // ------------------------------------------------------------- analyze ---
  function analyze(summary, ctx, opts) {
    ctx = ctx || {}; opts = opts || {};
    const teams = teamsOf(summary);
    const ros = roster(summary);
    let plays = normPlays(summary, teams);
    if (opts.uptoT != null) plays = plays.filter(p => p.t <= opts.uptoT);
    const comp = summary.header.competitions[0];
    const st = comp.status || {}, stType = st.type || {};
    let state = stType.state || 'pre';
    const last = plays[plays.length - 1];
    let period = st.period || (last ? last.period : 1);
    let remain = st.displayClock != null ? clockSec(st.displayClock) : (last ? last.remain : QLEN);
    if (opts.uptoT != null) { // replay: the game as it stood at uptoT
      state = 'in'; period = last ? last.period : 1; remain = last ? last.remain : QLEN;
    }
    if (last && (state === 'post' || opts.uptoT != null)) { period = last.period; remain = last.remain; }
    const nowT = elapsed(period, remain);
    const scoreA = last ? last.away : (teams.away.score || 0), scoreH = last ? last.home : (teams.home.score || 0);
    teams.away.score = state === 'post' && teams.away.score != null && opts.uptoT == null ? teams.away.score : scoreA;
    teams.home.score = state === 'post' && teams.home.score != null && opts.uptoT == null ? teams.home.score : scoreH;

    const out = {
      engine: 1, id: comp.id || (summary.header || {}).id, date: ctx.date || null, teams, state, period, remain,
      clock: fmtClock(remain), clockLabel: state === 'pre' ? 'Pregame' : (state === 'post' ? 'Final' : `${fmtClock(remain)} ${qName(period)}`),
      phase: phaseOf(state, period, remain),
    };
    if (state === 'pre' || !plays.length) {
      out.decision = { grade: '–', action: 'PREGAME', pick: null, headline: 'Not started' };
      return out;
    }

    const ev = scoringEvents(plays);
    const runs = findRuns(ev);
    const marginNow = { away: teams.away.score - teams.home.score, home: teams.home.score - teams.away.score };
    const scoreAt = t => { let e = null; for (const x of ev) { if (x.t <= t) e = x; else break; } return e ? { away: e.away, home: e.home } : { away: 0, home: 0 }; };
    const marginAt = t => { const s = scoreAt(t); return { away: s.away - s.home, home: s.home - s.away }; };
    const lu = lineups(plays, ros, teams, last.i);
    const ctrl = control(plays, nowT, period, remain);
    const ctrlAgo = control(plays.filter(p => p.t <= nowT - 120), Math.max(0, nowT - 120), period, Math.min(periodLen(period), remain + 120));
    const mom = {};
    ['away', 'home'].forEach(s => {
      mom[s] = { fg3m: ctrl.win[s].fg3m, forced: ctrl.win[other(s)].tov, runs: runs.filter(r => r.side === s && r.endT > ctrl.t0).length };
    });
    const closers = {
      away: closerSide('away', ctx, ros, plays, lu, teams, nowT, period),
      home: closerSide('home', ctx, ros, plays, lu, teams, nowT, period),
    };
    const S = { teams, plays, ev, runs, control: ctrl, controlAgo: ctrlAgo, closers, margin: marginNow,
                marginAgo: marginAt(nowT - 120), marginAt, momentum: mom, phase: out.phase, nowT };

    // "Who answered the run?" for the game as a whole: the most recent run, and
    // whether it was itself an answer or has been answered since.
    const lastRun = runs[runs.length - 1] || null;
    let whoAnswered = null, answerNote = 'No runs yet';
    if (lastRun) {
      const prevOpp = [...runs].reverse().find(r => r.side !== lastRun.side && r.e < lastRun.e);
      const a = findAnswer(ev, lastRun);
      if (a) { whoAnswered = a.side; answerNote = `${teams[a.side].abbr} answered ${teams[lastRun.side].abbr}'s ${lastRun.label} with ${a.how}`; }
      else if (prevOpp) {
        const pa = findAnswer(ev, prevOpp);
        if (pa && pa.side === lastRun.side && pa.k <= lastRun.e) {
          whoAnswered = lastRun.side;
          answerNote = `${teams[lastRun.side].abbr} answered ${teams[prevOpp.side].abbr}'s ${prevOpp.label} with a ${lastRun.label} run; ${teams[prevOpp.side].abbr} has not responded`;
        } else answerNote = `${teams[lastRun.side].abbr}'s ${lastRun.label} run is unanswered`;
      } else answerNote = `${teams[lastRun.side].abbr}'s ${lastRun.label} run is unanswered`;
    }

    // Fake comeback: a run built on 3s by a team still losing the possession battle
    let fake = null;
    runs.filter(r => r.endT > nowT - 360).forEach(r => {
      const s = r.side, o = other(s);
      const poss = (ctrl.win[s].oreb - ctrl.win[o].oreb) + (ctrl.win[o].tov - ctrl.win[s].tov);
      const trailedBefore = margin(ev[Math.max(0, r.s - 1)], s) < 0 || r.s === 0;
      if (trailedBefore && r.from3 >= r.pts * 0.5 && poss < 0) {
        fake = { side: s, abbr: teams[s].abbr, text: `${teams[s].abbr}'s ${r.label} came ${r.from3} of ${r.pts} from 3 while losing the possession battle (${poss}): expect regression` };
      }
    });

    const sides = { away: gradeSide('away', S), home: gradeSide('home', S) };
    const rank = x => gIdx(x.grade) * 100 + (x.answered ? 10 : 0) + (x.control ? 10 : 0) + (x.lead > 0 ? 5 : 0);
    const best = rank(sides.away) >= rank(sides.home) ? sides.away : sides.home;
    // A bet (or lean) needs an A/A+ side that is leading: probability over payout.
    const aSide = gIdx(best.grade) >= gIdx('A');
    if (aSide && best.lead <= 0) best.gates.push('Trailing team: an A grade is a comeback signal, not a bet');
    const pick = aSide && best.lead > 0 ? best : null;

    let action;
    const ph = out.phase.key;
    if (ph === 'final') action = 'FINAL';
    else if (ph === 'scout') action = 'SCOUT';
    else if (ph === 'under4' || ph === 'ot') action = 'NO NEW BET';
    else if (ph === 'end3' || ph === 'q3' || ph === 'q4early') action = pick ? 'LEAN' : 'NO BET';
    else action = pick ? 'BET' : 'NO BET';
    const grade = best.grade;
    const pickAbbr = pick ? pick.abbr : null;
    const headline = action === 'BET' ? `BET ${pickAbbr} ML` : action === 'LEAN' ? `LEAN ${pickAbbr}`
      : action === 'FINAL' ? `Final: ${teams.away.score > teams.home.score ? teams.away.abbr : teams.home.abbr} won`
      : action === 'SCOUT' ? 'Scouting only' : action === 'NO NEW BET' ? 'No new bets (read only)' : 'NO BET';

    // price check (Kalshi) — information only, never changes the grade
    let price = null;
    const k = ctx.kalshi || null;
    const focus = pick || best;
    if (k && k[focus.abbr] != null) {
      const p = +k[focus.abbr];
      price = { abbr: focus.abbr, prob: p, american: toAmerican(p), pricedIn: p >= 0.85 };
    }

    // risk flags
    const risk = [];
    const fs = focus.side, fo = other(fs);
    const q4tov = period >= 4 ? tally(plays, 3 * QLEN, nowT)[fs].tov : null;
    if (q4tov != null && q4tov >= 3) risk.push(`${teams[fs].abbr} turnovers: ${q4tov} in the 4th`);
    closers[fs].list.forEach(c => { if (c.game && c.game.pf >= 4) risk.push(`${c.name} has ${c.game.pf} fouls`); });
    const o3 = ctrl.win[fo];
    if (o3.fg3a >= 6 && o3.fg3m / o3.fg3a >= 0.45) risk.push(`${teams[fo].abbr} hot from 3 (${o3.fg3m}/${o3.fg3a} in the ${ctrl.window})`);
    if (fs === 'away') risk.push(`${teams[fs].abbr} on the road`);
    if (Math.abs(marginNow[fs]) <= 6) risk.push('Within two possessions');
    if (fake) risk.push(fake.text);

    // what changes it
    const changes = [];
    const fb = best, fbo = teams[other(fb.side)].abbr;
    if (fb.answered) changes.push(`Downgrade if ${fbo} answers with 4-0 or retakes the lead`);
    else if (fb.oppRun) changes.push(`${fb.abbr} must answer ${fbo}'s ${fb.oppRun.label} run (4-0 or retake the lead)`);
    else changes.push(`Wait for a run and an answer: no answered run = B maximum`);
    if (!fb.control) changes.push(`${fb.abbr} needs 2 of 5 control categories`);
    if (!fb.closerEdge) changes.push('A closer edge would lift it (watch substitutions)');
    if (fb.lead <= 0) changes.push(`${fb.abbr} has to take the lead before it can be a bet`);
    const next = nextCheck(state, period, remain);

    const decision = { grade, action, pick: pickAbbr, side: pick ? pick.side : null, focus: best.abbr, focusSide: best.side, headline, lock: !!(pick && pick.lock) };
    Object.assign(out, {
      runs, lastRun, whoAnswered: whoAnswered ? teams[whoAnswered].abbr : null, answerNote,
      control: { window: ctrl.window, rows: ctrl.rows, wins: ctrl.wins, team: ctrl.team ? teams[ctrl.team].abbr : null },
      momentum: mom, fake, closers, lineupsConfident: { away: lu.away.confident, home: lu.home.confident },
      sides, decision, price, risk, changes, next,
    });
    out.logLine = logLine(out, ctx);
    return out;
  }

  function toAmerican(p) {
    if (p <= 0 || p >= 1) return null;
    return p >= 0.5 ? -Math.round(100 * p / (1 - p)) : Math.round(100 * (1 - p) / p);
  }
  function impliedFromAmerican(o) {
    o = +o; if (!o) return null;
    return o < 0 ? (-o) / (-o + 100) : 100 / (o + 100);
  }
  function nextCheck(state, period, remain) {
    if (state !== 'in') return state === 'post' ? 'Game over' : 'At tip-off';
    if (period < 3) return 'End of the 3rd quarter';
    if (period === 3) return 'End of the 3rd quarter';
    if (period === 4) {
      for (const c of [600, 480, 360, 240]) if (remain > c) return `${fmtClock(c)} 4th`;
      return 'Final';
    }
    return 'Final';
  }
  function logLine(r, ctx) {
    const d = ctx.date || '';
    const g = `${r.teams.away.abbr}@${r.teams.home.abbr}`;
    const clock = r.state === 'in' ? `${r.clock} Q${r.period > 4 ? 'OT' : r.period}` : r.clockLabel;
    const score = `${r.teams.away.score}-${r.teams.home.score}`;
    const pick = r.decision.action === 'BET' ? `${r.decision.pick} ML` : (r.decision.action === 'LEAN' ? `lean ${r.decision.pick}` : 'no bet');
    const price = r.price ? `K ${Math.round(r.price.prob * 100)}c (${r.price.american > 0 ? '+' : ''}${r.price.american})` : '—';
    const note = (r.answerNote || '').replace(/\|/g, '/');
    return `${d} | ${g} | ${clock} | ${score} | ${r.decision.grade} | ${pick} | ${price} | ${note}`;
  }

  return { analyze, toAmerican, impliedFromAmerican, clockSec, findRuns, findAnswer, GRADES, _internal: { normPlays, teamsOf, roster, lineups, control, scoringEvents } };
}));
