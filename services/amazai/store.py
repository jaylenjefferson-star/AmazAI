"""DynamoDB access layer.

Every read and write filters on `ownerId`. The Auth0 access token proves who you are;
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
from typing import Any, Callable, Iterable

import boto3
from boto3.dynamodb.conditions import Key

TABLE_NAME = os.environ.get("TABLE_NAME", "amazai")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_id(prefix: str = "") -> str:
    # Time-ordered so a lexicographic sort is also chronological.
    stamp = format(int(time.time() * 1000), "012x")
    return f"{prefix}{stamp}{uuid.uuid4().hex[:10]}"


def ordered_suffix() -> str:
    """A sort-key suffix that is both unique and chronological.

    Append-only trails key on `PREFIX#<iso second>#<suffix>`, and the ISO
    stamp only resolves to the second — so within one second the suffix is
    what orders the rows, and it has to do two jobs at once.

    Two ways of getting this wrong were both live in this repository:

    - `new_id()[:8]` is not unique. The first twelve characters of new_id are
      a millisecond timestamp in hex, so a prefix of it is identical for
      hours. Two rows written in the same second got the same key and the
      second silently overwrote the first.
    - A purely random suffix is unique but unordered, so three events one
      second apart replayed in an arbitrary order — which for an audit trail
      is its own kind of wrong answer.

    The full `new_id()` is both: millisecond-ordered, then random.
    """
    return new_id()


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


def discover_owner_ids(table=None) -> list[str]:
    """Every distinct owner with at least one Bot, read with no owner filter.

    The one place `ownerId` is deliberately *not* enforced. Everywhere else a
    caller already knows who it is (a request's principal, a run's own
    `ownerId`) and `Store` exists to hold it to that. A scheduled sweep has no
    request to read one from, so it has to find out who exists before it can
    open a `Store` for any of them -- see `services/handlers/sweeper.py`.
    Scoped to the `AGENTS` index because every real owner has at least one
    Bot; an owner with none is not yet a tenant a sweep needs to protect.
    """
    tbl = table or boto3.resource("dynamodb").Table(TABLE_NAME)
    owners: set[str] = set()
    start = None
    while True:
        request: dict[str, Any] = {
            "IndexName": "gsi1",
            "KeyConditionExpression": Key("gsi1pk").eq("AGENTS"),
        }
        if start:
            request["ExclusiveStartKey"] = start
        resp = tbl.query(**request)
        owners.update(raw["ownerId"] for raw in resp.get("Items", []) if raw.get("ownerId"))
        start = resp.get("LastEvaluatedKey")
        if not start:
            break
    return sorted(owners)


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

    def get(self, pk: str, sk: str, *, consistent: bool = False) -> dict:
        kwargs: dict[str, Any] = {"Key": {"pk": pk, "sk": sk}}
        if consistent:
            kwargs["ConsistentRead"] = True
        resp = self._table.get_item(**kwargs)
        item = resp.get("Item")
        if not item:
            raise NotFound(f"{pk}/{sk}")
        item = _decimals_to_native(item)
        if item.get("ownerId") != self.owner_id:
            # Do not leak the existence of another owner's row.
            raise NotFound(f"{pk}/{sk}")
        return item

    def try_get(self, pk: str, sk: str, *, consistent: bool = False) -> dict | None:
        try:
            return self.get(pk, sk, consistent=consistent)
        except NotFound:
            return None

    def update(self, pk: str, sk: str, changes: dict, *, expect: dict | None = None,
               expect_absent_or_null: tuple[str, ...] = ()) -> dict:
        """Patch named attributes under optional conditional expectations.

        `expect_absent_or_null` is the set-once primitive for migration fields:
        a row written before the field existed and a new row that stores it as
        DynamoDB NULL are both unclaimed. Once any worker writes a value, every
        loser gets `Conflict` and must re-read the winner rather than overwrite
        it.
        """
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
        for i, key in enumerate(expect_absent_or_null):
            alias = f"#n{i}"
            null = f":null{i}"
            kwargs["ExpressionAttributeNames"][alias] = key
            kwargs["ExpressionAttributeValues"][null] = None
            conditions.append(f"(attribute_not_exists({alias}) OR {alias} = {null})")
        kwargs["ConditionExpression"] = " AND ".join(conditions)

        try:
            resp = self._table.update_item(**kwargs)
        except self._table.meta.client.exceptions.ConditionalCheckFailedException as e:
            raise Conflict(f"conditional update failed on {pk}/{sk}") from e
        return _decimals_to_native(resp["Attributes"])

    def increment(self, pk: str, sk: str, field: str, delta: int) -> int:
        """Atomically add `delta` to a numeric attribute; return its new value.

        DynamoDB's `ADD` is server-side and linearizable per item: concurrent
        increments on the same row are serialized by the service itself, so
        whichever caller's own call is the one that lands a counter on a
        particular number (zero, for a fan-in "every child has reported"
        counter) is unambiguous -- no separate claim/lock, and no window
        where two callers could both believe they were last. `update`'s
        `SET`-based conditional writes cannot express this: computing a new
        value from a value this client already read and writing it back with
        `SET` is a read-modify-write a concurrent caller can race and undo.
        """
        resp = self._table.update_item(
            Key={"pk": pk, "sk": sk},
            UpdateExpression="ADD #f :d SET updatedAt = :u",
            ExpressionAttributeNames={"#f": field},
            ExpressionAttributeValues={":d": delta, ":u": now_iso(), ":owner": self.owner_id},
            ConditionExpression="ownerId = :owner",
            ReturnValues="UPDATED_NEW",
        )
        return int(resp["Attributes"][field])

    def query(self, pk: str, *, sk_prefix: str = "", limit: int = 100,
              ascending: bool = True, consistent: bool = False,
              sk_between: tuple[str, str] | None = None) -> list[dict]:
        """Rows in one partition, optionally narrowed by sort key.

        `sk_between` is an inclusive range, used instead of `sk_prefix` when
        *which* rows matter more than how many. DynamoDB applies `Limit` before
        anything this client can filter, so a caller that needs a particular kind
        of row has to narrow the query itself rather than read a page and pick
        through it -- see `collab._messages_for_context`, where reading a fixed
        page and filtering afterwards let unrelated rows crowd out the ones a
        ceiling was counting.
        """
        cond = Key("pk").eq(pk)
        if sk_between:
            cond = cond & Key("sk").between(*sk_between)
        elif sk_prefix:
            cond = cond & Key("sk").begins_with(sk_prefix)
        resp = self._table.query(
            KeyConditionExpression=cond, Limit=limit, ScanIndexForward=ascending,
            ConsistentRead=consistent,
        )
        return [i for i in _decimals_to_native(resp.get("Items", []))
                if i.get("ownerId") == self.owner_id]

    def query_index(self, index: str, pk_name: str, pk_value: str, *,
                    sk_name: str | None = None, sk_lt: str | None = None,
                    limit: int = 100,
                    predicate: Callable[[dict], bool] | None = None) -> list[dict]:
        """Return up to `limit` owned rows, paging past rows that do not qualify.

        GSI partitions are currently global labels such as `AGENTS`. DynamoDB
        applies its page Limit before this client can enforce `ownerId` or an
        active-only projection, so a one-page query lets another owner or old
        archived rows crowd the current owner's active Bots out of the result.
        Pagination is therefore part of the ownership/correctness boundary, not
        an optimization.
        """
        cond = Key(pk_name).eq(pk_value)
        if sk_name and sk_lt:
            cond = cond & Key(sk_name).lt(sk_lt)
        rows: list[dict] = []
        start = None
        while len(rows) < limit:
            request: dict[str, Any] = {
                "IndexName": index,
                "KeyConditionExpression": cond,
                "Limit": max(1, limit),
            }
            if start:
                request["ExclusiveStartKey"] = start
            resp = self._table.query(**request)
            for raw in resp.get("Items", []):
                item = _decimals_to_native(raw)
                if item.get("ownerId") != self.owner_id:
                    continue
                if predicate and not predicate(item):
                    continue
                rows.append(item)
                if len(rows) >= limit:
                    break
            next_start = resp.get("LastEvaluatedKey")
            if not next_start or next_start == start:
                break
            start = next_start
        return rows[:limit]

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

    # -- transactions -------------------------------------------------------

    def transact_put(self, items: list[dict], *, unique_pks: bool = True) -> list[dict]:
        """Write several rows or none of them.

        Creating an agent writes its identity, its grants, its memory
        namespace and its starter thread. Those are one fact, not four, and a
        crash between them leaves an agent that exists but cannot be used —
        the specific failure this avoids.

        DynamoDB caps a transaction at 100 items; a caller that could exceed
        that is a caller whose unit of work is wrong, so this refuses rather
        than silently splitting into two non-atomic halves.
        """
        if not items:
            return []
        if len(items) > 100:
            raise ValueError(
                f"transaction of {len(items)} items exceeds DynamoDB's limit of 100"
            )

        prepared = []
        for item in items:
            item = dict(item)
            item["ownerId"] = self.owner_id
            item.setdefault("createdAt", now_iso())
            item["updatedAt"] = now_iso()
            prepared.append(item)

        put = []
        for item in prepared:
            entry: dict[str, Any] = {
                "TableName": self._table.name,
                "Item": _floats_to_decimal(item),
            }
            if unique_pks:
                entry["ConditionExpression"] = "attribute_not_exists(pk) AND attribute_not_exists(sk)"
            put.append({"Put": entry})

        client = self._table.meta.client
        try:
            client.transact_write_items(TransactItems=put)
        except client.exceptions.TransactionCanceledException as e:
            reasons = [r.get("Code") for r in e.response.get("CancellationReasons", [])]
            raise Conflict(f"transaction cancelled: {reasons}") from e
        return [_decimals_to_native(i) for i in prepared]

    def transact_delete(self, keys: list[tuple[str, str]]) -> None:
        """Undo a transact_put. Used only on a provisioning rollback, where
        leaving the rows behind would mean a half-created agent."""
        if not keys:
            return
        client = self._table.meta.client
        client.transact_write_items(TransactItems=[
            {"Delete": {
                "TableName": self._table.name,
                "Key": {"pk": pk, "sk": sk},
                "ConditionExpression": "ownerId = :owner",
                "ExpressionAttributeValues": {":owner": self.owner_id},
            }} for pk, sk in keys
        ])

    # -- idempotency --------------------------------------------------------

    def claim(self, key: str, value: str, *, ttl_days: int = 7,
              field: str = "runId") -> str | None:
        """Claim an idempotency key.

        Returns None if this caller won the claim, or the value the first
        caller stored if the key was already taken. EventBridge Scheduler is
        at-least-once; without this a doubled schedule does the work twice.

        `field` names what is being made idempotent — a run for a schedule, an
        agent for a create. The retried caller gets back the identifier the
        first attempt produced, so a duplicate request returns the original
        result instead of a second object.
        """
        from amazai import keys as K
        item = {
            "pk": K.idempotency_pk(key), "sk": "META",
            "entity": "Idempotency", field: value,
            "ttl": int(time.time()) + ttl_days * 86400,
        }
        try:
            self.put(item, unique=True)
            return None
        except Conflict:
            existing = self.try_get(K.idempotency_pk(key), "META")
            return (existing or {}).get(field, "unknown")
