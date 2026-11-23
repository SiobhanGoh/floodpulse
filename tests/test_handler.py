from decimal import Decimal

import pytest
from botocore.exceptions import ClientError

import handler
from conftest import FIXTURES

HTML = (FIXTURES / "selangor_sample.html").read_text()


class FakeTable:
    """Stands in for DynamoDB: remembers items and honours the
    'only if new' condition, so tests need no AWS account."""

    def __init__(self):
        self.items = {}

    def put_item(self, Item, ConditionExpression=None):
        key = (Item["station_key"], Item["observed_at"])
        if ConditionExpression and key in self.items:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
        self.items[key] = Item


def fetch_sample(state):
    return HTML


def test_first_run_saves_every_reading():
    table = FakeTable()
    summary = handler.lambda_handler({}, None, table=table, fetch=fetch_sample)
    assert summary["saved"] == 7
    assert summary["already_had"] == 0
    assert summary["states"]["SEL"]["skipped_rows"] == 1


def test_second_run_saves_nothing_new():
    table = FakeTable()
    handler.lambda_handler({}, None, table=table, fetch=fetch_sample)
    summary = handler.lambda_handler({}, None, table=table, fetch=fetch_sample)
    assert summary["saved"] == 0
    assert summary["already_had"] == 7
    assert len(table.items) == 7


def test_numbers_are_decimals_and_ttl_is_set():
    table = FakeTable()
    handler.lambda_handler({}, None, table=table, fetch=fetch_sample)
    item = next(iter(table.items.values()))
    assert isinstance(item["level_m"], Decimal)
    assert isinstance(item["expires_at"], int)


def test_failed_download_is_reported():
    def broken_fetch(state):
        raise TimeoutError("site did not answer")

    with pytest.raises(RuntimeError):
        handler.lambda_handler({}, None, table=FakeTable(), fetch=broken_fetch)
