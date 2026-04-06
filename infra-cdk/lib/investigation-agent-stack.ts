import * as cdk from "aws-cdk-lib"
import * as iam from "aws-cdk-lib/aws-iam"
import * as ssm from "aws-cdk-lib/aws-ssm"
import * as ecr_assets from "aws-cdk-lib/aws-ecr-assets"
import * as agentcore from "@aws-cdk/aws-bedrock-agentcore-alpha"
import { Construct } from "constructs"
import { AgentCoreRole } from "./utils/agentcore-role"
import * as path from "path"

export interface InvestigationAgentStackProps extends cdk.NestedStackProps {
  stackName: string
  memoryId: string
  memoryArn: string
  userPoolId: string
  machineClientId: string
}

export class InvestigationAgentStack extends cdk.NestedStack {
  public readonly invocationsUrl: string
  public readonly runtimeArn: string

  constructor(scope: Construct, id: string, props: InvestigationAgentStackProps) {
    super(scope, id, props)

    const stack = cdk.Stack.of(this)
    const region = stack.region

    // Cognito discovery URL — same user pool as the main agent
    const cognitoDiscoveryUrl = `https://cognito-idp.${region}.amazonaws.com/${props.userPoolId}/.well-known/openid-configuration`

    // JWT authorizer: accept tokens issued to the machine client (M2M flow from orchestrator)
    const authorizerConfiguration = agentcore.RuntimeAuthorizerConfiguration.usingJWT(
      cognitoDiscoveryUrl,
      [props.machineClientId]
    )

    // IAM execution role for the investigation agent runtime
    const agentRole = new AgentCoreRole(this, "InvestigationAgentRole")

    // Memory access — same memory resource as the main agent
    agentRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "MemoryResourceAccess",
        effect: iam.Effect.ALLOW,
        actions: [
          "bedrock-agentcore:CreateEvent",
          "bedrock-agentcore:GetEvent",
          "bedrock-agentcore:ListEvents",
          "bedrock-agentcore:RetrieveMemoryRecords",
        ],
        resources: [props.memoryArn],
      })
    )

    // SSM read access — for gateway URL and investigation agent runtime ARN at startup
    agentRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "SSMParameterAccess",
        effect: iam.Effect.ALLOW,
        actions: ["ssm:GetParameter", "ssm:GetParameters"],
        resources: [
          `arn:aws:ssm:${region}:${stack.account}:parameter/${props.stackName}/*`,
        ],
      })
    )

    // AgentCore Identity access — for @requires_access_token to call the Gateway
    agentRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "AgentCoreIdentityM2MAccess",
        effect: iam.Effect.ALLOW,
        actions: [
          "bedrock-agentcore:GetWorkloadAccessToken",
          "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
          "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
          "bedrock-agentcore:GetOauth2CredentialProvider",
          "bedrock-agentcore:GetResourceOauth2Token",
        ],
        resources: [
          `arn:aws:bedrock-agentcore:${region}:${stack.account}:oauth2-credential-provider/*`,
          `arn:aws:bedrock-agentcore:${region}:${stack.account}:token-vault/*`,
          `arn:aws:bedrock-agentcore:${region}:${stack.account}:workload-identity-directory/*`,
        ],
      })
    )

    // Secrets Manager — machine client secret for the @requires_access_token decorator
    agentRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "SecretsManagerAccess",
        effect: iam.Effect.ALLOW,
        actions: ["secretsmanager:GetSecretValue"],
        resources: [
          `arn:aws:secretsmanager:${region}:${stack.account}:secret:/${props.stackName}/machine_client_secret*`,
          `arn:aws:secretsmanager:${region}:${stack.account}:secret:bedrock-agentcore-identity!default/oauth2/${props.stackName}-runtime-gateway-auth*`,
        ],
      })
    )

    // Build the Docker image from repo root using the investigation agent Dockerfile
    const image = agentcore.AgentRuntimeArtifact.fromAsset(
      path.resolve(__dirname, "..", ".."), // repo root
      {
        platform: ecr_assets.Platform.LINUX_ARM64,
        file: "patterns/investigation-agent/Dockerfile",
      }
    )

    // AgentCore Runtime — HTTP protocol (A2A server inside the container)
    const runtime = new agentcore.Runtime(this, "InvestigationAgentRuntime", {
      runtimeName: `${props.stackName.replace(/-/g, "_")}_investigation_agent`,
      agentRuntimeArtifact: image,
      executionRole: agentRole,
      networkConfiguration: agentcore.RuntimeNetworkConfiguration.usingPublicNetwork(),
      protocolConfiguration: agentcore.ProtocolType.HTTP,
      authorizerConfiguration: authorizerConfiguration,
      environmentVariables: {
        STACK_NAME: props.stackName,
        MEMORY_ID: props.memoryId,
        AWS_DEFAULT_REGION: region,
        // GATEWAY_CREDENTIAL_PROVIDER_NAME: reuse the same provider as the main agent
        // (same machine client, same Cognito user pool)
        GATEWAY_CREDENTIAL_PROVIDER_NAME: `${props.stackName}-runtime-gateway-auth`,
      },
      description: "Investigation agent — deep GuardDuty + CloudTrail incident analysis",
    })

    this.runtimeArn = runtime.agentRuntimeArn

    // Invocations URL for the orchestrator to use as INVESTIGATION_AGENT_URL
    this.invocationsUrl = `https://bedrock-agentcore.${region}.amazonaws.com/runtimes/${runtime.agentRuntimeArn}/invocations`

    // Store runtime ARN in SSM so main.py can construct the A2A server URL at startup
    // (avoids the circular dep of passing the URL as an env var during CDK deploy)
    new ssm.StringParameter(this, "InvestigationAgentRuntimeArnParam", {
      parameterName: `/${props.stackName}/investigation-agent-runtime-arn`,
      stringValue: runtime.agentRuntimeArn,
      description: "Investigation agent runtime ARN — read by main.py to construct A2A server URL",
    })

    // Store invocations URL in SSM so the orchestrator can discover it at runtime
    new ssm.StringParameter(this, "InvestigationAgentUrlParam", {
      parameterName: `/${props.stackName}/investigation-agent-url`,
      stringValue: this.invocationsUrl,
      description: "Investigation agent A2A invocations URL — used by orchestrator",
    })

    new cdk.CfnOutput(this, "InvestigationAgentRuntimeArn", {
      description: "Investigation agent runtime ARN",
      value: runtime.agentRuntimeArn,
    })

    new cdk.CfnOutput(this, "InvestigationAgentUrl", {
      description: "Investigation agent A2A invocations URL",
      value: this.invocationsUrl,
    })
  }
}
