#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { AmazaiStack, SeatConfig } from '../lib/amazai-stack';

const repoRoot = path.join(__dirname, '..', '..');
const seatsPath = path.join(repoRoot, 'scripts', 'seats.json');
const seatsFile = JSON.parse(fs.readFileSync(seatsPath, 'utf8')) as {
  region: string;
  seats: SeatConfig[];
};

// Public identifiers only. One file so the JWT authorizer, the Lambda
// environment, identity.py (via that environment) and the SPA env examples
// cannot drift onto a second tenant. An env override that disagrees fails
// synth instead of deploying a different issuer than the console.
const auth0File = JSON.parse(fs.readFileSync(path.join(repoRoot, 'config', 'auth0.json'), 'utf8')) as {
  domain?: string;
  audience?: string;
};

function auth0Value(name: 'domain' | 'audience', envName: string): string {
  const fromFile = String(auth0File[name] ?? '').trim();
  if (!fromFile) {
    throw new Error(`config/auth0.json ${name} is empty`);
  }
  if (name === 'domain' && (fromFile.includes('://') || fromFile.includes('/'))) {
    throw new Error(
      `config/auth0.json domain must be a bare host (identity.issuer() prepends https://), got ${fromFile}`,
    );
  }
  const fromEnv = (process.env[envName] ?? '').trim();
  if (fromEnv && fromEnv !== fromFile) {
    throw new Error(
      `${envName} is ${fromEnv} but config/auth0.json ${name} is ${fromFile}. `
      + 'config/auth0.json is the source of record for issuer and audience.',
    );
  }
  return fromFile;
}

const app = new cdk.App();

// One account, one region. Region comes from seats.json so the CDK stack and
// the seat provisioner can never disagree about where the harnesses live.
new AmazaiStack(app, 'AmazaiStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    // AWS_REGION is what the deploy script and AWS CLI use. Prefer it over a
    // stale CDK_DEFAULT_REGION inherited from another local project.
    region: process.env.AWS_REGION ?? process.env.CDK_DEFAULT_REGION ?? seatsFile.region,
  },
  seats: seatsFile.seats,
  ownerEmail: process.env.AMAZAI_OWNER_EMAIL ?? 'jaylen.jefferson@amazflow.com',
  // Auth0 client credentials stay out of CDK and Lambda; this stack verifies
  // the tokens the SPA obtains through PKCE. Domain and audience come only
  // from config/auth0.json.
  auth0Domain: auth0Value('domain', 'AMAZAI_AUTH0_DOMAIN'),
  auth0Audience: auth0Value('audience', 'AMAZAI_AUTH0_AUDIENCE'),
  description: 'AmazAI control plane: identity, orchestration, execution roles, evidence.',
});

app.synth();
