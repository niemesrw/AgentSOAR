# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import gzip
import io
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# CloudTrail S3 delivery lag — events older than this are reliably in S3.
# Events newer than this fall back to LookupEvents in the current account.
_S3_LAG_MINUTES = 20

# Maximum files to download per query to avoid Lambda timeouts
_MAX_S3_FILES = 50


# ---------------------------------------------------------------------------
# S3-based centralized query (primary path)
# ---------------------------------------------------------------------------


def _s3_client() -> Any:
    return boto3.client("s3")


def _list_account_prefixes(bucket: str, org_id: str | None) -> list[str]:
    """
    Return all account IDs present in the centralized bucket.

    Org trails use two distinct prefix structures:
      - Management account:  AWSLogs/{account_id}/CloudTrail/...
      - Member accounts:     AWSLogs/{org_id}/{account_id}/CloudTrail/...
    """
    s3 = _s3_client()
    accounts: set[str] = set()

    # Direct prefixes (management account)
    result = s3.list_objects_v2(Bucket=bucket, Prefix="AWSLogs/", Delimiter="/")
    for p in result.get("CommonPrefixes", []):
        segment = p["Prefix"].rstrip("/").split("/")[-1]
        if segment.isdigit():  # account ID, not org ID
            accounts.add(segment)

    # Org-prefixed member accounts
    if org_id:
        result = s3.list_objects_v2(
            Bucket=bucket, Prefix=f"AWSLogs/{org_id}/", Delimiter="/"
        )
        for p in result.get("CommonPrefixes", []):
            segment = p["Prefix"].rstrip("/").split("/")[-1]
            if segment.isdigit():
                accounts.add(segment)

    return list(accounts)


def _s3_prefixes_for_account(
    account_id: str,
    org_id: str | None,
    management_account_id: str | None,
    region: str,
    dt: datetime,
) -> list[str]:
    """
    Return candidate S3 key prefixes for a given account/region/date.

    Management account logs land under both the direct and org-prefixed paths.
    Member accounts only land under the org-prefixed path.
    """
    date_path = f"{dt.year:04d}/{dt.month:02d}/{dt.day:02d}/"
    prefixes = []

    # Direct path: used by the management account (trail owner)
    if account_id == management_account_id or not org_id:
        prefixes.append(f"AWSLogs/{account_id}/CloudTrail/{region}/{date_path}")

    # Org path: used by all org member accounts (including management account)
    if org_id:
        prefixes.append(
            f"AWSLogs/{org_id}/{account_id}/CloudTrail/{region}/{date_path}"
        )

    return prefixes


def _list_keys_for_window(
    bucket: str,
    account_ids: list[str],
    regions: list[str],
    start_dt: datetime,
    end_dt: datetime,
    org_id: str | None = None,
    management_account_id: str | None = None,
) -> list[str]:
    """List all S3 keys that could contain events in the given window."""
    s3 = _s3_client()
    keys: list[str] = []

    current = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    dates: list[datetime] = []
    while current <= end_dt:
        dates.append(current)
        current += timedelta(days=1)

    for account_id in account_ids:
        for region in regions:
            for dt in dates:
                prefixes = _s3_prefixes_for_account(
                    account_id, org_id, management_account_id, region, dt
                )
                for prefix in prefixes:
                    paginator = s3.get_paginator("list_objects_v2")
                    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                        for obj in page.get("Contents", []):
                            keys.append(obj["Key"])
                            if len(keys) >= _MAX_S3_FILES:
                                logger.warning(
                                    "Reached %d file limit; results may be truncated",
                                    _MAX_S3_FILES,
                                )
                                return keys
    return keys


