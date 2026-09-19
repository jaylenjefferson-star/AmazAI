from amazai import redact
from amazai.redact import REDACTED


class TestSensitiveKeys:
    def test_authorization_header_value_removed(self):
        out, found = redact.redact({"headers": {"Authorization": "Bearer sk-live-abc123"}})
        assert out["headers"]["Authorization"] == REDACTED
        assert "headers.Authorization" in found

    def test_key_matching_is_case_insensitive(self):
        out, _ = redact.redact({"SET-COOKIE": "session=abc"})
        assert out["SET-COOKIE"] == REDACTED

    def test_mfa_and_password_keys_removed(self):
        out, _ = redact.redact({"password": "hunter2", "mfa_code": "123456"})
        assert out["password"] == REDACTED
        assert out["mfa_code"] == REDACTED

    def test_nested_structures_are_walked(self):
        out, found = redact.redact({"a": [{"b": {"cookie": "x=1"}}]})
        assert out["a"][0]["b"]["cookie"] == REDACTED
        assert "a[0].b.cookie" in found


class TestValuePatterns:
    def test_aws_access_key_scrubbed(self):
        out, found = redact.redact("key is AKIAIOSFODNN7EXAMPLE here")
        assert "AKIAIOSFODNN7EXAMPLE" not in out
        assert any("aws_access_key" in f for f in found)

    def test_sts_key_scrubbed(self):
        out, _ = redact.redact("ASIAIOSFODNN7EXAMPLE")
        assert "ASIA" not in out

    def test_github_token_scrubbed(self):
        out, _ = redact.redact("token ghp_abcdefghijklmnopqrstuvwxyz0123456789")
        assert "ghp_" not in out

    def test_slack_token_scrubbed(self):
        out, _ = redact.redact("xoxb-123456789012-abcdefghijkl")
        assert "xoxb-" not in out

    def test_jwt_scrubbed(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r"
        out, _ = redact.redact(jwt)
        assert "eyJhbGciOiJIUzI1NiJ9" not in out

    def test_private_key_header_scrubbed(self):
        out, _ = redact.redact("-----BEGIN RSA PRIVATE KEY-----\nMIIE...")
        assert "BEGIN RSA PRIVATE KEY" not in out

    def test_ordinary_text_is_untouched(self):
        text = "Fixed the flaky test in checkout_test.py; 20 tests pass."
        out, found = redact.redact(text)
        assert out == text
        assert found == []


class TestVaultReferences:
    def test_vault_reference_is_preserved(self):
        # The placeholder is not a secret. Preserving it is what shows the
        # token never entered the runtime -- redacting it would hide the proof.
        ref = "${arn:aws:bedrock-agentcore:us-west-2:123456789012:token-vault/default/apikeycredentialprovider/github}"
        out, found = redact.redact({"headers": {"x-custom": ref}})
        assert out["headers"]["x-custom"] == ref
        assert found == []

    def test_vault_reference_survives_alongside_a_real_secret(self):
        ref = "${arn:aws:bedrock-agentcore:us-west-2:1:token-vault/default/apikeycredentialprovider/gh}"
        out, found = redact.redact(f"{ref} and AKIAIOSFODNN7EXAMPLE")
        assert ref in out
        assert "AKIAIOSFODNN7EXAMPLE" not in out
        assert found


class TestStructurePreservation:
    def test_non_string_scalars_pass_through(self):
        out, _ = redact.redact({"count": 42, "ok": True, "none": None})
        assert out == {"count": 42, "ok": True, "none": None}

    def test_lists_stay_lists_and_tuples_stay_tuples(self):
        out, _ = redact.redact({"l": [1, 2], "t": (3, 4)})
        assert isinstance(out["l"], list)
        assert isinstance(out["t"], tuple)
