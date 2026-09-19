import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as path from 'node:path';
import * as apigwv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as apigwv2auth from 'aws-cdk-lib/aws-apigatewayv2-authorizers';
import * as apigwv2int from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';

export interface SeatConfig {
  key: string;
  name: string;
  accent: string;
  role: string;
  modelId: string | null;
  maxTokens: number;
  effort: string;
  tools: string[];
  workspaceMode: 'ephemeral' | 'project';
  budget: Record<string, unknown>;
  enabled: boolean;
}

export interface AmazaiStackProps extends cdk.StackProps {
  seats: SeatConfig[];
  ownerEmail: string;
}

const REPO_ROOT = path.join(__dirname, '..', '..');

export class AmazaiStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: AmazaiStackProps) {
    super(scope, id, props);

    // ---------------------------------------------------------------------
    // L0 · Encryption
    // One customer-managed key for the drive, evidence, and browser profiles.
    // ---------------------------------------------------------------------
    const key = new kms.Key(this, 'AmazaiKey', {
      alias: 'alias/amazai',
      description: 'AmazAI: drive, evidence, and browser profile encryption',
      enableKeyRotation: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // ---------------------------------------------------------------------
    // L0 · Identity — one login, self-signup off, TOTP MFA required
    // ---------------------------------------------------------------------
    const userPool = new cognito.UserPool(this, 'UserPool', {
      userPoolName: 'amazai',
      selfSignUpEnabled: false,
      signInAliases: { email: true },
      autoVerify: { email: true },
      mfa: cognito.Mfa.REQUIRED,
      mfaSecondFactor: { sms: false, otp: true },
      passwordPolicy: {
        minLength: 14,
        requireLowercase: true,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: true,
      },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    const userPoolClient = userPool.addClient('Console', {
      userPoolClientName: 'amazai-console',
      generateSecret: false, // public client (SPA / desktop shell)
      authFlows: { userSrp: true },
      accessTokenValidity: cdk.Duration.hours(1),
      idTokenValidity: cdk.Duration.hours(1),
      refreshTokenValidity: cdk.Duration.days(30),
      preventUserExistenceErrors: true,
    });

    // ---------------------------------------------------------------------
    // L2 · Control-plane state — single table, two GSIs
    // ---------------------------------------------------------------------
    const table = new dynamodb.Table(this, 'Table', {
      tableName: 'amazai',
      partitionKey: { name: 'pk', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'sk', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      timeToLiveAttribute: 'ttl',
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // Listings: AGENTS, THREADS, RUNS, ROUTINES, APPROVALS, CONNS…
    table.addGlobalSecondaryIndex({
      indexName: 'gsi1',
      partitionKey: { name: 'gsi1pk', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'gsi1sk', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // Sweeps: RUNSTATE#<state> by heartbeat, APVEXPIRY by expiry,
    // CONNECTOR#<id> by agent. This index is what makes worker-failure
    // recovery and approval expiry a single query each.
    table.addGlobalSecondaryIndex({
      indexName: 'gsi2',
      partitionKey: { name: 'gsi2pk', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'gsi2sk', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // ---------------------------------------------------------------------
    // L3/L4 · Storage
    // ---------------------------------------------------------------------
    const driveBucket = new s3.Bucket(this, 'DriveBucket', {
      bucketName: `amazai-drive-${this.account}`,
      versioned: true, // versioning IS the workspace snapshot strategy
      encryption: s3.BucketEncryption.KMS,
      encryptionKey: key,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // Evidence is append-only by policy: versioned, SSE-KMS, and deliberately
    // given NO lifecycle expiration. See docs/architecture/10.
    const evidenceBucket = new s3.Bucket(this, 'EvidenceBucket', {
      bucketName: `amazai-evidence-${this.account}`,
      versioned: true,
      encryption: s3.BucketEncryption.KMS,
      encryptionKey: key,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    const consoleBucket = new s3.Bucket(this, 'ConsoleBucket', {
      bucketName: `amazai-console-${this.account}`,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true, // console is a build artifact, safe to replace
    });

    // ---------------------------------------------------------------------
    // L0 · One harness execution role PER SEAT, S3 prefix-scoped.
    //
    // This is the boundary that makes seats mean anything. A single shared
    // role would make every agent a peer of every other on the drive: a
    // prompt injection against one seat would reach another seat's
    // credentials and files. See docs/architecture/01, boundary B5.
    //
    // For V1 the agent's stable ID is the seat key, so the prefix can be
    // pinned at synth time rather than depending on a principal tag.
    // ---------------------------------------------------------------------
    const executionRoles: Record<string, iam.Role> = {};

    for (const seat of props.seats) {
      const role = new iam.Role(this, `ExecRole${pascal(seat.key)}`, {
        roleName: `amazai-agent-${seat.key}`,
        assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com', {
          conditions: { StringEquals: { 'aws:SourceAccount': this.account } },
        }),
        description: `AmazAI harness execution role for the ${seat.name} seat`,
      });

      // Its own drive prefix, and the shared prefix. Nothing else.
      role.addToPolicy(new iam.PolicyStatement({
        sid: 'OwnWorkspacePrefix',
        actions: ['s3:GetObject', 's3:PutObject', 's3:DeleteObject', 's3:GetObjectVersion'],
        resources: [
          `${driveBucket.bucketArn}/agents/${seat.key}/*`,
          `${driveBucket.bucketArn}/shared/*`,
        ],
      }));
      role.addToPolicy(new iam.PolicyStatement({
        sid: 'ListOwnWorkspacePrefix',
        actions: ['s3:ListBucket'],
        resources: [driveBucket.bucketArn],
        conditions: {
          StringLike: { 's3:prefix': [`agents/${seat.key}/*`, 'shared/*'] },
        },
      }));
      // Write-only into its own evidence artifacts. An agent can produce
      // evidence; it cannot read or rewrite the record afterwards.
      role.addToPolicy(new iam.PolicyStatement({
        sid: 'WriteOwnEvidence',
        actions: ['s3:PutObject'],
        resources: [`${evidenceBucket.bucketArn}/evidence/*`],
      }));
      role.addToPolicy(new iam.PolicyStatement({
        sid: 'InvokeModels',
        actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
        resources: ['*'],
      }));
      role.addToPolicy(new iam.PolicyStatement({
        sid: 'OwnLogGroupOnly',
        actions: ['logs:CreateLogStream', 'logs:PutLogEvents'],
        resources: [
          `arn:aws:logs:${this.region}:${this.account}:log-group:/amazai/agent/${seat.key}:*`,
        ],
      }));
      key.grantEncryptDecrypt(role);

      new logs.LogGroup(this, `AgentLogs${pascal(seat.key)}`, {
        logGroupName: `/amazai/agent/${seat.key}`,
        retention: logs.RetentionDays.THREE_MONTHS,
        removalPolicy: cdk.RemovalPolicy.RETAIN,
      });

      executionRoles[seat.key] = role;
    }

    // ---------------------------------------------------------------------
    // L1/L2 · Compute
    // ---------------------------------------------------------------------
    // Lambda's bundled boto3 lags the AgentCore harness APIs. Build the layer
    // with scripts/build_layer.sh before deploying, or create_harness fails
    // with an unhelpful ParamValidationError.
    const boto3Layer = new lambda.LayerVersion(this, 'Boto3Layer', {
      layerVersionName: 'amazai-boto3',
      code: lambda.Code.fromAsset(path.join(REPO_ROOT, 'layer')),
      compatibleRuntimes: [lambda.Runtime.PYTHON_3_12],
      compatibleArchitectures: [lambda.Architecture.ARM_64],
      description: 'Current boto3/botocore for the AgentCore harness APIs',
    });

    const servicesCode = lambda.Code.fromAsset(path.join(REPO_ROOT, 'services'));

    const commonEnv: Record<string, string> = {
      TABLE_NAME: table.tableName,
      DRIVE_BUCKET: driveBucket.bucketName,
      EVIDENCE_BUCKET: evidenceBucket.bucketName,
      KMS_KEY_ID: key.keyId,
      USER_POOL_ID: userPool.userPoolId,
      POWERTOOLS_SERVICE_NAME: 'amazai',
    };

    const makeFn = (
      name: string,
      handler: string,
      timeout: cdk.Duration,
      memory = 512,
    ): lambda.Function => {
      const fnName = `amazai-${handler.split('.')[1]}`;
      return new lambda.Function(this, name, {
        functionName: fnName,
        runtime: lambda.Runtime.PYTHON_3_12,
        architecture: lambda.Architecture.ARM_64,
        code: servicesCode,
        handler,
        layers: [boto3Layer],
        timeout,
        memorySize: memory,
        environment: commonEnv,
        logGroup: new logs.LogGroup(this, `${name}Logs`, {
          logGroupName: `/aws/lambda/${fnName}`,
          retention: logs.RetentionDays.THREE_MONTHS,
          removalPolicy: cdk.RemovalPolicy.DESTROY,
        }),
      });
    };

    const apiFn = makeFn('ApiFn', 'handlers.api.handler', cdk.Duration.seconds(30));
    const wsFn = makeFn('WsFn', 'handlers.ws.handler', cdk.Duration.seconds(30));
    const orchestratorFn = makeFn('OrchestratorFn', 'handlers.orchestrator.handler', cdk.Duration.minutes(15), 1024);
    const routineFn = makeFn('RoutineFn', 'handlers.routine.handler', cdk.Duration.minutes(15), 1024);
    const sweeperFn = makeFn('SweeperFn', 'handlers.sweeper.handler', cdk.Duration.minutes(5));

    const allFns = [apiFn, wsFn, orchestratorFn, routineFn, sweeperFn];
    for (const fn of allFns) {
      table.grantReadWriteData(fn);
      key.grantEncryptDecrypt(fn);
    }
    for (const fn of [apiFn, orchestratorFn, routineFn, sweeperFn]) {
      driveBucket.grantReadWrite(fn);
      evidenceBucket.grantReadWrite(fn);
    }

    // Only the orchestrator and routine workers talk to AgentCore.
    for (const fn of [apiFn, orchestratorFn, routineFn]) {
      fn.addToRolePolicy(new iam.PolicyStatement({
        sid: 'AgentCore',
        actions: [
          'bedrock-agentcore:InvokeHarness',
          'bedrock-agentcore:InvokeAgentRuntimeCommand',
          'bedrock-agentcore:GetHarness',
        ],
        resources: ['*'],
      }));
    }

    // The orchestrator may hand a seat its own execution role, and nothing else.
    orchestratorFn.addToRolePolicy(new iam.PolicyStatement({
      sid: 'PassSeatExecutionRolesOnly',
      actions: ['iam:PassRole'],
      resources: Object.values(executionRoles).map((r) => r.roleArn),
      conditions: {
        StringEquals: { 'iam:PassedToService': 'bedrock-agentcore.amazonaws.com' },
      },
    }));

    apiFn.grantInvoke(wsFn);
    orchestratorFn.grantInvoke(apiFn);
    orchestratorFn.grantInvoke(wsFn);
    orchestratorFn.grantInvoke(routineFn);
    orchestratorFn.grantInvoke(sweeperFn);
    routineFn.grantInvoke(apiFn);

    // ---------------------------------------------------------------------
    // L1 · HTTP API — Cognito JWT on every route
    // ---------------------------------------------------------------------
    const httpApi = new apigwv2.HttpApi(this, 'HttpApi', {
      apiName: 'amazai',
      corsPreflight: {
        allowHeaders: ['authorization', 'content-type'],
        allowMethods: [apigwv2.CorsHttpMethod.ANY],
        allowOrigins: ['*'], // tighten to the console origin once DNS is settled
        maxAge: cdk.Duration.hours(1),
      },
    });

    httpApi.addRoutes({
      path: '/{proxy+}',
      methods: [apigwv2.HttpMethod.ANY],
      integration: new apigwv2int.HttpLambdaIntegration('ApiInt', apiFn),
      authorizer: new apigwv2auth.HttpUserPoolAuthorizer('JwtAuth', userPool, {
        userPoolClients: [userPoolClient],
      }),
    });

    // ---------------------------------------------------------------------
    // L1 · WebSocket — streaming deltas, tool chips, approval prompts
    // ---------------------------------------------------------------------
    const wsApi = new apigwv2.WebSocketApi(this, 'WsApi', {
      apiName: 'amazai-ws',
      connectRouteOptions: { integration: new apigwv2int.WebSocketLambdaIntegration('WsConnect', wsFn) },
      disconnectRouteOptions: { integration: new apigwv2int.WebSocketLambdaIntegration('WsDisconnect', wsFn) },
      defaultRouteOptions: { integration: new apigwv2int.WebSocketLambdaIntegration('WsDefault', wsFn) },
    });

    const wsStage = new apigwv2.WebSocketStage(this, 'WsStage', {
      webSocketApi: wsApi,
      stageName: 'live',
      autoDeploy: true,
    });

    // Anything that reports progress needs post_to_connection.
    for (const fn of [wsFn, orchestratorFn, routineFn, sweeperFn]) {
      wsStage.grantManagementApiAccess(fn);
      fn.addEnvironment('WS_ENDPOINT', wsStage.callbackUrl);
    }

    // ---------------------------------------------------------------------
    // L2 · Sweeper — stale-heartbeat recovery and approval expiry
    // ---------------------------------------------------------------------
    new events.Rule(this, 'SweeperSchedule', {
      ruleName: 'amazai-sweeper',
      description: 'Recover stalled runs; expire pending approvals to denied',
      schedule: events.Schedule.rate(cdk.Duration.minutes(5)),
      targets: [new targets.LambdaFunction(sweeperFn)],
    });

    // ---------------------------------------------------------------------
    // L2 · Routines — EventBridge Scheduler, one schedule per routine
    // ---------------------------------------------------------------------
    const schedulerRole = new iam.Role(this, 'SchedulerRole', {
      roleName: 'amazai-scheduler',
      assumedBy: new iam.ServicePrincipal('scheduler.amazonaws.com', {
        conditions: { StringEquals: { 'aws:SourceAccount': this.account } },
      }),
      description: 'Assumed by EventBridge Scheduler to fire routine runs',
    });
    routineFn.grantInvoke(schedulerRole);

    apiFn.addToRolePolicy(new iam.PolicyStatement({
      sid: 'ManageRoutineSchedules',
      actions: [
        'scheduler:CreateSchedule',
        'scheduler:UpdateSchedule',
        'scheduler:DeleteSchedule',
        'scheduler:GetSchedule',
        'scheduler:ListSchedules',
      ],
      resources: [`arn:aws:scheduler:${this.region}:${this.account}:schedule/amazai/*`],
    }));
    apiFn.addToRolePolicy(new iam.PolicyStatement({
      sid: 'PassSchedulerRole',
      actions: ['iam:PassRole'],
      resources: [schedulerRole.roleArn],
      conditions: {
        StringEquals: { 'iam:PassedToService': 'scheduler.amazonaws.com' },
      },
    }));
    apiFn.addEnvironment('SCHEDULER_ROLE_ARN', schedulerRole.roleArn);
    apiFn.addEnvironment('ROUTINE_FN_ARN', routineFn.functionArn);
    apiFn.addEnvironment('ORCHESTRATOR_FN_ARN', orchestratorFn.functionArn);
    wsFn.addEnvironment('ORCHESTRATOR_FN_ARN', orchestratorFn.functionArn);
    routineFn.addEnvironment('ORCHESTRATOR_FN_ARN', orchestratorFn.functionArn);
    sweeperFn.addEnvironment('ORCHESTRATOR_FN_ARN', orchestratorFn.functionArn);

    // ---------------------------------------------------------------------
    // L1 · Console delivery — SPA needs 403/404 -> index.html to survive a refresh
    // ---------------------------------------------------------------------
    const distribution = new cloudfront.Distribution(this, 'ConsoleDistribution', {
      comment: 'AmazAI console',
      defaultRootObject: 'index.html',
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(consoleBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
      },
      errorResponses: [
        { httpStatus: 403, responseHttpStatus: 200, responsePagePath: '/index.html', ttl: cdk.Duration.minutes(5) },
        { httpStatus: 404, responseHttpStatus: 200, responsePagePath: '/index.html', ttl: cdk.Duration.minutes(5) },
      ],
    });

    // Deploys web/dist when it exists; harmless placeholder before the console is built.
    new s3deploy.BucketDeployment(this, 'ConsoleDeployment', {
      sources: [s3deploy.Source.data('index.html', PLACEHOLDER_HTML)],
      destinationBucket: consoleBucket,
      distribution,
      distributionPaths: ['/*'],
      prune: false,
    });

    // ---------------------------------------------------------------------
    // Outputs — everything provision_agents.py and web/.env need
    // ---------------------------------------------------------------------
    const out = (id: string, value: string, description: string) =>
      new cdk.CfnOutput(this, `${id}Output`, { value, description });

    out('ConsoleUrl', `https://${distribution.distributionDomainName}`, 'Console URL');
    out('ApiUrl', httpApi.apiEndpoint, 'HTTP API base URL');
    out('WsUrl', wsStage.url, 'WebSocket URL');
    out('UserPoolId', userPool.userPoolId, 'Cognito user pool ID');
    out('UserPoolClientId', userPoolClient.userPoolClientId, 'Cognito app client ID');
    out('TableName', table.tableName, 'DynamoDB table');
    out('DriveBucket', driveBucket.bucketName, 'Workspace drive bucket');
    out('EvidenceBucket', evidenceBucket.bucketName, 'Evidence bucket');
    out('KmsKeyArn', key.keyArn, 'Customer-managed key');
    out('OwnerEmail', props.ownerEmail, 'Account owner (create this Cognito user)');

    for (const seat of props.seats) {
      out(`ExecRoleArn${pascal(seat.key)}`, executionRoles[seat.key]!.roleArn,
        `Harness execution role for the ${seat.name} seat`);
    }
  }
}

function pascal(s: string): string {
  return s.replace(/(^|[-_])(\w)/g, (_m, _p1, c: string) => c.toUpperCase());
}

const PLACEHOLDER_HTML = `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AmazAI</title>
<style>
  :root { color-scheme: light dark; }
  body { margin:0; min-height:100vh; display:grid; place-items:center;
         font:16px/1.6 ui-sans-serif,system-ui,sans-serif; background:#0b0d10; color:#e6e8eb; }
  main { max-width:32rem; padding:2rem; text-align:center; }
  code { background:#1a1d22; padding:.15em .4em; border-radius:4px; font-size:.9em; }
</style></head>
<body><main>
  <h1>AmazAI</h1>
  <p>Control plane deployed. The console has not been built yet.</p>
  <p><code>cd web &amp;&amp; npm run build &amp;&amp; cd ../infra &amp;&amp; npx cdk deploy</code></p>
</main></body></html>
`;
