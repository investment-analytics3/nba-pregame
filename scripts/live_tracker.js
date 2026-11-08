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
function end3Message(g, r, scoreTxt, state) {
  // One verdict from the 7-item checklist (docs/live/engine.js): "7 of 7, parlay-ready" or "N of 7, skip: reasons".
  const c = r.checklist, d = r.decision;
  const when = r.period === 3 ? 'End of 3rd' : r.clockLabel;
  if (!c) return { title: `${when}: grade ${d.grade}`, body: scoreTxt, priority: 'default', ready: false };
  const price = c.price ? ` · Kalshi ${c.price.cents}c (+${c.price.returnPct}%)` : ' · no Kalshi price';
  const rec = r.favorite && r.favorite.record && r.favorite.record.n && r.favorite.up10
    ? ` · fav up 10+ won ${r.favorite.record.won}-${r.favorite.record.n - r.favorite.record.won} this season` : '';
  let parlay = '';
  if (c.ready && c.price) {
    // pair with another tracked game that was parlay-ready at its end of the 3rd and is still on
    const other = Object.entries(state.games).find(([id, s]) => id !== g.id && s.alerts.end3 && s.alerts.end3.ready && !s.final && s.alerts.end3.kalshiFav);
    if (other) {
      const [, s2] = other, combo = c.price.prob * s2.alerts.end3.kalshiFav;
      parlay = ` · parlay with ${s2.label}: ${Math.round(combo * 100)}c${combo > 0.80 ? ' (above 80c ceiling)' : ' (within 80c ceiling)'}`;
    }
  }
  if (c.ready) {
    return { title: `${when}: ${c.verdict}`, priority: 'high', ready: true,
             body: `${scoreTxt} · ${c.fav} (fav) up ${r.favorite.leadEnd3 ?? r.favorite.lead}${price}${parlay}${rec}` };
  }
  const leadSide = r.teams.away.score === r.teams.home.score ? null : (r.teams.away.score > r.teams.home.score ? 'away' : 'home');
  const lg = leadSide ? r.sides[leadSide] : null;
  const later = lg && ['A+', 'A', 'B'].includes(lg.grade) ? ` · grade ${lg.grade}: a BET signal may still come` : ' · likely no bet';
  return { title: `${when}: ${c.verdict}`, priority: 'default', ready: false,
           body: `${scoreTxt} · ${c.reasons.slice(0, 3).join('; ')}${price}${later}` };
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
                                     favDefense: g.fav_defense, favB2B: g.fav_b2b, league: ctx.league, fav10: ctx.fav10 });
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
        const msg = end3Message(g, r, scoreTxt, state);
        if (await alert(`${title} - ${msg.title}`, `${msg.body} · Tap to open`, url, msg.priority)) {
          st.alerts.end3 = { at: cp.at, clock: r.clockLabel, grade: r.decision.grade, action: r.decision.action,
                             score: cp.score, ready: msg.ready, favLead: r.favorite ? (r.favorite.leadEnd3 ?? r.favorite.lead) : null,
                             kalshiFav: r.favorite && k ? (k[r.favorite.abbr] ?? null) : null,
                             defense: r.favorite ? r.favorite.tonight : null,
                             checklist: r.checklist ? { passed: r.checklist.passed, total: r.checklist.total,
                               items: r.checklist.items.map(i => ({ key: i.key, pass: i.pass, detail: i.detail })) } : null };
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
