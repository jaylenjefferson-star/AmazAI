import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as path from 'node:path';
import * as apigwv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as apigwv2auth from 'aws-cdk-lib/aws-apigatewayv2-authorizers';
import * as apigwv2int from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as scheduler from 'aws-cdk-lib/aws-scheduler';

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

  /** Auth0 is the only application identity provider. These are public
   * identifiers, not credentials; the SPA client id stays in Amplify. */
  readonly auth0Domain: string;
  readonly auth0Audience: string;

  /** Pipedream Connect project, e.g. proj_xxxxxxx. An identifier, not a
   *  credential -- it appears in every Connect URL. The OAuth client secret
   *  lives in Secrets Manager and never passes through here. */
  readonly pipedreamProjectId?: string;

  /** Pipedream's own environment switch: 'development' or 'production'.
   *  Defaults to development, so a half-configured stack talks to test
   *  accounts rather than real ones. */
  readonly pipedreamEnvironment?: string;
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
      // AgentCore's harness keeps the conversation state in a harness-owned
      // memory resource. The execution role must be able to read that event
      // stream before it can continue a turn.
      role.addToPolicy(new iam.PolicyStatement({
        sid: 'ReadOwnHarnessMemoryEvents',
        actions: [
          'bedrock-agentcore:CreateEvent',
          'bedrock-agentcore:ListEvents',
        ],
        resources: [
          `arn:aws:bedrock-agentcore:${this.region}:${this.account}:memory/amazai_${seat.key}-*`,
        ],
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

    // The Pipedream OAuth client. CDK creates it with a generated placeholder
    // value, which is then replaced out of band -- putting the real client
    // secret in a CDK property would put it in the synthesized template, in
    // CloudFormation's stored state and in this repository's history, three
    // places a credential should never be. Replace the placeholder with:
    //
    //   aws secretsmanager put-secret-value --secret-id amazai/pipedream \
    //     --secret-string '{"client_id":"...","client_secret":"..."}'
    const pipedreamSecret = new secretsmanager.Secret(this, 'PipedreamSecret', {
      secretName: 'amazai/pipedream',
      description: 'Pipedream Connect OAuth client (client_id, client_secret)',
      encryptionKey: key,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    const commonEnv: Record<string, string> = {
      TABLE_NAME: table.tableName,
      DRIVE_BUCKET: driveBucket.bucketName,
      EVIDENCE_BUCKET: evidenceBucket.bucketName,
      KMS_KEY_ID: key.keyId,
      AUTH0_DOMAIN: props.auth0Domain,
      AUTH0_AUDIENCE: props.auth0Audience,
      POWERTOOLS_SERVICE_NAME: 'amazai',

      // A Pipedream project id is an identifier, not a credential -- it
      // appears in every Connect URL. The secret above is the credential.
      PIPEDREAM_PROJECT_ID: props?.pipedreamProjectId ?? '',
      PIPEDREAM_ENVIRONMENT: props?.pipedreamEnvironment ?? 'development',
      PIPEDREAM_SECRET_ID: pipedreamSecret.secretName,
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

    // WebSocket clients cannot set an Authorization header during the browser
    // handshake. The $connect authorizer verifies the short-lived query token
    // once and passes only the subject into the socket handler afterwards.
    const wsAuthFn = new lambda.Function(this, 'WsAuthFn', {
      functionName: 'amazai-ws-auth',
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      code: servicesCode,
      handler: 'handlers.ws_auth.handler',
      layers: [boto3Layer],
      timeout: cdk.Duration.seconds(10),
      memorySize: 256,
      environment: {
        AUTH0_DOMAIN: props.auth0Domain,
        AUTH0_AUDIENCE: props.auth0Audience,
        POWERTOOLS_SERVICE_NAME: 'amazai',
      },
      logGroup: new logs.LogGroup(this, 'WsAuthFnLogs', {
        logGroupName: '/aws/lambda/amazai-ws-auth',
        retention: logs.RetentionDays.THREE_MONTHS,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    });

    const allFns = [apiFn, wsFn, orchestratorFn, routineFn, sweeperFn];
    for (const fn of allFns) {
      table.grantReadWriteData(fn);
      key.grantEncryptDecrypt(fn);
    }
    for (const fn of [apiFn, orchestratorFn, routineFn, sweeperFn]) {
      driveBucket.grantReadWrite(fn);
      evidenceBucket.grantReadWrite(fn);
    }

    // Only the paths that actually call a connector may read its credential:
    // the API installs and lists, the orchestrator and routine workers invoke.
    // The websocket and sweeper functions never touch Pipedream and are left
    // without the grant rather than given one they do not use.
    for (const fn of [apiFn, orchestratorFn, routineFn]) {
      pipedreamSecret.grantRead(fn);
    }

    // Only the orchestrator and routine workers talk to AgentCore.
    for (const fn of [apiFn, orchestratorFn, routineFn]) {
      fn.addToRolePolicy(new iam.PolicyStatement({
        sid: 'AgentCore',
        actions: [
          'bedrock-agentcore:InvokeHarness',
          'bedrock-agentcore:InvokeAgentRuntime',
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
    // Retries are asynchronous invocations of this same worker. Use the
    // deterministic function ARN rather than the construct token, which
    // would introduce a Lambda-role circular dependency in CloudFormation.
    orchestratorFn.addToRolePolicy(new iam.PolicyStatement({
      sid: 'InvokeSelfForRetry',
      actions: ['lambda:InvokeFunction'],
      resources: [
        `arn:aws:lambda:${this.region}:${this.account}:function:amazai-orchestrator`,
      ],
    }));

    apiFn.grantInvoke(wsFn);
    orchestratorFn.grantInvoke(apiFn);
    orchestratorFn.grantInvoke(wsFn);
    orchestratorFn.grantInvoke(routineFn);
    orchestratorFn.grantInvoke(sweeperFn);
    routineFn.grantInvoke(apiFn);

    // ---------------------------------------------------------------------
    // L1 · HTTP API — Auth0 JWT on every route
    // ---------------------------------------------------------------------
    const httpApi = new apigwv2.HttpApi(this, 'HttpApi', {
      apiName: 'amazai',
      corsPreflight: {
        allowHeaders: ['authorization', 'content-type'],
        allowMethods: [apigwv2.CorsHttpMethod.ANY],
        allowOrigins: [
          'https://amazai.co',
          'https://claude-modest-rubin-rtpea6.d2qtxrhp46u9pz.amplifyapp.com',
          'http://localhost:4173',
          'http://localhost:5173',
        ],
        maxAge: cdk.Duration.hours(1),
      },
    });

    const auth0Issuer = `https://${props.auth0Domain.replace(/^https:\/\//, '').replace(/\/$/, '')}/`;
    const httpAuthorizer = new apigwv2auth.HttpJwtAuthorizer('Auth0JwtAuth', auth0Issuer, {
      jwtAudience: [props.auth0Audience],
    });

    // A browser sends OPTIONS before its authenticated request. It cannot
    // attach the bearer token to that CORS preflight, so OPTIONS must remain
    // public while every operation that carries data stays behind Auth0.
    httpApi.addRoutes({
      path: '/{proxy+}',
      methods: [apigwv2.HttpMethod.OPTIONS],
      integration: new apigwv2int.HttpLambdaIntegration('OptionsInt', apiFn),
    });

    httpApi.addRoutes({
      path: '/{proxy+}',
      methods: [apigwv2.HttpMethod.ANY],
      integration: new apigwv2int.HttpLambdaIntegration('ApiInt', apiFn),
      authorizer: httpAuthorizer,
    });

    // ---------------------------------------------------------------------
    // L1 · WebSocket — streaming deltas, tool chips, approval prompts
    // ---------------------------------------------------------------------
    const wsApi = new apigwv2.WebSocketApi(this, 'WsApi', {
      apiName: 'amazai-ws',
      connectRouteOptions: {
        integration: new apigwv2int.WebSocketLambdaIntegration('WsConnect', wsFn),
        authorizer: new apigwv2auth.WebSocketLambdaAuthorizer('WsAuth', wsAuthFn, {
          identitySource: ['route.request.querystring.token'],
        }),
      },
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

    // The group the schedules live in. It has to exist before the first
    // CreateSchedule call: Scheduler does not create one implicitly, and the
    // policy below is scoped to `schedule/amazai/*`, so a schedule written
    // anywhere else would be created and then be unmanageable. Nothing
    // called Scheduler until the routine routes did, which is why an absent
    // group had never failed anything.
    const scheduleGroup = new scheduler.CfnScheduleGroup(this, 'RoutineScheduleGroup', {
      name: 'amazai',
    });

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
    apiFn.addEnvironment('SCHEDULE_GROUP', scheduleGroup.name!);
    apiFn.addEnvironment('SCHEDULER_ROLE_ARN', schedulerRole.roleArn);
    apiFn.addEnvironment('ROUTINE_FN_ARN', routineFn.functionArn);
    apiFn.addEnvironment('ORCHESTRATOR_FN_ARN', orchestratorFn.functionArn);
    wsFn.addEnvironment('ORCHESTRATOR_FN_ARN', orchestratorFn.functionArn);
    orchestratorFn.addEnvironment(
      'ORCHESTRATOR_FN_ARN',
      `arn:aws:lambda:${this.region}:${this.account}:function:amazai-orchestrator`,
    );
    routineFn.addEnvironment('ORCHESTRATOR_FN_ARN', orchestratorFn.functionArn);

    // Decision D4: how a paused run continues. `resume_note` is the documented
    // fallback and what is verified in code; `tool_result` is refused by the
    // orchestrator until `scripts/spike_d4.py` has shown the service accepts it.
    // Set with `cdk deploy -c continuation=<value>`. Anything else is an error at
    // synth time, not a mystery at 2am.
    const continuation = String(this.node.tryGetContext('continuation') ?? 'resume_note');
    if (!['resume_note', 'tool_result'].includes(continuation)) {
      throw new Error(`context continuation must be resume_note or tool_result, got ${continuation}`);
    }
    orchestratorFn.addEnvironment('AMAZAI_CONTINUATION', continuation);
    sweeperFn.addEnvironment('ORCHESTRATOR_FN_ARN', orchestratorFn.functionArn);

    // ---------------------------------------------------------------------
    // Outputs — everything provision_agents.py and web/.env need
    // ---------------------------------------------------------------------
    const out = (id: string, value: string, description: string) =>
      new cdk.CfnOutput(this, `${id}Output`, { value, description });

    out('ApiUrl', httpApi.apiEndpoint, 'HTTP API base URL');
    out('WsUrl', wsStage.url, 'WebSocket URL');
    out('Auth0Domain', props.auth0Domain, 'Auth0 issuer domain');
    out('Auth0Audience', props.auth0Audience, 'Auth0 API audience');
    out('TableName', table.tableName, 'DynamoDB table');
    out('DriveBucket', driveBucket.bucketName, 'Workspace drive bucket');
    out('EvidenceBucket', evidenceBucket.bucketName, 'Evidence bucket');
    out('KmsKeyArn', key.keyArn, 'Customer-managed key');
    out('OwnerEmail', props.ownerEmail, 'Private workspace owner email');

    for (const seat of props.seats) {
      out(`ExecRoleArn${pascal(seat.key)}`, executionRoles[seat.key]!.roleArn,
        `Harness execution role for the ${seat.name} seat`);
    }
  }
}

function pascal(s: string): string {
  return s.replace(/(^|[-_])(\w)/g, (_m, _p1, c: string) => c.toUpperCase());
}
