"""Issuer and audience have one source: config/auth0.json.

The CDK HTTP JWT authorizer, the Lambda AUTH0_DOMAIN / AUTH0_AUDIENCE
environment, identity.issuer(), and the SPA env examples must name the same
tenant and the same API audience. A second hardcoded tenant is how
web/.env.example drifted onto a different Auth0 domain than the stack.
"""

import json
from pathlib import Path

import pytest

from amazai import identity as I

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = json.loads((ROOT / "config" / "auth0.json").read_text())


def test_canonical_file_names_the_live_tenant():
    domain = CANONICAL["domain"]
    audience = CANONICAL["audience"]
    assert domain == "dev-msijboy7a85k3chd.us.auth0.com"
    assert audience == "https://api.amazai.co"
    assert "://" not in domain and "/" not in domain
    assert audience.startswith("https://")


def test_identity_issuer_matches_the_file(monkeypatch):
    monkeypatch.setenv("AUTH0_DOMAIN", CANONICAL["domain"])
    monkeypatch.setenv("AUTH0_AUDIENCE", CANONICAL["audience"])
    assert I.domain() == CANONICAL["domain"]
    assert I.audience() == CANONICAL["audience"]
    assert I.issuer() == f"https://{CANONICAL['domain']}/"
    I.check_config()


def test_verify_refuses_a_missing_audience(monkeypatch):
    monkeypatch.setenv("AUTH0_DOMAIN", CANONICAL["domain"])
    monkeypatch.delenv("AUTH0_AUDIENCE", raising=False)
    with pytest.raises(I.AuthError):
        I.verify("not-a-token")


def test_verify_refuses_a_missing_domain(monkeypatch):
    monkeypatch.delenv("AUTH0_DOMAIN", raising=False)
    monkeypatch.setenv("AUTH0_AUDIENCE", CANONICAL["audience"])
    with pytest.raises(I.AuthError):
        I.verify("not-a-token")


def test_verify_refuses_a_domain_that_would_change_the_issuer(monkeypatch):
    """A scheme in AUTH0_DOMAIN makes identity.issuer() disagree with the
    API Gateway authorizer, which is https://<bare-host>/."""
    monkeypatch.setenv("AUTH0_DOMAIN", f"https://{CANONICAL['domain']}")
    monkeypatch.setenv("AUTH0_AUDIENCE", CANONICAL["audience"])
    with pytest.raises(I.AuthError):
        I.verify("not-a-token")


def test_cdk_reads_the_file_and_does_not_hardcode_another_tenant():
    bin_src = (ROOT / "infra" / "bin" / "amazai.ts").read_text()
    stack = (ROOT / "infra" / "lib" / "amazai-stack.ts").read_text()
    assert "config/auth0.json" in bin_src
    assert "auth0.com" not in bin_src
    assert "auth0.com" not in stack
    assert "HttpUserPoolAuthorizer" not in stack
    assert "aws-cdk-lib/aws-cognito" not in stack
    assert "AUTH0_DOMAIN: props.auth0Domain" in stack
    assert "AUTH0_AUDIENCE: props.auth0Audience" in stack
    assert "jwtAudience: [props.auth0Audience]" in stack
    assert "const auth0Issuer = `https://${props.auth0Domain}/`;" in stack


def test_spa_examples_match_the_file_and_do_not_name_cognito():
    for rel in (".env.example", "web/.env.example"):
        text = (ROOT / rel).read_text()
        assert f"VITE_AUTH0_DOMAIN={CANONICAL['domain']}" in text
        assert f"VITE_AUTH0_AUDIENCE={CANONICAL['audience']}" in text
        assert "VITE_AUTH0_CLIENT_ID=your-auth0-spa-client-id" in text
        assert "VITE_USER_POOL" not in text
        assert "Cognito" not in text
        assert "cognito" not in text
