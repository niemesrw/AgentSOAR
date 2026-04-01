# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for infra-cdk/lambdas/github-oauth2-provider/index.py"""

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "infra-cdk/lambdas/github-oauth2-provider")

# Patch boto3 clients before importing the module
with (
    patch("boto3.client") as mock_boto3_client,
):
    mock_bedrock = MagicMock()
    mock_secrets = MagicMock()
    mock_sts = MagicMock()

    def _client_factory(service, **kwargs):
        if service == "bedrock-agentcore-control":
            return mock_bedrock
        if service == "secretsmanager":
            return mock_secrets
        if service == "sts":
            return mock_sts
        return MagicMock()

    mock_boto3_client.side_effect = _client_factory
    import index as provider


# Reset mocks before each test (side_effect=True, return_value=True clears those too)
@pytest.fixture(autouse=True)
def reset_mocks():
    mock_bedrock.reset_mock(side_effect=True, return_value=True)
    mock_secrets.reset_mock(side_effect=True, return_value=True)
    mock_sts.reset_mock(side_effect=True, return_value=True)
    mock_sts.get_caller_identity.return_value = {"Account": "123456789012"}
    mock_bedrock.meta.region_name = "us-east-1"
    mock_secrets.get_secret_value.return_value = {
        "SecretString": json.dumps({"clientId": "cid", "clientSecret": "csec"})
    }


# ---------------------------------------------------------------------------
# _build_provider_arn
# ---------------------------------------------------------------------------

class TestBuildProviderArn:
    def test_constructs_arn_correctly(self):
        arn = provider._build_provider_arn("my-provider")
        assert arn == (
            "arn:aws:bedrock-agentcore:us-east-1:123456789012:"
            "token-vault/default/oauth2credentialprovider/my-provider"
        )


# ---------------------------------------------------------------------------
# handle_create
# ---------------------------------------------------------------------------

class TestHandleCreate:
    def test_create_success_with_arn_in_response(self):
        mock_bedrock.create_oauth2_credential_provider.return_value = {
            "credentialProviderArn": "arn:aws:bedrock-agentcore:us-east-1:123456789012:token-vault/default/oauth2credentialprovider/test-github",
            "callbackUrl": "https://callback.example.com/cb",
        }
        props = {"ProviderName": "test-github", "SecretArn": "arn:aws:secretsmanager:us-east-1:123456789012:secret:test"}
        result = provider.handle_create(props)
        assert result["PhysicalResourceId"] == "test-github"
        assert "token-vault" in result["Data"]["ProviderArn"]
        assert result["Data"]["CallbackUrl"] == "https://callback.example.com/cb"

    def test_create_falls_back_to_built_arn_when_missing(self):
        mock_bedrock.create_oauth2_credential_provider.return_value = {}
        props = {"ProviderName": "test-github", "SecretArn": "arn:aws:secretsmanager:us-east-1:123456789012:secret:test"}
        result = provider.handle_create(props)
        assert result["Data"]["ProviderArn"] == (
            "arn:aws:bedrock-agentcore:us-east-1:123456789012:"
            "token-vault/default/oauth2credentialprovider/test-github"
        )

    def test_create_already_exists_falls_through_to_update(self):
        mock_bedrock.create_oauth2_credential_provider.side_effect = Exception(
            "Credential provider with name: test-github already exists"
        )
        mock_bedrock.update_oauth2_credential_provider.return_value = {
            "credentialProviderArn": "arn:aws:bedrock-agentcore:us-east-1:123456789012:token-vault/default/oauth2credentialprovider/test-github",
        }
        mock_bedrock.get_oauth2_credential_provider.return_value = {
            "callbackUrl": "https://callback.example.com/cb"
        }
        props = {"ProviderName": "test-github", "SecretArn": "arn:aws:secretsmanager:us-east-1:123456789012:secret:test"}
        result = provider.handle_create(props)
        mock_bedrock.update_oauth2_credential_provider.assert_called_once()
        assert result["PhysicalResourceId"] == "test-github"
        assert result["Data"]["CallbackUrl"] == "https://callback.example.com/cb"

    def test_create_other_exception_re_raises(self):
        mock_bedrock.create_oauth2_credential_provider.side_effect = Exception("AccessDenied")
        props = {"ProviderName": "test-github", "SecretArn": "arn:aws:secretsmanager:us-east-1:123456789012:secret:test"}
        with pytest.raises(Exception, match="AccessDenied"):
            provider.handle_create(props)
        mock_bedrock.update_oauth2_credential_provider.assert_not_called()


