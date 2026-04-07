# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for the GuardDuty tool Lambda.
"""

import importlib

# The Lambda module imports boto3 at the top level; patch before importing
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# Absolute path to the guardduty_tool package, resolved relative to this file
# so tests are portable across environments.
_GUARDDUTY_TOOL_DIR = str(
    Path(__file__).resolve().parents[2] / "gateway" / "tools" / "guardduty_tool"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(tool_name: str) -> SimpleNamespace:
    """Build a minimal Lambda context object containing the tool name."""
    return SimpleNamespace(
        client_context=SimpleNamespace(
            custom={"bedrockAgentCoreToolName": f"target123___{tool_name}"}
        )
    )


def _sample_finding(
    finding_id: str = "abc123",
    finding_type: str = "UnauthorizedAccess:EC2/SSHBruteForce",
    severity: float = 5.0,
) -> dict:
    return {
        "Id": finding_id,
        "AccountId": "123456789012",
        "Region": "us-east-1",
        "Type": finding_type,
        "Title": "SSH brute force attack detected",
        "Description": "An EC2 instance is being targeted by a brute force SSH attack.",
        "Severity": severity,
        "CreatedAt": "2024-01-01T00:00:00Z",
        "UpdatedAt": "2024-01-01T01:00:00Z",
        "Resource": {
            "ResourceType": "Instance",
            "InstanceDetails": {
                "InstanceId": "i-0abc123",
                "InstanceType": "t3.micro",
                "ImageId": "ami-0abc123",
                "InstanceState": "running",
                "Tags": [{"Key": "Name", "Value": "web-server"}],
            },
        },
        "Service": {
            "Count": 3,
            "EventFirstSeen": "2024-01-01T00:00:00Z",
            "EventLastSeen": "2024-01-01T01:00:00Z",
            "Action": {
                "networkConnectionAction": {
                    "RemoteIpDetails": {
                        "IpAddressV4": "1.2.3.4",
                        "City": {"CityName": "Amsterdam"},
                        "Country": {"CountryName": "Netherlands"},
                        "Organization": {"Org": "AS12345 Example ISP"},
                    }
                }
            },
        },
    }


# ---------------------------------------------------------------------------
# Import the module under test after setting up patches
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def mock_boto3(monkeypatch):
    """Prevent real AWS calls during all tests."""
    mock_client = MagicMock()
    mock_client.get_paginator.return_value.paginate.return_value = [
        {"DetectorIds": ["detector-abc"]}
    ]
    mock_client.list_findings.return_value = {"FindingIds": ["abc123"]}
    mock_client.get_findings.return_value = {"Findings": [_sample_finding()]}
    mock_client.archive_findings.return_value = {}

    with patch("boto3.client", return_value=mock_client):
        # Re-import module so patches apply
        if "gateway.tools.guardduty_tool.guardduty_lambda" in sys.modules:
            del sys.modules["gateway.tools.guardduty_tool.guardduty_lambda"]
        yield mock_client


# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------


@pytest.fixture()
def guardduty_module(mock_boto3):
    """Import guardduty_lambda with boto3 patched."""
    sys.path.insert(0, _GUARDDUTY_TOOL_DIR)
    import guardduty_lambda as mod

    importlib.reload(mod)
    yield mod
    sys.path.pop(0)


# ---------------------------------------------------------------------------
# _severity_label tests
# ---------------------------------------------------------------------------


class TestSeverityLabel:
    def test_critical(self, guardduty_module):
        assert guardduty_module._severity_label(9.5) == "CRITICAL"

    def test_high(self, guardduty_module):
        assert guardduty_module._severity_label(8.0) == "HIGH"

    def test_medium(self, guardduty_module):
        assert guardduty_module._severity_label(5.0) == "MEDIUM"

    def test_low(self, guardduty_module):
        assert guardduty_module._severity_label(2.0) == "LOW"

    def test_boundary_critical(self, guardduty_module):
        assert guardduty_module._severity_label(9.0) == "CRITICAL"

    def test_boundary_high(self, guardduty_module):
        assert guardduty_module._severity_label(7.0) == "HIGH"

    def test_boundary_medium(self, guardduty_module):
        assert guardduty_module._severity_label(4.0) == "MEDIUM"


# ---------------------------------------------------------------------------
# get_guardduty_findings tests
# ---------------------------------------------------------------------------


class TestGetGuarddutyFindings:
    def test_returns_findings(self, guardduty_module, mock_boto3):
        result = guardduty_module.get_guardduty_findings(
            severity="MEDIUM", region="us-east-1"
        )
        assert "GuardDuty finding" in result
        assert "SSH brute force" in result

    def test_no_detector(self, guardduty_module, mock_boto3):
        mock_boto3.get_paginator.return_value.paginate.return_value = [
            {"DetectorIds": []}
        ]
        result = guardduty_module.get_guardduty_findings()
        assert "No GuardDuty detectors found" in result

    def test_no_findings(self, guardduty_module, mock_boto3):
        mock_boto3.list_findings.return_value = {"FindingIds": []}
        result = guardduty_module.get_guardduty_findings(severity="HIGH")
        assert "No active GuardDuty findings" in result

    def test_max_results_clamped(self, guardduty_module, mock_boto3):
        # Should not raise; max_results is clamped internally
        guardduty_module.get_guardduty_findings(max_results=999)
        call_kwargs = mock_boto3.list_findings.call_args[1]
        assert call_kwargs["MaxResults"] <= 50

    def test_client_error_list(self, guardduty_module, mock_boto3):
        from botocore.exceptions import ClientError

        mock_boto3.list_findings.side_effect = ClientError(
            {"Error": {"Code": "BadRequest", "Message": "test error"}}, "ListFindings"
        )
        result = guardduty_module.get_guardduty_findings()
        assert "Error listing findings" in result


# ---------------------------------------------------------------------------
# describe_guardduty_finding tests
# ---------------------------------------------------------------------------


class TestDescribeGuarddutyFinding:
    def test_returns_description(self, guardduty_module, mock_boto3):
        result = guardduty_module.describe_guardduty_finding(
            "abc123", region="us-east-1"
        )
        assert "SSH brute force" in result
        assert "UnauthorizedAccess:EC2/SSHBruteForce" in result
        assert "MEDIUM" in result

    def test_finding_not_found(self, guardduty_module, mock_boto3):
        mock_boto3.get_findings.return_value = {"Findings": []}
        result = guardduty_module.describe_guardduty_finding("nonexistent")
        assert "not found" in result

    def test_no_detector(self, guardduty_module, mock_boto3):
        mock_boto3.get_paginator.return_value.paginate.return_value = [
            {"DetectorIds": []}
        ]
        result = guardduty_module.describe_guardduty_finding("abc123")
        assert "No GuardDuty detectors found" in result

    def test_resource_summary_accesskey(self, guardduty_module, mock_boto3):
        finding = _sample_finding()
        finding["Resource"] = {
            "ResourceType": "AccessKey",
            "AccessKeyDetails": {
                "UserName": "testuser",
                "UserType": "IAMUser",
                "AccessKeyId": "AKIAIOSFODNN7EXAMPLE",  # pragma: allowlist secret
            },
        }
        mock_boto3.get_findings.return_value = {"Findings": [finding]}
        result = guardduty_module.describe_guardduty_finding("abc123")
        assert "testuser" in result

    def test_resource_summary_s3(self, guardduty_module, mock_boto3):
        finding = _sample_finding()
        finding["Resource"] = {
            "ResourceType": "S3Bucket",
            "S3BucketDetails": [{"Name": "my-bucket", "Type": "Destination"}],
        }
        mock_boto3.get_findings.return_value = {"Findings": [finding]}
        result = guardduty_module.describe_guardduty_finding("abc123")
        assert "my-bucket" in result


# ---------------------------------------------------------------------------
# archive_guardduty_finding tests
# ---------------------------------------------------------------------------


class TestArchiveGuarddutyFinding:
    def test_archives_successfully(self, guardduty_module, mock_boto3):
        result = guardduty_module.archive_guardduty_finding(
            ["abc123"], region="us-east-1"
        )
        assert "Successfully archived" in result
        assert "abc123" in result
        mock_boto3.archive_findings.assert_called_once()

    def test_empty_list(self, guardduty_module, mock_boto3):
        result = guardduty_module.archive_guardduty_finding([])
        assert "No finding IDs provided" in result
        mock_boto3.archive_findings.assert_not_called()

    def test_no_detector(self, guardduty_module, mock_boto3):
        mock_boto3.get_paginator.return_value.paginate.return_value = [
            {"DetectorIds": []}
        ]
        result = guardduty_module.archive_guardduty_finding(["abc123"])
        assert "No GuardDuty detectors found" in result

    def test_client_error(self, guardduty_module, mock_boto3):
        from botocore.exceptions import ClientError

        mock_boto3.archive_findings.side_effect = ClientError(
            {"Error": {"Code": "BadRequest", "Message": "archive failed"}},
            "ArchiveFindings",
        )
        result = guardduty_module.archive_guardduty_finding(["abc123"])
        assert "Error archiving findings" in result


# ---------------------------------------------------------------------------
# triage_guardduty_finding tests
# ---------------------------------------------------------------------------


class TestTriageGuarddutyFinding:
    def test_triage_report_structure(self, guardduty_module, mock_boto3):
        result = guardduty_module.triage_guardduty_finding("abc123", region="us-east-1")
        assert "Automated Triage Report" in result
        assert "Step 1" in result
        assert "Step 2" in result
        assert "Step 3" in result
        assert "Step 4" in result

    def test_triage_critical_finding(self, guardduty_module, mock_boto3):
        finding = _sample_finding(severity=9.5)
        mock_boto3.get_findings.return_value = {"Findings": [finding]}
        result = guardduty_module.triage_guardduty_finding("abc123")
        assert "CRITICAL" in result
        assert "isolate" in result.lower()

    def test_triage_finding_not_found(self, guardduty_module, mock_boto3):
        mock_boto3.get_findings.return_value = {"Findings": []}
        result = guardduty_module.triage_guardduty_finding("nonexistent")
        assert "not found" in result

    def test_risk_factors_repeat_occurrences(self, guardduty_module, mock_boto3):
        finding = _sample_finding()
        finding["Service"]["Count"] = 10
        mock_boto3.get_findings.return_value = {"Findings": [finding]}
        result = guardduty_module.triage_guardduty_finding("abc123")
        assert "repeated occurrences" in result

    def test_risk_factors_high_impact_category(self, guardduty_module, mock_boto3):
        finding = _sample_finding(finding_type="Exfiltration:S3/MaliciousIPCaller")
        mock_boto3.get_findings.return_value = {"Findings": [finding]}
        result = guardduty_module.triage_guardduty_finding("abc123")
        assert "Exfiltration" in result


# ---------------------------------------------------------------------------
# Handler dispatch tests
# ---------------------------------------------------------------------------


class TestHandler:
    def test_get_guardduty_findings_dispatch(self, guardduty_module, mock_boto3):
        ctx = _make_context("get_guardduty_findings")
        resp = guardduty_module.handler(
            {"severity": "HIGH", "region": "us-east-1"}, ctx
        )
        assert "content" in resp
        assert resp["content"][0]["type"] == "text"

    def test_describe_guardduty_finding_dispatch(self, guardduty_module, mock_boto3):
        ctx = _make_context("describe_guardduty_finding")
        resp = guardduty_module.handler(
            {"finding_id": "abc123", "region": "us-east-1"}, ctx
        )
        assert "content" in resp

    def test_archive_guardduty_finding_dispatch(self, guardduty_module, mock_boto3):
        ctx = _make_context("archive_guardduty_finding")
        resp = guardduty_module.handler(
            {"finding_ids": ["abc123"], "region": "us-east-1"}, ctx
        )
        assert "content" in resp

    def test_triage_guardduty_finding_dispatch(self, guardduty_module, mock_boto3):
        ctx = _make_context("triage_guardduty_finding")
        resp = guardduty_module.handler(
            {"finding_id": "abc123", "region": "us-east-1"}, ctx
        )
        assert "content" in resp

    def test_unknown_tool_returns_error(self, guardduty_module, mock_boto3):
        ctx = _make_context("unknown_tool")
        resp = guardduty_module.handler({}, ctx)
        assert "error" in resp

    def test_missing_required_param_returns_error(self, guardduty_module, mock_boto3):
        ctx = _make_context("describe_guardduty_finding")
        # Missing 'finding_id' key
        resp = guardduty_module.handler({}, ctx)
        assert "error" in resp

    def test_eventbridge_invocation_returns_200(self, guardduty_module, mock_boto3):
        """EventBridge events must NOT crash on missing client_context."""
        event = {
            "source": "aws.guardduty",
            "detail-type": "GuardDuty Finding",
            "region": "us-east-1",
            "detail": {
                "id": "eb-finding-1",
                "type": "UnauthorizedAccess:EC2/SSHBruteForce",
                "severity": 5.0,
                "region": "us-east-1",
            },
        }
        # Pass None as context — EventBridge does not set client_context
        resp = guardduty_module.handler(event, None)
        assert resp.get("statusCode") == 200
        assert "eb-finding-1" in resp.get("body", "")

    def test_get_findings_excludes_archived(self, guardduty_module, mock_boto3):
        """list_findings call must include a service.archived = false criterion."""
        guardduty_module.get_guardduty_findings(severity="MEDIUM", region="us-east-1")
        call_kwargs = mock_boto3.list_findings.call_args[1]
        criterion = call_kwargs["FindingCriteria"]["Criterion"]
        assert "service.archived" in criterion
        assert criterion["service.archived"] == {"Eq": ["false"]}

    def test_eventbridge_stores_finding(self, guardduty_module, mock_boto3):
        """_handle_eventbridge must attempt to write to DynamoDB when FINDINGS_TABLE is set."""
        mock_table = MagicMock()
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table

        event = {
            "source": "aws.guardduty",
            "account": "123456789012",
            "region": "us-east-1",
            "detail": {
                "id": "store-test-1",
                "type": "UnauthorizedAccess:EC2/SSHBruteForce",
                "severity": 7.5,
                "region": "us-east-1",
                "accountId": "123456789012",
            },
        }
        with patch.dict(
            guardduty_module.os.environ, {"FINDINGS_TABLE": "test-findings-table"}
        ):
            with patch("boto3.resource", return_value=mock_resource):
                resp = guardduty_module.handler(event, None)

        assert resp.get("statusCode") == 200
        mock_table.put_item.assert_called_once()
        item = mock_table.put_item.call_args[1]["Item"]
        assert item["findingId"] == "store-test-1"
        assert item["severityLabel"] == "HIGH"

    def test_eventbridge_no_table_no_crash(self, guardduty_module, mock_boto3):
        """_handle_eventbridge must not crash when FINDINGS_TABLE is absent."""
        event = {
            "source": "aws.guardduty",
            "detail": {
                "id": "no-table-test",
                "type": "Recon:EC2/PortProbeUnprotectedPort",
                "severity": 3.0,
            },
        }
        with patch.dict(guardduty_module.os.environ, {}, clear=False):
            guardduty_module.os.environ.pop("FINDINGS_TABLE", None)
            resp = guardduty_module.handler(event, None)
        assert resp.get("statusCode") == 200


# ---------------------------------------------------------------------------
# list_guardduty_findings_since tests
# ---------------------------------------------------------------------------


class TestListGuarddutyFindingsSince:
    def test_no_table_configured(self, guardduty_module, mock_boto3):
        with patch.dict(guardduty_module.os.environ, {}, clear=False):
            guardduty_module.os.environ.pop("FINDINGS_TABLE", None)
            result = guardduty_module.list_guardduty_findings_since()
        assert "not configured" in result

    def test_returns_findings(self, guardduty_module, mock_boto3):
        mock_table = MagicMock()
        mock_table.query.return_value = {
            "Items": [
                {
                    "findingId": "abc123",
                    "findingType": "UnauthorizedAccess:EC2/SSHBruteForce",
                    "severityLabel": "MEDIUM",
                    "accountId": "123456789012",
                    "region": "us-east-1",
                    "ingestedAt": "2024-01-01T00:00:00+00:00",
                    "ingestedAtEpoch": 1704067200,
                }
            ]
        }
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table

        with patch.dict(guardduty_module.os.environ, {"FINDINGS_TABLE": "test-table"}):
            with patch("boto3.resource", return_value=mock_resource):
                result = guardduty_module.list_guardduty_findings_since(hours=24)

        assert "abc123" in result
        assert "MEDIUM" in result

    def test_no_findings(self, guardduty_module, mock_boto3):
        mock_table = MagicMock()
        mock_table.query.return_value = {"Items": []}
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table

        with patch.dict(guardduty_module.os.environ, {"FINDINGS_TABLE": "test-table"}):
            with patch("boto3.resource", return_value=mock_resource):
                result = guardduty_module.list_guardduty_findings_since(hours=6)

        assert "No GuardDuty findings ingested" in result
        assert "6 hour" in result

    def test_dispatch_list_findings_since(self, guardduty_module, mock_boto3):
        mock_table = MagicMock()
        mock_table.query.return_value = {"Items": []}
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table

        ctx = _make_context("list_guardduty_findings_since")
        with patch.dict(guardduty_module.os.environ, {"FINDINGS_TABLE": "test-table"}):
            with patch("boto3.resource", return_value=mock_resource):
                resp = guardduty_module.handler({"hours": 12}, ctx)

        assert "content" in resp
