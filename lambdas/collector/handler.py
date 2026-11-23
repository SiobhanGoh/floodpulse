"""The collector Lambda: every 15 minutes, read the JPS tables and save
new readings to DynamoDB.

AWS calls lambda_handler(event, context). Everything that talks to AWS
lives in this file; the reading and cleaning logic lives in jps.py.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

import jps

TABLE_NAME = os.environ.get("TABLE_NAME", "")
STATES = [s.strip() for s in os.environ.get("STATES", "SEL").split(",") if s.strip()]
KEEP_FOR_DAYS = int(os.environ.get("KEEP_FOR_DAYS", "90"))

_table = None


def get_table():
    """Connect to the DynamoDB table once and reuse it on warm starts."""
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(TABLE_NAME)
    return _table


def to_dynamo_item(reading: jps.Reading) -> dict:
    """DynamoDB stores numbers as Decimal, not float. It also needs the
    TTL attribute: a Unix time (seconds) after which the row is deleted."""
    item = {
        k: Decimal(str(v)) if isinstance(v, float) else v
        for k, v in reading.to_item().items()
    }
    observed = datetime.fromisoformat(reading.observed_at)
    item["expires_at"] = int((observed + timedelta(days=KEEP_FOR_DAYS)).timestamp())
    return item


def save_if_new(table, reading: jps.Reading) -> bool:
    """Write the reading only if this station + time isn't saved yet.

    Stations update every 15-60 minutes, so most runs see some readings
    they've already saved. The condition makes re-runs safe: nothing is
    written twice.
    """
    try:
        table.put_item(
            Item=to_dynamo_item(reading),
            ConditionExpression="attribute_not_exists(station_key)",
        )
        return True
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def lambda_handler(event, context, table=None, fetch=jps.fetch_state_page):
    table = table or get_table()
    now = datetime.now(timezone.utc)
    summary = {"states": {}, "saved": 0, "already_had": 0, "failed_states": []}

    for state in STATES:
        try:
            html = fetch(state)
        except Exception as error:  # one slow state shouldn't stop the others
            print(json.dumps({"level": "ERROR", "state": state, "error": str(error)}))
            summary["failed_states"].append(state)
            continue

        readings, skipped = jps.read_state(html, state, now)
        saved = sum(save_if_new(table, r) for r in readings)

        quality = {}
        for r in readings:
            quality[r.quality] = quality.get(r.quality, 0) + 1

        summary["states"][state] = {
            "stations": len(readings),
            "saved": saved,
            "skipped_rows": skipped,
            "quality": quality,
        }
        summary["saved"] += saved
        summary["already_had"] += len(readings) - saved

    # One JSON line per run: easy to search later in CloudWatch Logs.
    print(json.dumps({"level": "INFO", "summary": summary}))

    if summary["failed_states"] and len(summary["failed_states"]) == len(STATES):
        raise RuntimeError("Every state failed to download")  # makes the alarm fire
    return summary
