# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Cross-account client helper (same pattern as guardduty_lambda.py)
# ---------------------------------------------------------------------------


def _get_client(service: str, region: str, role_arn: str | None = None) -> Any:
    """
    Return a boto3 client for *service*, optionally via cross-account role assumption.

    When role_arn is provided the Lambda assumes that role via STS and returns a
    client scoped to those temporary credentials.  When absent, the Lambda's own
    execution credentials are used (same-account or local testing).
    """
    if role_arn:
        sts = boto3.client("sts")
        resp = sts.assume_role(
            RoleArn=role_arn,
            RoleSessionName="agentsoar-cloudtrail",
            ExternalId="agentsoar",
            DurationSeconds=900,
        )
        creds = resp["Credentials"]
        session = boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
        )
        return session.client(service, region_name=region)
    return boto3.client(service, region_name=region)


def _cloudtrail_client(region: str, account_id: str | None = None) -> Any:
    """
    Return a CloudTrail client, assuming a cross-account role when account_id
    differs from the Lambda's own account.

    Cross-account role ARN is built from CROSS_ACCOUNT_ROLE_NAME env var
    (default: AgentSOAR-CloudTrailReadRole) and the requested account_id.
    Falls back to Lambda execution credentials when no account_id is given or
    the account matches the Lambda's own account.
    """
    own_account = boto3.client("sts").get_caller_identity()["Account"]
    if account_id and account_id != own_account:
        role_name = os.environ.get(
            "CROSS_ACCOUNT_ROLE_NAME", "AgentSOAR-CloudTrailReadRole"
        )
        role_arn = f"arn:aws:iam::{account_id}:role/{role_name}"
        return _get_client("cloudtrail", region, role_arn=role_arn)
    return _get_client("cloudtrail", region)


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def lookup_cloudtrail_events(
    start_time: str | None = None,
    end_time: str | None = None,
    event_name: str | None = None,
    username: str | None = None,
    resource_type: str | None = None,
    resource_name: str | None = None,
    access_key_id: str | None = None,
    read_only: str | None = None,
    account_id: str | None = None,
    region: str | None = None,
    max_results: int = 20,
) -> str:
    """
    Query CloudTrail management events using the LookupEvents API.

    Covers the last 90 days of management events at no cost.  One or more
    attribute filters may be combined (all filters are ANDed).

    Args:
        start_time:    ISO-8601 UTC start (e.g. "2026-04-01T00:00:00Z"). Defaults to 24 h ago.
        end_time:      ISO-8601 UTC end.  Defaults to now.
        event_name:    CloudTrail event/API name to filter on (e.g. "DescribeStacks", "AssumeRole").
        username:      IAM user, role session name, or "Root" to filter by caller.
        resource_type: AWS resource type (e.g. "AWS::S3::Bucket", "AWS::IAM::Role").
        resource_name: Resource ARN or name to filter on.
        access_key_id: Access key ID used to make the API call.
        read_only:     "true" or "false" — filter to read-only or write events.
        account_id:    AWS account ID to query.  Defaults to the Lambda's account.
        region:        AWS region to query.  Defaults to Lambda execution region.
        max_results:   Number of events to return (1-50, default 20).
    """
    region = region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    max_results = max(1, min(50, max_results))

    # Parse time range
    now = datetime.now(timezone.utc)
    if end_time:
        end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
    else:
        end_dt = now
    if start_time:
        start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    else:
        # Default: 24 hours before end_time
        from datetime import timedelta

        start_dt = end_dt - timedelta(hours=24)

    # Build LookupAttributes — CloudTrail ANDs multiple attributes
    lookup_attrs: list[dict[str, str]] = []
    if event_name:
        lookup_attrs.append({"AttributeKey": "EventName", "AttributeValue": event_name})
    if username:
        lookup_attrs.append({"AttributeKey": "Username", "AttributeValue": username})
    if resource_type:
        lookup_attrs.append(
            {"AttributeKey": "ResourceType", "AttributeValue": resource_type}
        )
    if resource_name:
        lookup_attrs.append(
            {"AttributeKey": "ResourceName", "AttributeValue": resource_name}
        )
    if access_key_id:
        lookup_attrs.append(
            {"AttributeKey": "AccessKeyId", "AttributeValue": access_key_id}
        )
    if read_only:
        lookup_attrs.append(
            {"AttributeKey": "ReadOnly", "AttributeValue": read_only.lower()}
        )

    client = _cloudtrail_client(region, account_id)

    kwargs: dict[str, Any] = {
        "StartTime": start_dt,
        "EndTime": end_dt,
        "MaxResults": max_results,
    }
    if lookup_attrs:
        kwargs["LookupAttributes"] = lookup_attrs

    try:
        resp = client.lookup_events(**kwargs)
    except ClientError as exc:
        return f"Error querying CloudTrail: {exc.response['Error']['Message']}"

    events = resp.get("Events", [])
    if not events:
        filter_desc = _describe_filters(
            event_name, username, resource_type, resource_name, access_key_id, read_only
        )
        return (
            f"No CloudTrail events found in {account_id or 'current account'} / {region} "
            f"between {start_dt.isoformat()} and {end_dt.isoformat()}"
            + (f" with filters: {filter_desc}" if filter_desc else ".")
        )

    target = account_id or "current account"
    lines = [
        f"CloudTrail events in {target} / {region} "
        f"({start_dt.strftime('%Y-%m-%dT%H:%MZ')} → {end_dt.strftime('%Y-%m-%dT%H:%MZ')}): "
        f"{len(events)} result(s)\n"
    ]
    for ev in events:
        event_time = ev.get("EventTime", "")
        if hasattr(event_time, "isoformat"):
            event_time = event_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        name = ev.get("EventName", "?")
        user = ev.get("Username", "?")
        source = ev.get("EventSource", "?")
        event_id = ev.get("EventId", "?")
        resources = ev.get("Resources", [])
        resource_strs = [
            f"{r.get('ResourceType', '?')}:{r.get('ResourceName', '?')}"
            for r in resources[:3]
        ]
        resource_text = ", ".join(resource_strs) if resource_strs else "—"
        lines.append(
            f"  {event_time}  {name:<35} user={user:<25} src={source}\n"
            f"    event_id={event_id}  resources=[{resource_text}]"
        )

    return "\n".join(lines)


