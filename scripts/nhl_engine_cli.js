#!/usr/bin/env node
/* Run the NHL live engine on one ESPN summary (for the Python checkpoint logger).
 *   node scripts/nhl_engine_cli.js <summary.json> <ctx.json>   ->  prints a compact read as JSON
 */
'use strict';
const fs = require('fs');
const E = require('../docs/nhl/engine.js');
const [summaryPath, ctxPath] = process.argv.slice(2);
const r = E.analyze(JSON.parse(fs.readFileSync(summaryPath, 'utf8')), JSON.parse(fs.readFileSync(ctxPath, 'utf8')));
const ck = r.checklist;
process.stdout.write(JSON.stringify({
  clock: r.clockLabel, phase: r.phase && r.phase.key, grade: r.decision.grade, action: r.decision.action,
  pick: r.decision.pick, focus: r.decision.focus, headline: r.decision.headline,
  who_answered: r.whoAnswered || null, control: r.control ? r.control.team : null, xg_share: r.control ? r.control.xgShare : null,
  att_share: r.control ? r.control.attShare : null, gates: r.decision.gates || [], reason: (r.decision.reasons || []).join('; '),
  readouts: r.readouts || [], shots: r.game ? { home: r.game.home.sog, away: r.game.away.sog } : null,
  xg: r.game ? { home: +r.game.home.xg.toFixed(2), away: +r.game.away.xg.toFixed(2) } : null,
  checklist: ck ? { passed: ck.passed, total: ck.total, verdict: ck.verdict, reasons: ck.reasons } : null, log: r.logLine || '',
}));
