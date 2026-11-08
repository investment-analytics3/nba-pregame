#!/usr/bin/env node
/* Live backtest + closers draft (developer / occasional run).
 *
 * 1. Pulls ESPN play-by-play for every saved game this season (data/team_games.csv).
 * 2. Closers draft: for each team, who takes and creates the shots in close late
 *    games (4th quarter/OT, last 6 minutes, margin 8 or less). Top 2 by volume;
 *    "elite" = 30%+ of the team's clutch actions at 0.9+ points per action and
 *    20+ points per game. Written to data/closers.json for review. Entries you
 *    have edited (source not "draft") are never overwritten.
 * 3. Replays every game through docs/live/engine.js at the checkpoints in your
 *    timing rules (end of 3rd, 10:00, 8:00, 6:00, 4:00 of the 4th) and records how
 *    BET / LEAN calls did. Written to data/live/backtest.json.
 *
 * In-sample caveat: closers are drafted from the same games that are replayed.
 *    node scripts/live_backtest.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const E = require('../docs/live/engine.js');

const ROOT = path.join(__dirname, '..');
const ESPN = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba';
const CHECKS = [['End 3rd', 3 * 720], ['10:00 Q4', 3 * 720 + 120], ['8:00 Q4', 3 * 720 + 240],
                ['6:00 Q4', 3 * 720 + 360], ['4:00 Q4', 3 * 720 + 480]];
const sleep = ms => new Promise(r => setTimeout(r, ms));

function csv(file) {
  const [head, ...rows] = fs.readFileSync(path.join(ROOT, 'data', file), 'utf8').trim().split('\n');
  const cols = head.split(',');
  return rows.map(r => { const v = r.split(','); return Object.fromEntries(cols.map((c, i) => [c, v[i]])); });
}
async function getJSON(url) {
  for (let a = 0; a < 3; a++) {
    try { const r = await fetch(url, { signal: AbortSignal.timeout(25000) }); if (r.ok) return await r.json(); } catch (e) { /* retry */ }
    await sleep(1500 * (a + 1));
  }
  return null;
}