# ---------------------------------------------------------------------------
# handle_update
# ---------------------------------------------------------------------------

class TestHandleUpdate:
    def test_update_success_with_arn_in_response(self):
        mock_bedrock.update_oauth2_credential_provider.return_value = {
            "credentialProviderArn": "arn:aws:bedrock-agentcore:us-east-1:123456789012:token-vault/default/oauth2credentialprovider/test-github",
            "callbackUrl": "https://callback.example.com/cb",
        }
        event = {"PhysicalResourceId": "test-github", "ResourceProperties": {}}
        props = {"SecretArn": "arn:aws:secretsmanager:us-east-1:123456789012:secret:test"}
        result = provider.handle_update(event, props)
        assert result["PhysicalResourceId"] == "test-github"
        assert "token-vault" in result["Data"]["ProviderArn"]

    def test_update_falls_back_to_built_arn_when_missing(self):
        mock_bedrock.update_oauth2_credential_provider.return_value = {}
        mock_bedrock.get_oauth2_credential_provider.return_value = {
            "callbackUrl": "https://callback.example.com/cb"
        }
        event = {"PhysicalResourceId": "test-github", "ResourceProperties": {}}
        props = {"SecretArn": "arn:aws:secretsmanager:us-east-1:123456789012:secret:test"}
        result = provider.handle_update(event, props)
        assert result["Data"]["ProviderArn"] == (
            "arn:aws:bedrock-agentcore:us-east-1:123456789012:"
            "token-vault/default/oauth2credentialprovider/test-github"
        )
        assert result["Data"]["CallbackUrl"] == "https://callback.example.com/cb"


# ---------------------------------------------------------------------------
# handle_delete
# ---------------------------------------------------------------------------

class TestHandleDelete:
    def test_delete_success(self):
        mock_bedrock.delete_oauth2_credential_provider.return_value = {}
        event = {"PhysicalResourceId": "test-github", "ResourceProperties": {}}
        result = provider.handle_delete(event, {})
        assert result == {"PhysicalResourceId": "test-github"}
        mock_bedrock.delete_oauth2_credential_provider.assert_called_once_with(name="test-github")

    def test_delete_not_found_is_swallowed(self):
        not_found = MagicMock()
        mock_bedrock.exceptions.ResourceNotFoundException = type("ResourceNotFoundException", (Exception,), {})
        mock_bedrock.delete_oauth2_credential_provider.side_effect = (
            mock_bedrock.exceptions.ResourceNotFoundException("not found")
        )
        event = {"PhysicalResourceId": "test-github", "ResourceProperties": {}}
        result = provider.handle_delete(event, {})
        assert result == {"PhysicalResourceId": "test-github"}

    def test_delete_other_exception_re_raises(self):
        mock_bedrock.exceptions.ResourceNotFoundException = type("ResourceNotFoundException", (Exception,), {})
        mock_bedrock.delete_oauth2_credential_provider.side_effect = Exception("InternalError")
        event = {"PhysicalResourceId": "test-github", "ResourceProperties": {}}
        with pytest.raises(Exception, match="InternalError"):
            provider.handle_delete(event, {})


# ---------------------------------------------------------------------------
# handler dispatch
# ---------------------------------------------------------------------------

class TestHandler:
    def test_create_dispatched(self):
        mock_bedrock.create_oauth2_credential_provider.return_value = {}
        event = {
            "RequestType": "Create",
            "ResourceProperties": {
                "ProviderName": "test-github",
                "SecretArn": "arn:aws:secretsmanager:us-east-1:123456789012:secret:test",
            },
        }
        result = provider.handler(event, {})
        assert result["PhysicalResourceId"] == "test-github"

    def test_unknown_request_type_raises(self):
        event = {
            "RequestType": "Purge",
            "ResourceProperties": {"ProviderName": "test-github", "SecretArn": "arn:x"},
        }
        with pytest.raises(ValueError, match="Unknown request type"):
            provider.handler(event, {})
