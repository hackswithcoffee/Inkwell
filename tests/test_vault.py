"""The vault client, with the Keychain and HTTP calls stubbed."""
import pytest

from inkwell import vault


@pytest.fixture
def keychain(monkeypatch):
    items = {vault.ROLE_ID_ITEM: "rid", vault.SECRET_ID_ITEM: "sid"}
    monkeypatch.setattr(vault, "_keychain", items.get)
    return items


def test_reads_google_fields_with_an_approle_token(keychain, monkeypatch):
    seen = []

    def fake(method, path, token=None, body=None):
        seen.append((method, path, token, body))
        if path == "auth/approle/login":
            return {"auth": {"client_token": "t1"}}
        return {"data": {"data": {"token": "{}", "client_config": "{}"}}}

    monkeypatch.setattr(vault, "_request", fake)
    assert vault.read_google() == {"token": "{}", "client_config": "{}"}
    assert seen[0] == ("POST", "auth/approle/login", None, {"role_id": "rid", "secret_id": "sid"})
    assert seen[1][:3] == ("GET", "secret/data/apps/inkwell/google", "t1")


def test_nothing_stored_yet_reads_as_empty(keychain, monkeypatch):
    monkeypatch.setattr(vault, "_request", lambda m, p, token=None, body=None:
                        {"auth": {"client_token": "t"}} if p.startswith("auth/") else {})
    assert vault.read_google() == {}


def test_missing_keychain_items_mean_not_configured(monkeypatch):
    monkeypatch.setattr(vault, "_keychain", lambda item: None)
    assert vault.configured() is False
    with pytest.raises(vault.VaultError):
        vault.read_google()


def test_store_merges_rather_than_replacing(monkeypatch):
    """KV v2 put replaces the whole secret; storing the token must keep the client."""
    monkeypatch.setenv("BAO_TOKEN", "admin")
    written = {}

    def fake(method, path, token=None, body=None):
        assert token == "admin"
        if method == "GET":
            return {"data": {"data": {"client_config": "C"}}}
        written.update(body["data"])
        return {}

    monkeypatch.setattr(vault, "_request", fake)
    vault.store_google(token="T")
    assert written == {"client_config": "C", "token": "T"}


def test_store_refuses_without_an_admin_token(monkeypatch):
    monkeypatch.delenv("BAO_TOKEN", raising=False)
    with pytest.raises(vault.VaultError):
        vault.store_google(token="T")