def _parse_s3_file(bucket: str, key: str) -> list[dict[str, Any]]:
    """Download and parse a single gzipped CloudTrail log file."""
    s3 = _s3_client()
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        compressed = obj["Body"].read()
        with gzip.open(io.BytesIO(compressed), "rt", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("Records", [])
    except Exception as exc:
        logger.warning("Failed to parse %s: %s", key, exc)
        return []


def _event_matches(
    record: dict[str, Any],
    start_dt: datetime,
    end_dt: datetime,
    event_name: str | None,
    username: str | None,
    resource_type: str | None,
    resource_name: str | None,
    access_key_id: str | None,
    read_only: str | None,
) -> bool:
    """Return True if a CloudTrail record matches all active filters."""
    # Time filter
    event_time_str = record.get("eventTime", "")
    try:
        event_time = datetime.fromisoformat(event_time_str.replace("Z", "+00:00"))
    except ValueError:
        return False
    if not (start_dt <= event_time <= end_dt):
        return False

    if event_name and record.get("eventName") != event_name:
        return False

    if username:
        identity = record.get("userIdentity", {})
        record_user = (
            identity.get("userName")
            or identity.get("sessionContext", {})
            .get("sessionIssuer", {})
            .get("userName")
            or identity.get("type", "")
        )
        if username.lower() not in record_user.lower():
            return False

    if access_key_id:
        identity = record.get("userIdentity", {})
        if identity.get("accessKeyId") != access_key_id:
            return False

    if read_only is not None:
        expected = read_only.lower() == "true"
        if record.get("readOnly") != expected:
            return False

    if resource_type or resource_name:
        resources = record.get("resources", [])
        if not resources:
            return False
        match = False
        for r in resources:
            type_ok = (
                not resource_type or resource_type.lower() in r.get("type", "").lower()
            )
            name_ok = (
                not resource_name or resource_name.lower() in r.get("ARN", "").lower()
            )
            if type_ok and name_ok:
                match = True
                break
        if not match:
            return False

    return True


def _format_record(record: dict[str, Any]) -> str:
    """Format a single CloudTrail record as a readable line."""
    event_time = record.get("eventTime", "?")
    event_name = record.get("eventName", "?")
    source = record.get("eventSource", "?")
    region = record.get("awsRegion", "?")
    account = record.get(
        "recipientAccountId", record.get("userIdentity", {}).get("accountId", "?")
    )
    identity = record.get("userIdentity", {})
    username = (
        identity.get("userName")
        or identity.get("sessionContext", {}).get("sessionIssuer", {}).get("userName")
        or identity.get("type", "?")
    )
    access_key = identity.get("accessKeyId", "")
    event_id = record.get("eventID", "?")
    resources = record.get("resources", [])
    resource_strs = [
        f"{r.get('type', '?')}:{r.get('ARN', '?').split(':')[-1]}"
        for r in resources[:2]
    ]
    resource_text = ", ".join(resource_strs) if resource_strs else "—"
    key_text = f" key={access_key}" if access_key else ""
    return (
        f"  {event_time}  {event_name:<35} user={username:<25} acct={account} region={region}\n"
        f"    src={source}{key_text}  event_id={event_id}  resources=[{resource_text}]"
    )


def _query_s3(
    bucket: str,
    account_ids: list[str],
    regions: list[str],
    start_dt: datetime,
    end_dt: datetime,
    max_results: int,
    org_id: str | None = None,
    management_account_id: str | None = None,
    **filters: Any,
) -> list[dict[str, Any]]:
    """Fetch and filter CloudTrail records from the centralized S3 bucket."""
    keys = _list_keys_for_window(
        bucket, account_ids, regions, start_dt, end_dt, org_id, management_account_id
    )
    if not keys:
        return []

    all_records: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_parse_s3_file, bucket, key): key for key in keys}
        for future in as_completed(futures):
            records = future.result()
            for record in records:
                if _event_matches(record, start_dt, end_dt, **filters):
                    all_records.append(record)
                    if len(all_records) >= max_results * 3:
                        # Over-fetch so we can sort and trim
                        break

    # Sort descending by event time, return top N
    all_records.sort(key=lambda r: r.get("eventTime", ""), reverse=True)
    return all_records[:max_results]


# ---------------------------------------------------------------------------
# LookupEvents fallback for the trailing ~20-minute window
# ---------------------------------------------------------------------------


