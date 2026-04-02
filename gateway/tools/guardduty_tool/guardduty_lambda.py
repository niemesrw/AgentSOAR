# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Severity band definitions (GuardDuty uses a float 0.1–10.0)
_SEVERITY_THRESHOLDS: dict[str, float] = {
    "LOW": 1.0,
    "MEDIUM": 4.0,
    "HIGH": 7.0,
    "CRITICAL": 9.0,
}

# Human-readable category descriptions keyed by GuardDuty finding-type prefix
_TYPE_DESCRIPTIONS: dict[str, str] = {
    "Backdoor": "Indicates that an EC2 instance may have been compromised and is being used for backdoor attacks.",
    "Behavior": "Detects unusual API call patterns that deviate from the baseline for the account.",
    "CryptoCurrency": "Detects EC2 instances querying cryptocurrency-related domains, a common sign of cryptomining malware.",
    "DefenseEvasion": "API activity designed to evade detection, such as disabling logging or CloudTrail.",
    "Discovery": "Reconnaissance activity such as unusual API calls to discover account resources.",
    "Exfiltration": "Possible data exfiltration patterns detected from EC2 instances or S3 buckets.",
    "Impact": "Actions that could affect the confidentiality, integrity, or availability of resources.",
    "InitialAccess": "Attempts to gain initial access to the AWS environment.",
    "Persistence": "Actions that suggest an attempt to maintain access in the environment.",
    "Policy": "IAM or S3 policy misconfiguration that exposes resources to unintended access.",
    "PrivilegeEscalation": "Attempts to gain higher-level permissions within the AWS environment.",
    "Recon": "Reconnaissance activity suggesting an attacker is gathering information about the environment.",
    "Stealth": "Activity designed to hide malicious behavior, such as deleting CloudTrail logs.",
    "Trojan": "EC2 instance is communicating with infrastructure associated with trojan malware.",
    "UnauthorizedAccess": "Suspicious activity suggesting unauthorized access to EC2 instances or AWS credentials.",
}

# Recommended immediate actions keyed by severity band
_SEVERITY_ACTIONS: dict[str, list[str]] = {
    "CRITICAL": [
        "Immediately isolate affected EC2 instances by applying a restrictive security group.",
        "Revoke and rotate any IAM credentials associated with the finding.",
        "Enable enhanced monitoring and capture memory/disk forensics before remediation.",
        "Notify the security team and initiate the incident response plan.",
        "Preserve CloudTrail and VPC Flow Logs for forensic analysis.",
    ],
    "HIGH": [
        "Review and restrict IAM permissions implicated in the finding.",
        "Inspect affected EC2 instances for unauthorized processes or files.",
        "Check CloudTrail for related API activity within ±2 hours of the finding.",
        "Consider isolating affected resources pending investigation.",
    ],
    "MEDIUM": [
        "Review the affected resource configuration and recent CloudTrail events.",
        "Verify the activity is authorized; escalate if it cannot be confirmed.",
        "Apply least-privilege principles to the implicated IAM entity.",
    ],
    "LOW": [
        "Document the finding and review periodically.",
        "Verify the activity is expected; archive the finding if it is a known false positive.",
    ],
}


def _get_guardduty_client(region: str) -> Any:
    """Return a GuardDuty boto3 client for the given region."""
    return boto3.client("guardduty", region_name=region)


def _list_detectors(client: Any) -> list[str]:
    """Return all GuardDuty detector IDs in the current account/region."""
    paginator = client.get_paginator("list_detectors")
    detector_ids: list[str] = []
    for page in paginator.paginate():
        detector_ids.extend(page.get("DetectorIds", []))
    return detector_ids


def _severity_label(score: float) -> str:
    """Convert a GuardDuty float severity score to a human-readable label."""
    if score >= _SEVERITY_THRESHOLDS["CRITICAL"]:
        return "CRITICAL"
    if score >= _SEVERITY_THRESHOLDS["HIGH"]:
        return "HIGH"
    if score >= _SEVERITY_THRESHOLDS["MEDIUM"]:
        return "MEDIUM"
    return "LOW"


