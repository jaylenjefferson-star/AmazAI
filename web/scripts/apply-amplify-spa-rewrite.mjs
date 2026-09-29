/**
 * Merge the SPA 200 rewrite onto the Amplify app this build belongs to.
 *
 * Amplify sets AWS_APP_ID in the build environment. Without it (local
 * `npm run build`, CI) this prints the rule and exits 0 — the static
 * `/welcome/index.html` copy is already in the artifact.
 *
 * Live app is `d2qtxrhp46u9pz` (amazai.co). Amplify's build sets AWS_APP_ID
 * to that app; this script does not hardcode it, so a laptop without the
 * variable cannot retarget production by accident.
 *
 * `--apply` calls `aws amplify update-app` with the merged rule list.
 * It never replaces rules it does not recognize: domain 301s stay.
 * A failed CLI call exits non-zero; amplify.yml treats that as non-fatal.
 */
import { execFileSync } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { SPA_REWRITE, mergeSpaRewrite } from '../src/spaRewrite.js';

const apply = process.argv.includes('--apply');
const appFlag = process.argv.indexOf('--app-id');
const appId = (appFlag >= 0 ? process.argv[appFlag + 1] : '') || process.env.AWS_APP_ID || '';

if (!apply) {
  console.log(JSON.stringify(SPA_REWRITE, null, 2));
  console.log('Dry run. Pass --apply with AWS_APP_ID (Amplify sets it) to merge this rule.');
  process.exit(0);
}

if (!appId) {
  console.error('SPA rewrite not applied: AWS_APP_ID is unset and no --app-id was given.');
  process.exit(0);
}

function aws(args) {
  return execFileSync('aws', args, { encoding: 'utf8' });
}

const described = JSON.parse(aws(['amplify', 'get-app', '--app-id', appId]));
const existing = described?.app?.customRules || [];
const merged = mergeSpaRewrite(existing).map((rule) => {
  const out = {
    source: rule.source,
    target: rule.target,
    status: String(rule.status),
  };
  if (rule.condition) out.condition = rule.condition;
  return out;
});

const dir = mkdtempSync(join(tmpdir(), 'amazai-spa-'));
const file = join(dir, 'rules.json');
writeFileSync(file, JSON.stringify(merged));
aws(['amplify', 'update-app', '--app-id', appId, '--custom-rules', `file://${file}`]);
console.log(`Merged SPA rewrite onto Amplify app ${appId} (${merged.length} rules).`);