def get_trail_status(
    trail_name: str = "management-trail",
    account_id: str | None = None,
    region: str | None = None,
) -> str:
    """
    Return the logging status and health of a CloudTrail trail.

    Args:
        trail_name: Trail name or ARN.  Defaults to "management-trail".
        account_id: AWS account ID where the trail lives.  Defaults to Lambda's account.
        region:     AWS region.  Defaults to Lambda execution region.
    """
    region = region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    client = _cloudtrail_client(region, account_id)

    try:
        status = client.get_trail_status(Name=trail_name)
    except ClientError as exc:
        return f"Error getting trail status for '{trail_name}': {exc.response['Error']['Message']}"

    is_logging = status.get("IsLogging", False)
    latest_delivery = status.get("LatestDeliveryTime", "never")
    latest_digest = status.get("LatestDigestDeliveryTime", "never")
    delivery_error = status.get("LatestDeliveryError", "none")
    latest_error = (
        status.get("LatestNotificationError")
        or status.get("LatestDeliveryError")
        or "none"
    )

    if hasattr(latest_delivery, "isoformat"):
        latest_delivery = latest_delivery.strftime("%Y-%m-%dT%H:%M:%SZ")
    if hasattr(latest_digest, "isoformat"):
        latest_digest = latest_digest.strftime("%Y-%m-%dT%H:%M:%SZ")

    health = "HEALTHY" if is_logging and delivery_error == "none" else "DEGRADED"
    lines = [
        f"Trail: {trail_name}",
        f"  Status         : {'LOGGING' if is_logging else 'NOT LOGGING'} ({health})",
        f"  Latest delivery: {latest_delivery}",
        f"  Latest digest  : {latest_digest}",
        f"  Last error     : {latest_error}",
    ]
    return "\n".join(lines)