def _query_recent_lookupevents(
    region: str,
    start_dt: datetime,
    end_dt: datetime,
    max_results: int,
    event_name: str | None,
    username: str | None,
    access_key_id: str | None,
    read_only: str | None,
) -> list[dict[str, Any]]:
    """
    Query LookupEvents in the current account for events not yet in S3.
    Returns normalised dicts compatible with _format_record.
    """
    client = boto3.client("cloudtrail", region_name=region)
    lookup_attrs: list[dict[str, str]] = []
    if event_name:
        lookup_attrs.append({"AttributeKey": "EventName", "AttributeValue": event_name})
    if username:
        lookup_attrs.append({"AttributeKey": "Username", "AttributeValue": username})
    if access_key_id:
        lookup_attrs.append(
            {"AttributeKey": "AccessKeyId", "AttributeValue": access_key_id}
        )
    if read_only:
        lookup_attrs.append(
            {"AttributeKey": "ReadOnly", "AttributeValue": read_only.lower()}
        )

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
        logger.warning("LookupEvents fallback failed: %s", exc)
        return []

    records = []
    for ev in resp.get("Events", []):
        # Normalise LookupEvents response to CloudTrail record format
        ct_event = {}
        if ev.get("CloudTrailEvent"):
            try:
                ct_event = json.loads(ev["CloudTrailEvent"])
            except Exception:
                pass
        if not ct_event:
            ct_event = {
                "eventTime": ev.get("EventTime", "").isoformat()
                if hasattr(ev.get("EventTime"), "isoformat")
                else str(ev.get("EventTime", "")),
                "eventName": ev.get("EventName", "?"),
                "eventSource": ev.get("EventSource", "?"),
                "eventID": ev.get("EventId", "?"),
                "userIdentity": {"userName": ev.get("Username", "?")},
                "resources": [
                    {
                        "ARN": r.get("ResourceName", ""),
                        "type": r.get("ResourceType", ""),
                    }
                    for r in ev.get("Resources", [])
                ],
            }
        records.append(ct_event)
    return records


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
    Query CloudTrail management events across all org accounts.

    Primary path: reads from the centralized S3 bucket (all accounts, unlimited history).
    Fallback: uses LookupEvents for events in the last ~20 minutes not yet delivered to S3
    (current account only).

    Args:
        start_time:    ISO-8601 UTC (e.g. "2026-04-01T00:00:00Z"). Defaults to 24 h ago.
        end_time:      ISO-8601 UTC. Defaults to now.
        event_name:    API name to filter on (e.g. "DescribeStacks", "AssumeRole").
        username:      IAM username, role session name, or "Root".
        resource_type: AWS resource type (e.g. "AWS::S3::Bucket").
        resource_name: Resource ARN or name substring.
        access_key_id: Access key ID (AKIA... or ASIA...).
        read_only:     "true" or "false".
        account_id:    Specific account to query; omit to search all org accounts.
        region:        AWS region to query. Defaults to Lambda execution region.
        max_results:   Events to return (1-50, default 20).
    """
    region = region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    max_results = max(1, min(50, max_results))
    bucket = os.environ.get("CLOUDTRAIL_LOGS_BUCKET", "")
    org_id = os.environ.get("CLOUDTRAIL_ORG_ID")
    management_account_id = os.environ.get("CLOUDTRAIL_MANAGEMENT_ACCOUNT")

    now = datetime.now(timezone.utc)
    end_dt = (
        datetime.fromisoformat(end_time.replace("Z", "+00:00")) if end_time else now
    )
    start_dt = (
        datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        if start_time
        else end_dt - timedelta(hours=24)
    )

    # Determine which accounts and regions to scan
    if account_id:
        account_ids = [account_id]
    elif bucket:
        try:
            account_ids = _list_account_prefixes(bucket, org_id)
        except Exception as exc:
            logger.warning("Could not list account prefixes: %s", exc)
            account_ids = []
    else:
        account_ids = []

    regions = [region]
    filters = dict(
        event_name=event_name,
        username=username,
        resource_type=resource_type,
        resource_name=resource_name,
        access_key_id=access_key_id,
        read_only=read_only,
    )

    all_records: list[dict[str, Any]] = []
    sources: list[str] = []

    # --- S3 path: events older than the delivery lag ---
    s3_end = min(end_dt, now - timedelta(minutes=_S3_LAG_MINUTES))
    if bucket and account_ids and start_dt < s3_end:
        try:
            s3_records = _query_s3(
                bucket,
                account_ids,
                regions,
                start_dt,
                s3_end,
                max_results,
                org_id=org_id,
                management_account_id=management_account_id,
                **filters,
            )
            all_records.extend(s3_records)
            sources.append(
                f"S3 ({len(s3_records)} events from {len(account_ids)} account(s))"
            )
        except Exception as exc:
            logger.error("S3 query failed: %s", exc)
            sources.append(f"S3 (error: {exc})")
    elif not bucket:
        sources.append("S3 (CLOUDTRAIL_LOGS_BUCKET not configured)")

    # --- LookupEvents fallback: trailing window not yet in S3 ---
    recent_start = max(start_dt, now - timedelta(minutes=_S3_LAG_MINUTES))
    if recent_start < end_dt:
        recent_records = _query_recent_lookupevents(
            region,
            recent_start,
            end_dt,
            max_results,
            event_name,
            username,
            access_key_id,
            read_only,
        )
        # Deduplicate by eventID
        existing_ids = {r.get("eventID") for r in all_records}
        new_records = [
            r for r in recent_records if r.get("eventID") not in existing_ids
        ]
        all_records.extend(new_records)
        sources.append(
            f"LookupEvents last {_S3_LAG_MINUTES}m ({len(new_records)} event(s), current account only)"
        )

    if not all_records:
        target = account_id or "all org accounts"
        return (
            f"No CloudTrail events found in {target} / {region} "
            f"between {start_dt.strftime('%Y-%m-%dT%H:%MZ')} and {end_dt.strftime('%Y-%m-%dT%H:%MZ')}.\n"
            f"Sources checked: {', '.join(sources)}"
        )

    all_records.sort(key=lambda r: r.get("eventTime", ""), reverse=True)
    all_records = all_records[:max_results]

    target = account_id or "all org accounts"
    lines = [
        f"CloudTrail events — {target} / {region} "
        f"({start_dt.strftime('%Y-%m-%dT%H:%MZ')} → {end_dt.strftime('%Y-%m-%dT%H:%MZ')}): "
        f"{len(all_records)} result(s)",
        f"Sources: {', '.join(sources)}",
        "",
    ]
    for record in all_records:
        lines.append(_format_record(record))

    return "\n".join(lines)


def get_trail_status(
    trail_name: str = "management-trail",
    account_id: str | None = None,
    region: str | None = None,
) -> str:
    """
    Return the logging status and health of a CloudTrail trail.

    The org-wide trail is visible from any member account via shadow trails.
    No cross-account role assumption required.
    """
    region = region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    client = boto3.client("cloudtrail", region_name=region)

    # For org trails, the ARN includes the management account ID
    name = trail_name
    if account_id and not trail_name.startswith("arn:"):
        name = f"arn:aws:cloudtrail:{region}:{account_id}:trail/{trail_name}"

    try:
        status = client.get_trail_status(Name=name)
    except ClientError as exc:
        return f"Error getting trail status for '{trail_name}': {exc.response['Error']['Message']}"

    is_logging = status.get("IsLogging", False)
    latest_delivery = status.get("LatestDeliveryTime", "never")
    latest_digest = status.get("LatestDigestDeliveryTime", "never")
    delivery_error = status.get("LatestDeliveryError") or "none"

    if hasattr(latest_delivery, "strftime"):
        latest_delivery = latest_delivery.strftime("%Y-%m-%dT%H:%M:%SZ")
    if hasattr(latest_digest, "strftime"):
        latest_digest = latest_digest.strftime("%Y-%m-%dT%H:%M:%SZ")

    health = "HEALTHY" if is_logging and delivery_error == "none" else "DEGRADED"
    return "\n".join(
        [
            f"Trail: {trail_name}",
            f"  Status         : {'LOGGING' if is_logging else 'NOT LOGGING'} ({health})",
            f"  Latest delivery: {latest_delivery}",
            f"  Latest digest  : {latest_digest}",
            f"  Last error     : {delivery_error}",
        ]
    )


def list_trails(
    account_id: str | None = None,
    region: str | None = None,
) -> str:
    """
    List all CloudTrail trails visible from the current account, including org shadow trails.
    """
    region = region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    client = boto3.client("cloudtrail", region_name=region)

    try:
        resp = client.describe_trails(includeShadowTrails=True)
    except ClientError as exc:
        return f"Error listing trails: {exc.response['Error']['Message']}"

    trails = resp.get("trailList", [])
    if not trails:
        return f"No CloudTrail trails found in {region}."

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
    - lookup_cloudtrail_events  (S3-based, all org accounts; LookupEvents fallback for last 20 min)
    - get_trail_status
    - list_trails
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
