import re
from pathlib import Path

import pytest

from collector.redact import redact
from collector.redact.egress_guard import scan
from collector.redact.pseudonym import EMAIL, IPV4, Pseudonymizer
from collector.redact.rules import RULES

CORPUS = Path(__file__).parent / "corpus"
SECRETS = [ln for ln in (CORPUS / "secrets.txt").read_text().splitlines() if ln.strip()]
BENIGN = [ln for ln in (CORPUS / "benign.txt").read_text().splitlines() if ln.strip()]
P = Pseudonymizer(b"test-key-acme", {"db-01": "h", "web-01": "h", "uplink-1": "link"})
MARKER = re.compile(r"<[a-z]+(?::[a-z0-9_]+)?>")

RAW_SECRETS = ["AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI", "ghp_x8Kq", "github_pat_11", "xoxb-2481", "eyJhbGci",
               "Sup3rS3cret", "9f8e7d6c5b4a", "4111 1111", "5500-0000", "hunter2pass", "MIIEvQIB", "Zq8@Lm",
               "Zx9Lq2Mv8Np4"]


@pytest.mark.parametrize("line", SECRETS)
def test_every_secret_line_is_scrubbed(line):
    r = redact(line, P)
    assert r.secrets >= 1, line
    for raw in RAW_SECRETS:
        assert raw not in r.template, (raw, r.template)
    # re-running every rule on the output finds nothing except our own markers
    masked = MARKER.sub("<M>", r.template)
    for rule in RULES:
        for m in rule.regex.finditer(masked):
            hit = m.group(rule.group)
            if hit.strip("\"'") == "<M>":
                continue
            assert rule.validator and not rule.validator(hit), (rule.name, hit, r.template)


@pytest.mark.parametrize("line", BENIGN)
def test_benign_lines_have_no_secret_hits(line):
    r = redact(line, P)
    assert r.secrets == 0, (line, r.template)


def test_identities_become_codes():
    r = redact("GET /api/cart 200 user=alice@acme.test from 10.0.1.23 via db-01 on uplink-1", P)
    assert "alice" not in r.template and "10.0.1.23" not in r.template and "db-01" not in r.template
    assert "<u:" in r.template and "<ip:" in r.template and "<h:" in r.template and "<link:" in r.template
    assert not EMAIL.search(MARKER.sub(" ", r.template)) and not IPV4.search(MARKER.sub(" ", r.template))


def test_same_user_same_code_and_tenant_scoped():
    a = Pseudonymizer(b"k1").code("u", "alice@acme.test")
    b = Pseudonymizer(b"k1").code("u", "ALICE@acme.test")
    c = Pseudonymizer(b"k2").code("u", "alice@acme.test")
    assert a == b != c


def test_sshd_user_and_ip_coded():
    r = redact("Failed password for invalid user admin from 203.0.113.7 port 52344 ssh2", P)
    assert "admin" not in r.template and "203.0.113.7" not in r.template
    assert r.template.startswith("Failed password for invalid user <u:")


def test_template_is_safe_and_capped():
    r = redact("weird {chars} & $vars ~ " + "x" * 300, P)
    assert len(r.template) <= 160
    assert not re.search(r"[{}&$~]", r.template)


def test_egress_guard_blocks_planted_secret_and_raw_identity():
    assert scan({"records": [{"template": "ok <secret:github_token> fine"}]}) is None
    assert scan({"records": [{"template": "POST /login token=<secret:github_token> user <u:7db913104dbe>"}]}) is None
    assert scan({"records": [{"template": "db password=<secret:password_kv> timeout=<num>s"}]}) is None
    assert scan({"records": [{"template": "leak AKIAIOSFODNN7EXAMPLE"}]}).startswith("aws_access_key")
    assert scan({"records": [{"template": "from 10.0.0.5"}]}).startswith("raw_ipv4")
    assert scan({"records": [{"template": "mail bob@acme.test"}]}).startswith("raw_email")
    # numbers are not strings -> a Luhn-valid buffer_bytes never false-blocks
    assert scan({"records": [{"buffer_bytes": 4111111111111111}]}) is None
