#!/usr/bin/env node
/* Run the college basketball live engine on one ESPN summary (for the Python checkpoint logger).
 *   node scripts/cbb_engine_cli.js <summary.json> <ctx.json>   ->  prints a compact read as JSON
 */
'use strict';
const fs = require('fs');
const E = require('../docs/cbb/engine.js');
const [summaryPath, ctxPath] = process.argv.slice(2);
const r = E.analyze(JSON.parse(fs.readFileSync(summaryPath, 'utf8')), JSON.parse(fs.readFileSync(ctxPath, 'utf8')));
const side = r.decision && r.decision.focusSide ? r.sides[r.decision.focusSide] : null;
const ck = r.checklist;
process.stdout.write(JSON.stringify({
  clock: r.clockLabel, phase: r.phase && r.phase.key, grade: r.decision.grade, action: r.decision.action,
  pick: r.decision.pick, focus: r.decision.focus, headline: r.decision.headline,
  who_answered: r.whoAnswered || null, answer: r.answerNote || '', control: r.control ? r.control.team : null,
  control_wins: r.control ? r.control.wins : null, gates: side ? side.gates : [], fake: r.fake ? r.fake.text : null,
  checklist: ck ? { passed: ck.passed, total: ck.total, verdict: ck.verdict, reasons: ck.reasons } : null, log: r.logLine || '',
}));
