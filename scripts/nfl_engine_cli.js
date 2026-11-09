#!/usr/bin/env node
/* Run the NFL live engine on one ESPN summary (for the Python checkpoint logger).
 *   node scripts/nfl_engine_cli.js <summary.json> <ctx.json>   ->  prints a compact grade as JSON
 */
'use strict';
const fs = require('fs');
const path = require('path');
const E = require('../docs/nfl/engine.js');
const [summaryPath, ctxPath] = process.argv.slice(2);
const summary = JSON.parse(fs.readFileSync(summaryPath, 'utf8'));
const ctx = JSON.parse(fs.readFileSync(ctxPath, 'utf8'));
ctx.ep = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'docs', 'nfl', 'ep.json'), 'utf8'));
const r = E.analyze(summary, ctx);
const side = r.decision.focusSide ? r.sides[r.decision.focusSide] : null;
process.stdout.write(JSON.stringify({
  clock: r.clockLabel, phase: r.phase && r.phase.key, grade: r.decision.grade, action: r.decision.action,
  pick: r.decision.pick, focus: r.decision.focus, headline: r.decision.headline,
  who_answered: r.whoAnswered || null, answer: r.answerNote || '', control: r.control ? r.control.team : null,
  control_wins: r.control ? r.control.wins : null, qb: r.qb ? { [r.teams.away.abbr]: r.qb.away.score, [r.teams.home.abbr]: r.qb.home.score } : null,
  gates: side ? side.gates : [], fake: r.fake ? r.fake.text : null,
  early: r.earlyPath ? { full: r.earlyPath.full, any_d: r.earlyPath.anyD } : null, log: r.logLine || '',
}));
