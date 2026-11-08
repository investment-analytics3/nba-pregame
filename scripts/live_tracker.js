#!/usr/bin/env node
/* Live tracker: follows today's above-the-line games while they are on.
 *
 * Every ~45 s it pulls ESPN play-by-play and the Kalshi market, grades each game
 * with docs/live/engine.js (the same engine the Live page runs) and:
 *   - sends at most TWO phone alerts per game:
 *       1. "Live read ready" at the end of the 3rd (always)
 *       2. "BET signal" the first time the game reaches A/A+ with a BET call (4th, 10:00-4:00)
 *   - publishes live.json (Kalshi prices, checkpoint grades, alerts sent) to the
 *     "live-data" branch for the Live page; Kalshi can't be called from a web page
 *   - when every game is final, saves the day's checkpoint log to data/live/<date>.json
 *
 * Started by the Scheduler (live.yml). Safe to restart: alert state is reloaded
 * from the live-data branch, so an alert never goes out twice.
 *
 *   node scripts/live_tracker.js [--no-send] [--once] [--max-minutes N]
 */
'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');
const E = require('../docs/live/engine.js');

const ROOT = path.join(__dirname, '..');
const REPO = process.env.GITHUB_REPOSITORY || 'investment-analytics3/nba-pregame';
const PAGES = 'https://investment-analytics3.github.io/nba-pregame/';
const ESPN = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba';
const KALSHI = 'https://api.elections.kalshi.com/trade-api/v2';
const POLL_MS = 45000;
const args = process.argv.slice(2);
const NO_SEND = args.includes('--no-send');
const ONCE = args.includes('--once');
const MAX_MIN = args.includes('--max-minutes') ? +args[args.indexOf('--max-minutes') + 1] : 330;
// ESPN abbreviations that differ from Kalshi's tickers
const KALSHI_CODE = { GS: 'GSW', NY: 'NYK', SA: 'SAS', NO: 'NOP', UTAH: 'UTA', WSH: 'WAS' };
const MON = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];

const sleep = ms => new Promise(r => setTimeout(r, ms));
const log = (...a) => console.log(new Date().toLocaleTimeString('en-US', { timeZone: 'America/Los_Angeles' }), ...a);

async function getJSON(url, headers = {}) {
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const r = await fetch(url, { headers: { Accept: 'application/json', ...headers }, signal: AbortSignal.timeout(20000) });
      if (r.ok) return await r.json();
      if (r.status === 404) return null;
    } catch (e) { /* retry */ }
    await sleep(1500 * (attempt + 1));
  }
  return null;
}

// ------------------------------------------------------------------ Kalshi --
async function kalshiProbs(game, dateET) {
  const a = KALSHI_CODE[game.away.abbr] || game.away.abbr, h = KALSHI_CODE[game.home.abbr] || game.home.abbr;
  const d = new Date(dateET + 'T12:00:00Z');
  const ev = `KXNBAGAME-${String(d.getUTCFullYear()).slice(2)}${MON[d.getUTCMonth()]}${String(d.getUTCDate()).padStart(2, '0')}${a}${h}`;
  // No Origin header: Kalshi refuses browser-style requests.
  const body = await getJSON(`${KALSHI}/markets?event_ticker=${ev}&limit=10`, { 'User-Agent': 'nba-pregame-dashboard/1.0' });
  const ms = (body && body.markets) || [];
  if (!ms.length) return null;
  const out = { event: ev };
  for (const m of ms) {
    const code = m.ticker.split('-').pop();
    const abbr = code === a ? game.away.abbr : (code === h ? game.home.abbr : null);
    if (!abbr) continue;
    const bid = parseFloat(m.yes_bid_dollars), ask = parseFloat(m.yes_ask_dollars), last = parseFloat(m.last_price_dollars);
    let p = null;
    if (bid > 0 && ask > 0 && ask - bid <= 0.1) p = (bid + ask) / 2;
    else if (last > 0) p = last;
    else if (bid > 0 && ask > 0) p = (bid + ask) / 2;
    if (p != null) out[abbr] = Math.round(p * 1000) / 1000;
  }
  return out[game.away.abbr] != null || out[game.home.abbr] != null ? out : null;
}