def _type_category(finding_type: str) -> str:
    """Extract the category prefix from a GuardDuty finding type string."""
    return finding_type.split(":")[0] if ":" in finding_type else finding_type


def _format_finding_summary(finding: dict[str, Any]) -> str:
    """Return a concise one-line summary for a GuardDuty finding."""
    severity_score: float = finding.get("Severity", 0.0)
    label = _severity_label(severity_score)
    finding_type: str = finding.get("Type", "Unknown")
    finding_id: str = finding.get("Id", "unknown")
    title: str = finding.get("Title", finding_type)
    region: str = finding.get("Region", "unknown")
    return f"[{label}] {title} (id={finding_id}, type={finding_type}, region={region})"


def _resource_summary(service: dict[str, Any], resource: dict[str, Any]) -> str:
    """Build a brief description of the affected resource from the finding detail."""
    lines: list[str] = []

    resource_type: str = resource.get("ResourceType", "Unknown")
    lines.append(f"Resource type: {resource_type}")

    if resource_type == "Instance":
        instance = resource.get("InstanceDetails", {})
        lines.append(f"  Instance ID : {instance.get('InstanceId', 'n/a')}")
        lines.append(f"  Instance type: {instance.get('InstanceType', 'n/a')}")
        lines.append(f"  Image ID    : {instance.get('ImageId', 'n/a')}")
        lines.append(f"  State       : {instance.get('InstanceState', 'n/a')}")
        tags = {t["Key"]: t["Value"] for t in instance.get("Tags", [])}
        if tags:
            lines.append(f"  Tags        : {json.dumps(tags)}")

    elif resource_type == "AccessKey":
        access_key = resource.get("AccessKeyDetails", {})
        lines.append(f"  User name   : {access_key.get('UserName', 'n/a')}")
        lines.append(f"  User type   : {access_key.get('UserType', 'n/a')}")
        lines.append(f"  Access key  : {access_key.get('AccessKeyId', 'n/a')}")

    elif resource_type == "S3Bucket":
        for bucket in resource.get("S3BucketDetails", []):
            lines.append(f"  Bucket name : {bucket.get('Name', 'n/a')}")
            lines.append(f"  Bucket type : {bucket.get('Type', 'n/a')}")

    elif resource_type == "EKSCluster":
        cluster = resource.get("EksClusterDetails", {})
        lines.append(f"  Cluster name: {cluster.get('Name', 'n/a')}")

    # Actor / remote IP
    action = service.get("Action", {})
    remote_ip_details = None
    for action_type in action.values():
        if isinstance(action_type, dict):
            remote_ip_details = action_type.get("RemoteIpDetails")
            if remote_ip_details:
                break

    if remote_ip_details:
        city = remote_ip_details.get("City", {}).get("CityName", "n/a")
        country = remote_ip_details.get("Country", {}).get("CountryName", "n/a")
        org = remote_ip_details.get("Organization", {}).get("Org", "n/a")
        ip = remote_ip_details.get("IpAddressV4", "n/a")
        lines.append(f"Actor IP : {ip} ({city}, {country} / {org})")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def get_guardduty_findings(
    severity: str = "MEDIUM",
    region: str | None = None,
    account_id: str | None = None,
    max_results: int = 20,
) -> str:
    """
    List active GuardDuty findings filtered by minimum severity.

    Returns a formatted list of matching findings.
    """
    region = region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    max_results = max(1, min(50, max_results))
    min_score = _SEVERITY_THRESHOLDS.get(severity.upper(), 4.0)

    client = _get_guardduty_client(region)
    detector_ids = _list_detectors(client)
    if not detector_ids:
        return f"No GuardDuty detectors found in region {region}."

    # Use the first (primary) detector
    detector_id = detector_ids[0]

    criteria: dict[str, Any] = {
        "Criterion": {
            "severity": {"GreaterThanOrEqual": int(min_score)},
            # Exclude suppressed/archived findings — only return actionable active findings
            "service.archived": {"Eq": ["false"]},
        }
    }

    if account_id:
        criteria["Criterion"]["accountId"] = {"Eq": [account_id]}

    try:
        list_resp = client.list_findings(
            DetectorId=detector_id,
            FindingCriteria=criteria,
            MaxResults=max_results,
            SortCriteria={"AttributeName": "severity", "OrderBy": "DESC"},
        )
    except ClientError as exc:
        return f"Error listing findings: {exc.response['Error']['Message']}"

    finding_ids: list[str] = list_resp.get("FindingIds", [])
    if not finding_ids:
        return f"No active GuardDuty findings with severity >= {severity} found in {region}."

    try:
        detail_resp = client.get_findings(
            DetectorId=detector_id,
            FindingIds=finding_ids,
        )
    except ClientError as exc:
        return f"Error retrieving finding details: {exc.response['Error']['Message']}"

    findings = detail_resp.get("Findings", [])
    lines = [
        f"Found {len(findings)} GuardDuty finding(s) in {region} with severity >= {severity}:\n"
    ]
    for finding in findings:
        lines.append("  " + _format_finding_summary(finding))

    return "\n".join(lines)


