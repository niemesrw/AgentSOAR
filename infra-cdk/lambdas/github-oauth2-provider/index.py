"""
Custom Resource Lambda for managing GitHub OAuth2 Credential Provider lifecycle.

This Lambda is invoked by CloudFormation during stack deployment to create a
GithubOauth2 credential provider in Bedrock AgentCore Identity. GitHub is a
prebuilt vendor — no discovery URL needed, just the OAuth App credentials.

CloudFormation Events:
- Create: Creates GithubOauth2 provider with credentials from Secrets Manager
- Update: Updates provider credentials
- Delete: Deletes the provider

After deploy, the stack outputs a CallbackUrl. Register this in your GitHub OAuth App
at: Settings → Developer settings → OAuth Apps → <your app> → Authorization callback URL
"""

import json
import logging

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

bedrock_client = boto3.client("bedrock-agentcore-control")
secrets_client = boto3.client("secretsmanager")
sts_client = boto3.client("sts")


def handler(event: dict, context: dict) -> dict:
    """CloudFormation Custom Resource handler for GitHub OAuth2 Credential Provider."""
    request_type = event["RequestType"]
    props = event["ResourceProperties"]

    logger.info(
        "Request type: %s, Provider name: %s", request_type, props["ProviderName"]
    )

    try:
        if request_type == "Create":
            return handle_create(props)
        elif request_type == "Update":
            return handle_update(event, props)
        elif request_type == "Delete":
            return handle_delete(event, props)
        else:
            raise ValueError(f"Unknown request type: {request_type}")
    except Exception as e:
        logger.error("Error handling %s: %s", request_type, str(e), exc_info=True)
        raise


def _build_provider_arn(provider_name: str) -> str:
    """Construct the provider ARN from its name.

    The API does not consistently return credentialProviderArn in create/update
    responses, so we build it from the known ARN format.
    """
    account = sts_client.get_caller_identity()["Account"]
    region = bedrock_client.meta.region_name
    return f"arn:aws:bedrock-agentcore:{region}:{account}:token-vault/default/oauth2credentialprovider/{provider_name}"


def _get_credentials(secret_arn: str) -> tuple[str, str]:
    """Retrieve GitHub OAuth App client_id and client_secret from Secrets Manager.

    The secret must be a JSON string: {"clientId": "...", "clientSecret": "..."}
    """
    response = secrets_client.get_secret_value(SecretId=secret_arn)
    creds = json.loads(response["SecretString"])
    return creds["clientId"], creds["clientSecret"]


def handle_create(props: dict) -> dict:
    client_id, client_secret = _get_credentials(props["SecretArn"])

    logger.info("Creating GithubOauth2 credential provider: %s", props["ProviderName"])
    try:
        response = bedrock_client.create_oauth2_credential_provider(
            name=props["ProviderName"],
            credentialProviderVendor="GithubOauth2",
            oauth2ProviderConfigInput={
                "githubOauth2ProviderConfig": {
                    "clientId": client_id,
                    "clientSecret": client_secret,
                }
            },
        )
    except Exception as e:
        if "already exists" not in str(e):
            raise
        logger.warning("Provider already exists, updating instead: %s", props["ProviderName"])
        response = bedrock_client.update_oauth2_credential_provider(
            name=props["ProviderName"],
            credentialProviderVendor="GithubOauth2",
            oauth2ProviderConfigInput={
                "githubOauth2ProviderConfig": {
                    "clientId": client_id,
                    "clientSecret": client_secret,
                }
            },
        )

    logger.info("API response keys: %s", list(response.keys()))
    provider_arn = response.get("credentialProviderArn") or _build_provider_arn(props["ProviderName"])
    callback_url = response.get("callbackUrl", "")
    logger.info("Created/updated provider ARN: %s, CallbackUrl: %s", provider_arn, callback_url)

    return {
        "PhysicalResourceId": props["ProviderName"],
        "Data": {
            "ProviderArn": provider_arn,
            # Register this URL as the Authorization callback URL in your GitHub OAuth App
            "CallbackUrl": callback_url,
        },
    }


def handle_update(event: dict, props: dict) -> dict:
    provider_name = event["PhysicalResourceId"]
    client_id, client_secret = _get_credentials(props["SecretArn"])

    logger.info("Updating GithubOauth2 credential provider: %s", provider_name)
    response = bedrock_client.update_oauth2_credential_provider(
        name=provider_name,
        credentialProviderVendor="GithubOauth2",
        oauth2ProviderConfigInput={
            "githubOauth2ProviderConfig": {
                "clientId": client_id,
                "clientSecret": client_secret,
            }
        },
    )

    logger.info("API response keys: %s", list(response.keys()))
    provider_arn = response.get("credentialProviderArn") or _build_provider_arn(provider_name)
    callback_url = response.get("callbackUrl", "")
    logger.info("Updated provider ARN: %s", provider_arn)

    return {
        "PhysicalResourceId": provider_name,
        "Data": {
            "ProviderArn": provider_arn,
            "CallbackUrl": callback_url,
        },
    }


def handle_delete(event: dict, props: dict) -> dict:
    provider_name = event["PhysicalResourceId"]
    logger.info("Deleting GithubOauth2 credential provider: %s", provider_name)

    try:
        bedrock_client.delete_oauth2_credential_provider(name=provider_name)
        logger.info("Deleted provider: %s", provider_name)
    except bedrock_client.exceptions.ResourceNotFoundException:
        logger.warning("Provider not found (already deleted): %s", provider_name)
    except Exception as e:
        logger.error("Error deleting provider: %s", str(e))
        raise

    return {"PhysicalResourceId": provider_name}
