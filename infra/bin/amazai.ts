#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { AmazaiStack, SeatConfig } from '../lib/amazai-stack';

const seatsPath = path.join(__dirname, '..', '..', 'scripts', 'seats.json');
const seatsFile = JSON.parse(fs.readFileSync(seatsPath, 'utf8')) as {
  region: string;
  seats: SeatConfig[];
};

const app = new cdk.App();

// One account, one region. Region comes from seats.json so the CDK stack and
// the seat provisioner can never disagree about where the harnesses live.
new AmazaiStack(app, 'AmazaiStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION ?? seatsFile.region,
  },
  seats: seatsFile.seats,
  ownerEmail: process.env.AMAZAI_OWNER_EMAIL ?? 'jaylen.jefferson@amazflow.com',
  pipedreamProjectId: process.env.PIPEDREAM_PROJECT_ID ?? 'proj_W7sA34l',
  // Left at development unless the deploy says otherwise, so an unconfigured
  // stack cannot reach real connected accounts.
  pipedreamEnvironment: process.env.PIPEDREAM_ENVIRONMENT ?? 'development',
  description: 'AmazAI control plane: identity, orchestration, execution roles, evidence.',
});

app.synth();
