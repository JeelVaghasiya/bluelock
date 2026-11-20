import copy
import json
import math
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError

from trustline_schema import MAX_RECORDS, Payload, code, new_payload_id

FIX = Path(__file__).resolve().parent.parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_valid_fixture_passes():
    p = Payload.model_validate(load("payload_valid.json"))
    assert p.schema_ == "trustline.v1"
    assert len(p.records) == 4


@pytest.mark.parametrize(
    "name, needle",
    [
        ("payload_extra_field.json", "extra_forbidden"),
        ("payload_raw_hostname.json", "string_pattern_mismatch"),
        ("payload_tenant_spoof.json", "extra_forbidden"),
        ("payload_non_utc.json", "timestamp must be UTC"),
    ],
)
def test_bad_fixtures_rejected(name, needle):
    with pytest.raises(ValidationError) as e:
        Payload.model_validate(load(name))
    assert needle in str(e.value)


def test_nan_rejected():
    d = load("payload_valid.json")
    d["records"][0]["avg"] = math.nan
    with pytest.raises(ValidationError):
        Payload.model_validate(d)


def test_uuid4_rejected():
    d = load("payload_valid.json")
    d["payload_id"] = str(uuid.uuid4())
    with pytest.raises(ValidationError, match="UUIDv7"):
        Payload.model_validate(d)


def test_too_many_records_rejected():
    d = load("payload_valid.json")
    d["records"] = [copy.deepcopy(d["records"][0]) for _ in range(MAX_RECORDS + 1)]
    with pytest.raises(ValidationError):
        Payload.model_validate(d)


def test_template_charset_and_length():
    d = load("payload_valid.json")
    d["records"][2]["template"] = "x" * 161
    with pytest.raises(ValidationError):
        Payload.model_validate(d)
    d["records"][2]["template"] = "newline\nnot allowed"
    with pytest.raises(ValidationError):
        Payload.model_validate(d)


def test_roundtrip_identical():
    p = Payload.model_validate(load("payload_valid.json"))
    again = Payload.model_validate_json(p.to_json_bytes())
    assert again == p
    assert again.to_json_bytes() == p.to_json_bytes()
    assert b'"schema":"trustline.v1"' in p.to_json_bytes()


def test_hmac_code_deterministic_and_tenant_scoped():
    a1 = code("u", b"key-acme", "Alice@Acme.test")
    a2 = code("u", b"key-acme", "alice@acme.test")
    b = code("u", b"key-globex", "alice@acme.test")
    assert a1 == a2 and a1 != b
    assert a1.startswith("u:") and len(a1) == 14


def test_new_payload_id_is_v7():
    assert new_payload_id().version == 7
