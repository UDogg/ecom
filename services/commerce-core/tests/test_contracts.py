"""Producer side of docs/contracts/events: what commerce-core writes to the outbox."""

import json
import os
from pathlib import Path

import pytest

from outbox.models import OutboxEvent

from .conftest import checkout

pytestmark = pytest.mark.django_db

jsonschema = pytest.importorskip("jsonschema")
def _contracts_dir():
    if os.environ.get("CONTRACTS_DIR"):
        return Path(os.environ["CONTRACTS_DIR"])
    parents = Path(__file__).resolve().parents
    return parents[3] / "docs/contracts/events" if len(parents) > 3 else Path("/nonexistent")


CONTRACTS = _contracts_dir()
if not CONTRACTS.exists():
    pytest.skip("contracts directory not available (container build context)", allow_module_level=True)


def schema(name):
    return json.loads((CONTRACTS / name).read_text())


def test_outbox_events_match_their_contracts(customer, make_product):
    product = make_product()
    checkout(customer, [(product, 2)])
    checker = jsonschema.FormatChecker()

    data_schemas = {"order.placed.v1": "order.placed.v1.schema.json",
                    "inventory.changed.v1": "inventory.changed.v1.schema.json"}
    seen = set()
    for event in OutboxEvent.objects.all():
        jsonschema.validate(event.envelope(), schema("envelope.v1.schema.json"), format_checker=checker)
        if event.event_type in data_schemas:
            jsonschema.validate(event.payload, schema(data_schemas[event.event_type]), format_checker=checker)
            seen.add(event.event_type)
    assert seen == set(data_schemas)
