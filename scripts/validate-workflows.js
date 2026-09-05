'use strict';
// Validates workflow JSON files: parseable, connections reference real nodes.
const fs = require('fs');
const path = require('path');

const dir = path.join(__dirname, '..', 'workflows');
let failed = false;
for (const f of fs.readdirSync(dir).filter((x) => x.endsWith('.json'))) {
  const wf = JSON.parse(fs.readFileSync(path.join(dir, f)));
  const names = new Set(wf.nodes.map((n) => n.name));
  const bad = [];
  for (const [from, conn] of Object.entries(wf.connections)) {
    if (!names.has(from)) bad.push(`conn from missing node: ${from}`);
    for (const outputs of conn.main) for (const c of outputs) if (!names.has(c.node)) bad.push(`conn to missing node: ${c.node}`);
  }
  const nodesWithMultipleOutputs = wf.nodes.filter((n) => n.onError === 'continueErrorOutput').map((n) => n.name);
  for (const n of nodesWithMultipleOutputs) {
    if (!wf.connections[n] || wf.connections[n].main.length < 2) bad.push(`node "${n}" has error output but only ${wf.connections[n]?.main.length ?? 0} connection branch(es)`);
  }
  console.log(`${f.padEnd(24)} nodes=${String(wf.nodes.length).padStart(2)} ${bad.length ? 'BROKEN: ' + bad.join('; ') : 'OK'}`);
  if (bad.length) failed = true;
}
process.exit(failed ? 1 : 0);