def list_trails(
    account_id: str | None = None,
    region: str | None = None,
) -> str:
    """
    List all CloudTrail trails visible from the given account and region.

    Args:
        account_id: AWS account ID to query.  Defaults to Lambda's account.
        region:     AWS region.  Defaults to Lambda execution region.
    """
    region = region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    client = _cloudtrail_client(region, account_id)

    try:
        resp = client.describe_trails(includeShadowTrails=True)
    except ClientError as exc:
        return f"Error listing trails: {exc.response['Error']['Message']}"

    trails = resp.get("trailList", [])
    if not trails:
        target = account_id or "current account"
        return f"No CloudTrail trails found in {target} / {region}."

    lines = [f"CloudTrail trails ({len(trails)}):\n"]
    for t in trails:
        name = t.get("Name", "?")
        home = t.get("HomeRegion", "?")
        multi = "multi-region" if t.get("IsMultiRegionTrail") else "single-region"
        org = "org-trail" if t.get("IsOrganizationTrail") else "account-trail"
        bucket = t.get("S3BucketName", "none")
        cw = t.get("CloudWatchLogsLogGroupArn", "none")
        lines.append(
            f"  {name}\n"
            f"    Home region: {home}  |  {multi}  |  {org}\n"
            f"    S3 bucket  : {bucket}\n"
            f"    CloudWatch : {cw}"
        )
    return "\n".join(lines)


def _describe_filters(
    event_name: str | None,
    username: str | None,
    resource_type: str | None,
    resource_name: str | None,
    access_key_id: str | None,
    read_only: str | None,
) -> str:
    parts = []
    if event_name:
        parts.append(f"event_name={event_name}")
    if username:
        parts.append(f"username={username}")
    if resource_type:
        parts.append(f"resource_type={resource_type}")
    if resource_name:
        parts.append(f"resource_name={resource_name}")
    if access_key_id:
        parts.append(f"access_key_id={access_key_id}")
    if read_only:
        parts.append(f"read_only={read_only}")
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------

_TOOL_DISPATCH: dict[str, Any] = {
    "lookup_cloudtrail_events": lookup_cloudtrail_events,
    "get_trail_status": get_trail_status,
    "list_trails": list_trails,
}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    CloudTrail tools Lambda handler for AgentSOAR AgentCore Gateway.

    Implements three read-only tools:
    - lookup_cloudtrail_events  (free LookupEvents API, 90-day history)
    - get_trail_status
    - list_trails

    Gateway input format:
        event: tool arguments passed directly from the AgentCore Gateway
        context.client_context.custom['bedrockAgentCoreToolName']: full tool name with target prefix
    """
    logger.info("Received event: %s", json.dumps(event, default=str))

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
            return {
                "error": (
                    f"This Lambda only supports {list(_TOOL_DISPATCH.keys())}, "
                    f"received: {tool_name}"
                )
            }

        func = _TOOL_DISPATCH[tool_name]

        if tool_name == "lookup_cloudtrail_events":
            result = func(
                start_time=event.get("start_time"),
                end_time=event.get("end_time"),
                event_name=event.get("event_name"),
                username=event.get("username"),
                resource_type=event.get("resource_type"),
                resource_name=event.get("resource_name"),
                access_key_id=event.get("access_key_id"),
                read_only=event.get("read_only"),
                account_id=event.get("account_id"),
                region=event.get("region"),
                max_results=int(event.get("max_results", 20)),
            )
        elif tool_name == "get_trail_status":
            result = func(
                trail_name=event.get("trail_name", "management-trail"),
                account_id=event.get("account_id"),
                region=event.get("region"),
            )
        elif tool_name == "list_trails":
            result = func(
                account_id=event.get("account_id"),
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