// ------------------------------------------------------------------ alerts --
async function alert(title, message, click, priority) {
  if (NO_SEND) { log(`[no-send] ALERT ${title} | ${message}`); return true; }
  const topic = (process.env.NTFY_TOPIC || '').trim();
  if (!topic) { log('NTFY_TOPIC missing; alert not sent:', title); return false; }
  try {
    const r = await fetch(`https://ntfy.sh/${topic}`, {
      method: 'POST', body: message,
      headers: { Title: title.replace(/[^\x20-\x7E]/g, '-'), Priority: priority, Tags: 'basketball', Click: click },
      signal: AbortSignal.timeout(20000),
    });
    log(`alert sent (${r.status}): ${title}`);
    return r.ok;
  } catch (e) { log('alert failed:', e.message); return false; }
}

// ------------------------------------------------------ end-of-3rd message --
// Favorite up 10+ -> its own signal with the season record, the favorite's defense
// (last 3 ranks + tonight) and the Kalshi price. Otherwise: wait for the BET signal,
// or likely no bet.
function end3Message(g, r, scoreTxt) {
  const f = r.favorite, d = r.decision;
  const pctK = r.price || null;
  const kal = (abbr) => (r._kalshi && r._kalshi[abbr] != null) ? ` · Kalshi ${Math.round(r._kalshi[abbr] * 100)}%` : '';
  const defTxt = f ? defenseText(f) : '';
  const when = r.period === 3 ? 'End of 3rd' : r.clockLabel;
  if (f && f.up10) {
    const rec = f.record && f.record.n ? ` · favorites up 10+ won ${f.record.won}-${f.record.n - f.record.won} this season` : '';
    return { title: `${when}: ${f.abbr} (fav) up ${f.leadEnd3 != null ? f.leadEnd3 : f.lead}`, priority: 'high', fav10: true,
             body: `${scoreTxt}${rec}${defTxt ? ' · ' + defTxt : ''}${kal(f.abbr)} · grade ${d.grade}` };
  }
  const leadSide = r.teams.away.score === r.teams.home.score ? null : (r.teams.away.score > r.teams.home.score ? 'away' : 'home');
  const lg = leadSide ? r.sides[leadSide] : null;
  const wait = lg && ['A+', 'A', 'B'].includes(lg.grade);
  const lead = leadSide ? `${r.teams[leadSide].abbr} by ${Math.abs(r.teams.away.score - r.teams.home.score)}` : 'tied';
  return { title: `${when}: ${wait ? 'wait for BET signal' : 'likely no bet'}`, priority: 'default', fav10: false,
           body: `${scoreTxt} · ${lead}, grade ${lg ? lg.grade : d.grade}${defTxt ? ' · ' + defTxt : ''}${f ? kal(f.abbr) : ''}` };
}
function defenseText(f) {
  const l3 = f.last3 || {}, t = f.tonight || {};
  const r1 = x => (x && x.r != null ? `#${x.r}` : '–');
  let s = `${f.abbr} D last 3: opp eFG ${r1(l3.opp_efg)}, Def ${r1(l3.def)}`;
  if (t.opp_efg != null) s += ` · tonight opp eFG ${t.opp_efg}%${t.opp_efg_rank ? ` (#${t.opp_efg_rank})` : ''}, Def ${t.def}${t.def_rank ? ` (#${t.def_rank})` : ''}`;
  return s;
}

// ------------------------------------------------------- live-data branch --
function git(argsList, cwd) {
  return execFileSync('git', argsList, { cwd, stdio: ['ignore', 'pipe', 'pipe'] }).toString();
}
function loadState(dateET) {
  try {
    git(['fetch', '-q', 'origin', 'live-data:refs/remotes/origin/live-data'], ROOT);
    const s = JSON.parse(git(['show', 'origin/live-data:live.json'], ROOT));
    if (s.date === dateET) return s;
  } catch (e) { /* no branch yet */ }
  return { date: dateET, games: {} };
}
let lastPublished = '';
function publish(state) {
  const json = JSON.stringify(state);
  if (json === lastPublished) return;
  const token = process.env.GH_TOKEN;
  if (!token) { log('GH_TOKEN missing; live.json not published'); return; }
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'live-'));
  fs.writeFileSync(path.join(dir, 'live.json'), JSON.stringify(state, null, 1));
  // One-commit branch, replaced on every publish (like dev-samples): it only ever holds today's live.json.
  try {
    git(['init', '-q', '-b', 'live-data'], dir);
    git(['add', 'live.json'], dir);
    git(['-c', 'user.name=github-actions[bot]', '-c', 'user.email=41898282+github-actions[bot]@users.noreply.github.com',
      'commit', '-q', '-m', `live ${state.updated}`], dir);
    git(['push', '-q', '-f', `https://x-access-token:${token}@github.com/${REPO}.git`, 'HEAD:live-data'], dir);
    lastPublished = json;
  } catch (e) { log('publish failed:', (e.stderr || e.message).toString().slice(0, 200)); }
  fs.rmSync(dir, { recursive: true, force: true });
}