def describe_guardduty_finding(finding_id: str, region: str | None = None) -> str:
    """
    Return a detailed, human-readable description of a GuardDuty finding.
    """
    region = region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

    client = _get_guardduty_client(region)
    detector_ids = _list_detectors(client)
    if not detector_ids:
        return f"No GuardDuty detectors found in region {region}."

    detector_id = detector_ids[0]

    try:
        resp = client.get_findings(
            DetectorId=detector_id,
            FindingIds=[finding_id],
        )
    except ClientError as exc:
        return (
            f"Error retrieving finding {finding_id}: {exc.response['Error']['Message']}"
        )

    findings = resp.get("Findings", [])
    if not findings:
        return f"Finding {finding_id} not found in detector {detector_id} ({region})."

    f = findings[0]
    finding_type: str = f.get("Type", "Unknown")
    category = _type_category(finding_type)
    severity_score: float = f.get("Severity", 0.0)
    severity_label = _severity_label(severity_score)
    service: dict[str, Any] = f.get("Service", {})
    resource: dict[str, Any] = f.get("Resource", {})

    lines: list[str] = [
        f"Finding ID  : {f.get('Id', finding_id)}",
        f"Title       : {f.get('Title', 'n/a')}",
        f"Type        : {finding_type}",
        f"Severity    : {severity_label} ({severity_score})",
        f"Region      : {f.get('Region', region)}",
        f"Account     : {f.get('AccountId', 'n/a')}",
        f"Created at  : {f.get('CreatedAt', 'n/a')}",
        f"Updated at  : {f.get('UpdatedAt', 'n/a')}",
        f"Count       : {service.get('Count', 1)} occurrence(s)",
        "",
        "Description:",
        f.get("Description", "No description available."),
        "",
        "Category context:",
        _TYPE_DESCRIPTIONS.get(category, "No additional category context available."),
        "",
        "Affected resource:",
        _resource_summary(service, resource),
    ]

    return "\n".join(lines)


def archive_guardduty_finding(finding_ids: list[str], region: str | None = None) -> str:
    """
    Archive (suppress) one or more GuardDuty findings.
    """
    region = region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    if not finding_ids:
        return "No finding IDs provided."

    client = _get_guardduty_client(region)
    detector_ids = _list_detectors(client)
    if not detector_ids:
        return f"No GuardDuty detectors found in region {region}."

    detector_id = detector_ids[0]

    try:
        client.archive_findings(
            DetectorId=detector_id,
            FindingIds=finding_ids,
        )
    except ClientError as exc:
        return f"Error archiving findings: {exc.response['Error']['Message']}"

    ids_str = ", ".join(finding_ids)
    return (
        f"Successfully archived {len(finding_ids)} finding(s) in {region}: {ids_str}\n"
        "These findings are now suppressed and will not appear in the active findings list."
    )