async function main() {
  const tg = csv('team_games.csv');
  const games = {};
  tg.forEach(r => { games[r.game_id] = games[r.game_id] || { id: r.game_id, date: r.game_date }; });
  const ids = Object.keys(games).sort((a, b) => games[a].date.localeCompare(games[b].date));
  console.log(`Fetching play-by-play for ${ids.length} games…`);
  const summaries = {};
  for (const id of ids) {
    const s = await getJSON(`${ESPN}/summary?event=${id}`);
    if (s && s.plays && s.plays.length) summaries[id] = s;
    await sleep(250);
  }
  console.log(`  got ${Object.keys(summaries).length}`);

  // ---- season points per game (for elite) --------------------------------------
  const ppg = {};
  csv('player_games.csv').forEach(p => {
    if (p.dnp === '1' || !(+p.minutes > 0)) return;
    const k = `${p.team}|${p.player}`;
    const a = ppg[k] = ppg[k] || [0, 0];
    a[0] += +p.pts; a[1] += 1;
  });

  // ---- clutch creation -------------------------------------------------------
  const clutch = {}; // team -> name -> stats
  for (const s of Object.values(summaries)) {
    const teams = E._internal.teamsOf(s), ros = E._internal.roster(s);
    const plays = E._internal.normPlays(s, teams);
    let a = 0, h = 0;
    for (const p of plays) {
      const before = Math.abs(a - h);
      a = p.away; h = p.home;
      const late = p.period >= 5 || (p.period === 4 && p.remain <= 360);
      if (!late || before > 8 || !p.side) continue;
      const abbr = teams[p.side].abbr;
      const add = (id, f, n = 1) => {
        const pl = ros.byId[id]; if (!pl) return;
        const t = clutch[abbr] = clutch[abbr] || {};
        const x = t[pl.name] = t[pl.name] || { actions: 0, fga: 0, fgm: 0, trips: 0, ast: 0, pts: 0 };
        x[f] += n; if (f !== 'pts') x.actions += f === 'fgm' ? 0 : n;
      };
      if (p.fga && p.ath[0]) { add(p.ath[0], 'fga'); if (p.made) add(p.ath[0], 'fgm'); }
      if (p.ftTrip && p.ath[0]) add(p.ath[0], 'trips');
      if (p.made && p.ath[0]) add(p.ath[0], 'pts', p.pts);
      if (p.assist && p.ath[1]) add(p.ath[1], 'ast');
    }
  }
  const draft = {};
  for (const [abbr, players] of Object.entries(clutch)) {
    const total = Object.values(players).reduce((n, x) => n + x.actions, 0);
    const ranked = Object.entries(players).sort((x, y) => y[1].actions - x[1].actions ||
      ((ppg[`${abbr}|${y[0]}`] || [0, 1])[0] / (ppg[`${abbr}|${y[0]}`] || [0, 1])[1]) -
      ((ppg[`${abbr}|${x[0]}`] || [0, 1])[0] / (ppg[`${abbr}|${x[0]}`] || [0, 1])[1]));
    const top = ranked.slice(0, 2);
    const elite = top.filter(([n, x]) => {
      const sp = ppg[`${abbr}|${n}`]; const sppg = sp ? sp[0] / sp[1] : 0;
      return total >= 10 && x.actions / total >= 0.30 && x.pts / Math.max(1, x.actions) >= 0.9 && sppg >= 20;
    }).map(([n]) => n);
    draft[abbr] = {
      closers: top.map(([n]) => n), elite, source: 'draft',
      evidence: top.map(([n, x]) => `${n}: ${x.actions} of ${total} clutch actions, ${x.pts} pts, ${x.fgm}-${x.fga} FG`),
    };
  }
  const cPath = path.join(ROOT, 'data', 'closers.json');
  const current = fs.existsSync(cPath) ? JSON.parse(fs.readFileSync(cPath, 'utf8')) : {};
  const merged = { _note: 'Closers per team. Edit freely: change "source" to "yours" for a team you have reviewed; ' +
    'the backtest only rewrites entries still marked "draft". "elite" names count +1 alone on the floor.' };
  const keepYours = Object.entries(current).filter(([k, v]) => !k.startsWith('_') && (Array.isArray(v) || (v && v.source !== 'draft')));
  Object.keys(draft).sort().forEach(t => { merged[t] = draft[t]; });
  keepYours.forEach(([k, v]) => { merged[k] = v; });
  fs.writeFileSync(cPath, JSON.stringify(merged, null, 1));
  console.log(`Closers: ${Object.keys(draft).length} teams drafted, ${keepYours.length} kept as yours.`);

  // ---- replay ------------------------------------------------------------------
  const ctxClosers = {};
  Object.entries(merged).forEach(([k, v]) => { if (!k.startsWith('_')) ctxClosers[k] = Array.isArray(v) ? { closers: v, elite: [] } : v; });
  const res = await fs.promises.readFile(path.join(ROOT, 'data', 'results.json'), 'utf8').then(JSON.parse).catch(() => []);
  const above = new Set(res.filter(r => r.above_line === 'Yes').map(r => r.game_id));
  const signals = [], byCheck = {};
  CHECKS.forEach(([c]) => { byCheck[c] = { BET: [0, 0], LEAN: [0, 0], leader: [0, 0], grades: {} }; });
  for (const [id, s] of Object.entries(summaries)) {
    const fin = E.analyze(s, {});
    if (fin.state !== 'post') continue;
    const winner = fin.teams.away.score > fin.teams.home.score ? fin.teams.away.abbr : fin.teams.home.abbr;
    let firstBet = null, end3 = null;
    for (const [label, t] of CHECKS) {
      const r = E.analyze(s, { closers: ctxClosers, date: games[id].date }, { uptoT: t });
      const d = r.decision, b = byCheck[label];
      b.grades[d.grade] = (b.grades[d.grade] || 0) + 1;
      // baseline: just back whoever leads at this checkpoint
      const m = r.teams.away.score - r.teams.home.score;
      if (m !== 0) { b.leader[0]++; if ((m > 0 ? r.teams.away.abbr : r.teams.home.abbr) === winner) b.leader[1]++; }
      if (d.action === 'BET' || d.action === 'LEAN') {
        b[d.action][0]++; if (d.pick === winner) b[d.action][1]++;
      }
      if (label === 'End 3rd') end3 = { grade: d.grade, action: d.action, pick: d.pick };
      if (d.action === 'BET' && !firstBet) firstBet = { at: label, grade: d.grade, pick: d.pick, score: `${r.teams.away.score}-${r.teams.home.score}`, lead: Math.abs(m) };
    }
    signals.push({ id, date: games[id].date, game: `${fin.teams.away.abbr}@${fin.teams.home.abbr}`, final: `${fin.teams.away.score}-${fin.teams.home.score}`,
      winner, above_line: above.has(id), end3, bet: firstBet, bet_won: firstBet ? firstBet.pick === winner : null });
  }
  const sum = list => { const b = list.filter(x => x.bet); return { games: list.length, bet_signals: b.length, bet_won: b.filter(x => x.bet_won).length,
    by_grade: ['A+', 'A'].map(g => { const x = b.filter(y => y.bet.grade === g); return { grade: g, n: x.length, won: x.filter(y => y.bet_won).length }; }) }; };
  const out = { built: new Date().toISOString(), note: 'Replay of finished games through the live engine. "bet" = the first BET call (the phone alert). In-sample: closers drafted from the same games.',
    all: sum(signals), above_line: sum(signals.filter(x => x.above_line)), checkpoints: byCheck, games: signals };
  fs.mkdirSync(path.join(ROOT, 'data', 'live'), { recursive: true });
  fs.writeFileSync(path.join(ROOT, 'data', 'live', 'backtest.json'), JSON.stringify(out, null, 1));
  const pct = (w, n) => n ? `${w}/${n} (${Math.round(100 * w / n)}%)` : '0';
  console.log(`\nBET alerts, all games: ${pct(out.all.bet_won, out.all.bet_signals)} of ${out.all.games} games`);
  out.all.by_grade.forEach(g => console.log(`   ${g.grade}: ${pct(g.won, g.n)}`));
  console.log(`BET alerts, above the line: ${pct(out.above_line.bet_won, out.above_line.bet_signals)} of ${out.above_line.games} games`);
  CHECKS.forEach(([c]) => { const b = byCheck[c]; console.log(`  ${c.padEnd(9)} BET ${pct(b.BET[1], b.BET[0])}  LEAN ${pct(b.LEAN[1], b.LEAN[0])}  leader baseline ${pct(b.leader[1], b.leader[0])}  grades ${JSON.stringify(b.grades)}`); });
  const leads = signals.filter(x => x.bet).map(x => x.bet.lead).sort((a, b) => a - b);
  if (leads.length) console.log(`Lead when the BET alert fired: median ${leads[Math.floor(leads.length / 2)]}, range ${leads[0]}-${leads[leads.length - 1]}`);
  const lost = signals.filter(x => x.bet && !x.bet_won).map(x => `${x.date} ${x.game} ${x.bet.at} ${x.bet.grade} ${x.bet.pick} (${x.bet.score}), final ${x.final}`);
  if (lost.length) console.log('BET alerts that lost:\n  ' + lost.join('\n  '));
  return 0;
}
main().then(c => process.exit(c)).catch(e => { console.error(e); process.exit(1); });
