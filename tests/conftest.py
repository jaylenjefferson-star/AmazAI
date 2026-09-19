import os
import sys
from pathlib import Path

# The CDK packages `services/` as the Lambda asset root, so handlers import
# `amazai.*`. Mirror that here so tests exercise the real import path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))

os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")

import boto3  # noqa: E402
import pytest  # noqa: E402
from moto import mock_aws  # noqa: E402

from amazai.store import Store  # noqa: E402


def make_table():
    """The real table shape from the CDK stack, minus the capacity settings."""
    ddb = boto3.resource("dynamodb", region_name="us-west-2")
    return ddb.create_table(
        TableName="amazai",
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"},
                   {"AttributeName": "sk", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
            {"AttributeName": "gsi1pk", "AttributeType": "S"},
            {"AttributeName": "gsi1sk", "AttributeType": "S"},
            {"AttributeName": "gsi2pk", "AttributeType": "S"},
            {"AttributeName": "gsi2sk", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {"IndexName": "gsi1",
             "KeySchema": [{"AttributeName": "gsi1pk", "KeyType": "HASH"},
                           {"AttributeName": "gsi1sk", "KeyType": "RANGE"}],
             "Projection": {"ProjectionType": "ALL"}},
            {"IndexName": "gsi2",
             "KeySchema": [{"AttributeName": "gsi2pk", "KeyType": "HASH"},
                           {"AttributeName": "gsi2sk", "KeyType": "RANGE"}],
             "Projection": {"ProjectionType": "ALL"}},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture
def table():
    with mock_aws():
        yield make_table()


@pytest.fixture
def store(table):
    return Store("owner-a", table=table)


@pytest.fixture
def two_stores(table):
    """Two tenants on one table. Isolation is a property of the rows, not of
    separate infrastructure, so it has to be tested this way."""
    return Store("owner-a", table=table), Store("owner-b", table=table)