def triage_guardduty_finding(finding_id: str, region: str | None = None) -> str:
    """
    Run an automated triage playbook for a GuardDuty finding.

    Steps:
    1. Retrieve the finding details
    2. Classify by type and severity
    3. Enrich with resource context
    4. Recommend remediation actions
    """
    region = region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

    client = _get_guardduty_client(region)
    detector_ids = _list_detectors(client)
    if not detector_ids:
        return f"No GuardDuty detectors found in region {region}."

    detector_id = detector_ids[0]

    try:
        resp = client.get_findings(
            DetectorId=detector_id,
            FindingIds=[finding_id],
        )
    except ClientError as exc:
        return (
            f"Error retrieving finding {finding_id}: {exc.response['Error']['Message']}"
        )

    findings = resp.get("Findings", [])
    if not findings:
        return f"Finding {finding_id} not found in detector {detector_id} ({region})."

    f = findings[0]
    finding_type: str = f.get("Type", "Unknown")
    category = _type_category(finding_type)
    severity_score: float = f.get("Severity", 0.0)
    severity_label = _severity_label(severity_score)
    service: dict[str, Any] = f.get("Service", {})
    resource: dict[str, Any] = f.get("Resource", {})

    # --- Step 1: Classification ---
    classification_lines: list[str] = [
        "## Step 1 — Classification",
        f"Finding type : {finding_type}",
        f"Category     : {category}",
        f"Severity     : {severity_label} ({severity_score})",
        f"Description  : {_TYPE_DESCRIPTIONS.get(category, 'Unknown category.')}",
    ]

    # --- Step 2: Enrichment ---
    enrichment_lines: list[str] = [
        "",
        "## Step 2 — Resource Enrichment",
        _resource_summary(service, resource),
        f"First seen   : {service.get('EventFirstSeen', 'n/a')}",
        f"Last seen    : {service.get('EventLastSeen', 'n/a')}",
        f"Occurrences  : {service.get('Count', 1)}",
    ]

    # --- Step 3: Risk assessment ---
    risk_score = severity_score
    risk_factors: list[str] = []
    if service.get("Count", 1) > 5:
        risk_factors.append("repeated occurrences (>5)")
        risk_score = min(10.0, risk_score + 0.5)
    if severity_label in ("HIGH", "CRITICAL"):
        risk_factors.append("high/critical base severity")
    if category in (
        "UnauthorizedAccess",
        "Persistence",
        "Exfiltration",
        "PrivilegeEscalation",
    ):
        risk_factors.append(f"high-impact category ({category})")
        risk_score = min(10.0, risk_score + 0.5)

    risk_text = ", ".join(risk_factors) if risk_factors else "none identified"
    risk_lines: list[str] = [
        "",
        "## Step 3 — Risk Assessment",
        f"Adjusted risk score : {risk_score:.1f} / 10",
        f"Risk factors        : {risk_text}",
    ]

    # --- Step 4: Recommended actions ---
    actions = _SEVERITY_ACTIONS.get(severity_label, _SEVERITY_ACTIONS["LOW"])
    action_lines: list[str] = (
        [
            "",
            "## Step 4 — Recommended Actions",
        ]
        + [f"  {i + 1}. {action}" for i, action in enumerate(actions)]
        + [
            "",
            "Note: Review the finding details carefully before executing any remediation.",
            f"      Archive this finding (id={finding_id}) once remediation is confirmed.",
        ]
    )

    header = [
        f"# Automated Triage Report — {f.get('Title', finding_type)}",
        f"Finding ID: {finding_id}",
        f"Account: {f.get('AccountId', 'n/a')} | Region: {f.get('Region', region)}",
        "",
    ]

    return "\n".join(
        header + classification_lines + enrichment_lines + risk_lines + action_lines
    )


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------

