"""DynamoDB access layer.

Every read and write filters on `ownerId`. The Cognito JWT proves who you are;
this proves the row is yours. Today there is exactly one owner, which is
precisely why the check is cheap to add now and painful to retrofit later
(see the multi-user seam in docs/architecture/03-data-model.md).
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

import boto3
from boto3.dynamodb.conditions import Key

TABLE_NAME = os.environ.get("TABLE_NAME", "amazai")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_id(prefix: str = "") -> str:
    # Time-ordered so a lexicographic sort is also chronological.
    stamp = format(int(time.time() * 1000), "012x")
    return f"{prefix}{stamp}{uuid.uuid4().hex[:10]}"


def _floats_to_decimal(obj: Any) -> Any:
    """DynamoDB rejects float. Convert on the way in, not at every call site."""
    if isinstance(obj, float):
        return Decimal(str(round(obj, 6)))
    if isinstance(obj, dict):
        return {k: _floats_to_decimal(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_floats_to_decimal(v) for v in obj]
    return obj


def _decimals_to_native(obj: Any) -> Any:
    """Convert back on the way out so handlers hand plain JSON to the console."""
    if isinstance(obj, Decimal):
        f = float(obj)
        return int(f) if f.is_integer() else f
    if isinstance(obj, dict):
        return {k: _decimals_to_native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decimals_to_native(v) for v in obj]
    return obj


class NotFound(LookupError):
    pass


class Conflict(RuntimeError):
    """A conditional write lost. The caller must re-read, never force."""


class Store:
    def __init__(self, owner_id: str, table=None) -> None:
        self.owner_id = owner_id
        self._table = table or boto3.resource("dynamodb").Table(TABLE_NAME)

    # -- primitives ---------------------------------------------------------

    def put(self, item: dict, *, unique: bool = False) -> dict:
        item = dict(item)
        item["ownerId"] = self.owner_id
        item.setdefault("createdAt", now_iso())
        item["updatedAt"] = now_iso()
        kwargs: dict[str, Any] = {"Item": _floats_to_decimal(item)}
        if unique:
            kwargs["ConditionExpression"] = "attribute_not_exists(pk)"
        try:
            self._table.put_item(**kwargs)
        except self._table.meta.client.exceptions.ConditionalCheckFailedException as e:
            raise Conflict(f"{item['pk']}/{item['sk']} already exists") from e
        return _decimals_to_native(item)

    def get(self, pk: str, sk: str) -> dict:
        resp = self._table.get_item(Key={"pk": pk, "sk": sk})
        item = resp.get("Item")
        if not item:
            raise NotFound(f"{pk}/{sk}")
        item = _decimals_to_native(item)
        if item.get("ownerId") != self.owner_id:
            # Do not leak the existence of another owner's row.
            raise NotFound(f"{pk}/{sk}")
        return item

    def try_get(self, pk: str, sk: str) -> dict | None:
        try:
            return self.get(pk, sk)
        except NotFound:
            return None

    def update(self, pk: str, sk: str, changes: dict, *, expect: dict | None = None) -> dict:
        """Patch named attributes, optionally under a conditional expectation."""
        changes = dict(changes)
        changes["updatedAt"] = now_iso()
        names = {f"#a{i}": k for i, k in enumerate(changes)}
        values = {f":v{i}": v for i, v in enumerate(changes.values())}
        sets = ", ".join(f"#a{i} = :v{i}" for i in range(len(changes)))

        kwargs: dict[str, Any] = {
            "Key": {"pk": pk, "sk": sk},
            "UpdateExpression": f"SET {sets}",
            "ExpressionAttributeNames": names,
            "ExpressionAttributeValues": _floats_to_decimal(values),
            "ReturnValues": "ALL_NEW",
        }

        conditions = ["ownerId = :owner"]
        kwargs["ExpressionAttributeValues"][":owner"] = self.owner_id
        if expect:
            for i, (k, v) in enumerate(expect.items()):
                kwargs["ExpressionAttributeNames"][f"#e{i}"] = k
                kwargs["ExpressionAttributeValues"][f":e{i}"] = _floats_to_decimal(v)
                conditions.append(f"#e{i} = :e{i}")
        kwargs["ConditionExpression"] = " AND ".join(conditions)

        try:
            resp = self._table.update_item(**kwargs)
        except self._table.meta.client.exceptions.ConditionalCheckFailedException as e:
            raise Conflict(f"conditional update failed on {pk}/{sk}") from e
        return _decimals_to_native(resp["Attributes"])

    def query(self, pk: str, *, sk_prefix: str = "", limit: int = 100,
              ascending: bool = True) -> list[dict]:
        cond = Key("pk").eq(pk)
        if sk_prefix:
            cond = cond & Key("sk").begins_with(sk_prefix)
        resp = self._table.query(
            KeyConditionExpression=cond, Limit=limit, ScanIndexForward=ascending
        )
        return [i for i in _decimals_to_native(resp.get("Items", []))
                if i.get("ownerId") == self.owner_id]

    def query_index(self, index: str, pk_name: str, pk_value: str, *,
                    sk_name: str | None = None, sk_lt: str | None = None,
                    limit: int = 100) -> list[dict]:
        cond = Key(pk_name).eq(pk_value)
        if sk_name and sk_lt:
            cond = cond & Key(sk_name).lt(sk_lt)
        resp = self._table.query(
            IndexName=index, KeyConditionExpression=cond, Limit=limit
        )
        return [i for i in _decimals_to_native(resp.get("Items", []))
                if i.get("ownerId") == self.owner_id]

    def delete(self, pk: str, sk: str) -> None:
        self._table.delete_item(
            Key={"pk": pk, "sk": sk},
            ConditionExpression="ownerId = :owner",
            ExpressionAttributeValues={":owner": self.owner_id},
        )

    def batch_put(self, items: Iterable[dict]) -> int:
        count = 0
        with self._table.batch_writer() as batch:
            for item in items:
                item = dict(item)
                item["ownerId"] = self.owner_id
                item.setdefault("createdAt", now_iso())
                item["updatedAt"] = now_iso()
                batch.put_item(Item=_floats_to_decimal(item))
                count += 1
        return count

    # -- idempotency --------------------------------------------------------

    def claim(self, key: str, run_id: str, *, ttl_days: int = 7) -> str | None:
        """Claim an idempotency key.

        Returns None if this caller won the claim, or the existing run ID if
        the trigger already produced a run. EventBridge Scheduler is
        at-least-once; without this a doubled schedule does the work twice.
        """
        from amazai import keys as K
        item = {
            "pk": K.idempotency_pk(key), "sk": "META",
            "entity": "Idempotency", "runId": run_id,
            "ttl": int(time.time()) + ttl_days * 86400,
        }
        try:
            self.put(item, unique=True)
            return None
        except Conflict:
            existing = self.try_get(K.idempotency_pk(key), "META")
            return (existing or {}).get("runId", "unknown")