function saveDayLog(state) {
  const dir = path.join(ROOT, 'data', 'live');
  fs.mkdirSync(dir, { recursive: true });
  const out = { date: state.date, games: {} };
  for (const [id, g] of Object.entries(state.games)) {
    out.games[id] = { label: g.label, final: g.final || null, alerts: g.alerts, checkpoints: g.checkpoints };
  }
  fs.writeFileSync(path.join(dir, `${state.date}.json`), JSON.stringify(out, null, 1));
  log(`day log written: data/live/${state.date}.json`);
}

// -------------------------------------------------------------------- main --
async function main() {
  const ctxPath = path.join(ROOT, 'docs', 'live', 'context.json');
  if (!fs.existsSync(ctxPath)) { log('No docs/live/context.json; run scripts/live_context.py first.'); return 1; }
  const ctx = JSON.parse(fs.readFileSync(ctxPath, 'utf8'));
  if (!ctx.games.length) { log(`No above-the-line games on ${ctx.date}; nothing to track.`); return 0; }
  const state = loadState(ctx.date);
  const deadline = Date.now() + MAX_MIN * 60000;
  log(`Tracking ${ctx.games.length} game(s) for ${ctx.date}${NO_SEND ? ' (no-send)' : ''}: ` +
      ctx.games.map(g => `${g.away.abbr} @ ${g.home.abbr}`).join(', '));

  while (true) {
    let active = 0;
    for (const g of ctx.games) {
      const st = state.games[g.id] = state.games[g.id] ||
        { label: `${g.away.abbr} @ ${g.home.abbr}`, alerts: {}, checkpoints: [], kalshi: [] };
      if (st.final) continue;
      const tip = Date.parse(g.tip_utc);
      if (Date.now() < tip - 20 * 60000 && !ONCE) { active++; continue; } // not yet; keep waiting
      const summary = await getJSON(`${ESPN}/summary?event=${g.id}`);
      if (!summary || !summary.header) { active++; continue; }
      const k = await kalshiProbs(g, ctx.date);
      if (k) {
        const prev = st.kalshi[st.kalshi.length - 1];
        const row = [new Date().toISOString(), k[g.away.abbr] ?? null, k[g.home.abbr] ?? null];
        if (!prev || prev[1] !== row[1] || prev[2] !== row[2]) st.kalshi.push(row);
        st.kalshiEvent = k.event;
      }
      const r = E.analyze(summary, { date: ctx.date, fav: g.fav, closers: ctx.closers, kalshi: k,
                                     favDefense: g.fav_defense, league: ctx.league, fav10: ctx.fav10 });
      r._kalshi = k;
      const cp = { at: new Date().toISOString(), clock: r.clockLabel, score: `${r.teams.away.score}-${r.teams.home.score}`,
                   phase: r.phase.key, grade: r.decision.grade, action: r.decision.action, pick: r.decision.pick,
                   focus: r.decision.focus, kalshi: r.price ? r.price.prob : null, note: r.answerNote, log: r.logLine };
      const prevCp = st.checkpoints[st.checkpoints.length - 1];
      if (!prevCp || prevCp.phase !== cp.phase || prevCp.grade !== cp.grade || prevCp.action !== cp.action || prevCp.pick !== cp.pick) {
        st.checkpoints.push(cp);
        log(`${st.label} ${cp.clock} ${cp.score}: ${r.decision.headline} (${cp.grade})`);
      }
      st.last = cp;
      const url = `${PAGES}live/?g=${g.id}`;
      const title = `${g.away.abbr} @ ${g.home.abbr}`;
      const scoreTxt = `${r.teams.away.abbr} ${r.teams.away.score}-${r.teams.home.score} ${r.teams.home.abbr}`;

      // Alert 1: end of the 3rd (once)
      const past3 = r.state === 'in' && (r.period > 3 || (r.period === 3 && r.remain === 0));
      if (past3 && !st.alerts.end3) {
        const msg = end3Message(g, r, scoreTxt);
        if (await alert(`${title} - ${msg.title}`, `${msg.body} · Tap to open`, url, msg.priority)) {
          st.alerts.end3 = { at: cp.at, clock: r.clockLabel, grade: r.decision.grade, action: r.decision.action,
                             score: cp.score, fav10: msg.fav10, favLead: r.favorite ? r.favorite.lead : null,
                             kalshiFav: r.favorite && k ? (k[r.favorite.abbr] ?? null) : null,
                             defense: r.favorite ? r.favorite.tonight : null };
        }
      }
      // Alert 2: first BET signal (once)
      if (r.decision.action === 'BET' && !st.alerts.bet) {
        const price = r.price ? ` · Kalshi ${Math.round(r.price.prob * 100)}%${r.price.pricedIn ? ' (priced in)' : ''}` : '';
        if (await alert(`${title} - ${r.decision.grade} BET signal`, `${r.clockLabel} · ${r.decision.headline} · ${scoreTxt}${price} · Tap to open`, url, 'high')) {
          st.alerts.bet = { at: cp.at, clock: r.clockLabel, grade: r.decision.grade, pick: r.decision.pick, kalshi: r.price ? r.price.prob : null };
        }
      }
      if (r.state === 'post') {
        st.final = { score: `${r.teams.away.score}-${r.teams.home.score}`,
                     winner: r.teams.away.score > r.teams.home.score ? g.away.abbr : g.home.abbr };
        log(`${st.label} final ${st.final.score}`);
      } else active++;
    }
    state.updated = new Date().toISOString();
    publish(state);
    if (!active) { log('All tracked games are final.'); saveDayLog(state); return 0; }
    if (ONCE) { log('--once: stopping after one pass.'); return 0; }
    if (Date.now() > deadline) { log('Time limit reached; the Scheduler will start a fresh tracker.'); return 0; }
    // Nothing on for a while? Exit; the Scheduler restarts the tracker 15 min before the next tip.
    const nextTip = Math.min(...ctx.games.filter(g => !state.games[g.id].final).map(g => Date.parse(g.tip_utc)));
    if (nextTip - Date.now() > 45 * 60000 && !ctx.games.some(g => {
      const s = state.games[g.id]; return s.last && s.last.phase !== 'pre' && !s.final;
    })) { log('Next tip is more than 45 minutes away; exiting until the Scheduler restarts me.'); return 0; }
    await sleep(POLL_MS);
  }
}

main().then(code => process.exit(code || 0)).catch(e => { console.error(e); process.exit(1); });