_TOOL_DISPATCH: dict[str, Any] = {
    "get_guardduty_findings": get_guardduty_findings,
    "describe_guardduty_finding": describe_guardduty_finding,
    "archive_guardduty_finding": archive_guardduty_finding,
    "triage_guardduty_finding": triage_guardduty_finding,
}


def _handle_eventbridge(event: dict[str, Any]) -> dict[str, Any]:
    """
    Handle an EventBridge 'GuardDuty Finding' event.

    EventBridge invocations do not carry client_context, so they must be
    detected and dispatched separately.  The raw finding is logged for
    downstream processing (e.g. CloudWatch Logs Insights, SIEM forwarding).
    Extend this function to trigger automated workflows on new findings.
    """
    detail = event.get("detail", {})
    finding_id = detail.get("id", "unknown")
    severity = detail.get("severity", 0.0)
    finding_type = detail.get("type", "unknown")
    region = detail.get("region", event.get("region", "unknown"))
    logger.info(
        "EventBridge GuardDuty finding ingested: id=%s type=%s severity=%s region=%s",
        finding_id,
        finding_type,
        severity,
        region,
    )
    return {"statusCode": 200, "body": f"Ingested finding {finding_id}"}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    GuardDuty tools Lambda handler for AgentSOAR AgentCore Gateway.

    Handles two invocation paths:
      1. AgentCore Gateway tool call — context.client_context carries the tool name.
      2. EventBridge 'GuardDuty Finding' event — no client_context; routed to
         _handle_eventbridge() for ingestion/logging.

    Implements four tools:
    - get_guardduty_findings
    - describe_guardduty_finding
    - archive_guardduty_finding
    - triage_guardduty_finding

    Gateway input format:
        event: tool arguments passed directly from the AgentCore Gateway
        context.client_context.custom['bedrockAgentCoreToolName']: full tool name with target prefix

    Gateway output format:
        {"content": [{"type": "text", "text": "<result>"}]}
        or
        {"error": "<message>"}
    """
    logger.info("Received event: %s", json.dumps(event))

    # EventBridge invocations have no client_context — detect and dispatch early.
    if event.get("source") == "aws.guardduty":
        return _handle_eventbridge(event)

    try:
        delimiter = "___"
        original_tool_name: str = context.client_context.custom[
            "bedrockAgentCoreToolName"
        ]
        tool_name = original_tool_name[
            original_tool_name.index(delimiter) + len(delimiter) :
        ]
        logger.info("Processing tool: %s", tool_name)

        if tool_name not in _TOOL_DISPATCH:
            logger.error("Unexpected tool name: %s", tool_name)
            return {
                "error": (
                    f"This Lambda only supports {list(_TOOL_DISPATCH.keys())}, "
                    f"received: {tool_name}"
                )
            }

        func = _TOOL_DISPATCH[tool_name]

        if tool_name == "get_guardduty_findings":
            result = func(
                severity=event.get("severity", "MEDIUM"),
                region=event.get("region"),
                account_id=event.get("account_id"),
                max_results=int(event.get("max_results", 20)),
            )
        elif tool_name in ("describe_guardduty_finding", "triage_guardduty_finding"):
            result = func(
                finding_id=event["finding_id"],
                region=event.get("region"),
            )
        elif tool_name == "archive_guardduty_finding":
            result = func(
                finding_ids=event["finding_ids"],
                region=event.get("region"),
            )
        else:
            result = func(**event)

        return {"content": [{"type": "text", "text": result}]}

    except KeyError as exc:
        logger.error("Missing required parameter: %s", exc)
        return {"error": f"Missing required parameter: {exc}"}
    except Exception as exc:
        logger.error("Error processing request: %s", exc, exc_info=True)
        return {"error": f"Internal server error: {exc}"}
