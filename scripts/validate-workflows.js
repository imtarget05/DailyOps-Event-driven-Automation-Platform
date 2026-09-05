'use strict';
// Validates workflow JSON files: parseable, connections reference real nodes.
// Plus non-failing audits: credential coverage + fan-in barrier integrity.
const fs = require('fs');
const path = require('path');

// Node types that REQUIRE a credential to run without warnings.
const CRED_REQUIRED = {
  'n8n-nodes-base.telegramTrigger': 'Telegram bot',
  'n8n-nodes-base.telegram': 'Telegram bot',
  'n8n-nodes-base.gmail': 'Gmail OAuth2',
  'n8n-nodes-base.googleSheets': 'Google Sheets OAuth2',
  'n8n-nodes-base.postgres': 'Postgres',
};

const dir = path.join(__dirname, '..', 'n8n', 'workflows');
let failed = false;
for (const f of fs.readdirSync(dir).filter((x) => x.endsWith('.json'))) {
  const wf = JSON.parse(fs.readFileSync(path.join(dir, f)));
  const names = new Set(wf.nodes.map((n) => n.name));
  const bad = [];
  const warn = [];
  for (const [from, conn] of Object.entries(wf.connections)) {
    if (!names.has(from)) bad.push(`conn from missing node: ${from}`);
    for (const outputs of conn.main) for (const c of outputs) if (!names.has(c.node)) bad.push(`conn to missing node: ${c.node}`);
  }
  const nodesWithMultipleOutputs = wf.nodes.filter((n) => n.onError === 'continueErrorOutput').map((n) => n.name);
  for (const n of nodesWithMultipleOutputs) {
    if (!wf.connections[n] || wf.connections[n].main.length < 2) bad.push(`node "${n}" has error output but only ${wf.connections[n]?.main.length ?? 0} connection branch(es)`);
  }
  // Audit (warn-only): nodes whose type needs credentials but has none attached.
  for (const n of wf.nodes) {
    const need = CRED_REQUIRED[n.type];
    if (need && !n.credentials) warn.push(`no credential: "${n.name}" needs ${need}`);
    const s = JSON.stringify(n.parameters || {});
    if (s.includes('REPLACE_WITH_SHEET_ID')) warn.push(`placeholder: "${n.name}" still has REPLACE_WITH_SHEET_ID`);
    if (s.includes('ops-team@example.com')) warn.push(`placeholder: "${n.name}" still has ops-team@example.com`);
    if (s.includes('example.com/api/execute')) warn.push(`placeholder: "${n.name}" still points at example.com API`);
  }
  // Audit (fail): fan-in barrier must join 2+ branches (prevents Nx downstream execution).
  for (const n of wf.nodes.filter((x) => x.name === 'Join Specialists')) {
    const incoming = Object.entries(wf.connections)
      .filter(([, conn]) => conn.main.some((outs) => outs.some((c) => c.node === n.name)))
      .map(([from]) => from);
    if (incoming.length < 2) bad.push(`barrier "${n.name}" joins only ${incoming.length} branch(es), expected 4 specialists`);
  }
  console.log(`${f.padEnd(24)} nodes=${String(wf.nodes.length).padStart(2)} ${bad.length ? 'BROKEN: ' + bad.join('; ') : 'OK'}`);
  for (const w of warn) console.log(`   warn: ${w}`);
  if (bad.length) failed = true;
}
process.exit(failed ? 1 : 0);
