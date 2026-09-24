"""Secrets from the baobox OpenBao vault — nothing secret is kept on disk.

The Google OAuth client and sign-in live in KV v2 at `secret/apps/inkwell/google`
(fields `client_config` and `token`, each a JSON string). The pipeline reads them
with its own AppRole, `inkwell`, whose policy `inkwell-ro` can read that one path
and nothing else. The AppRole's role_id and secret_id sit in the macOS login
Keychain, where a launchd job running in the user's session can read them.

Writing needs more than the pipeline is given: `store_google` is only used by
the one-time `python -m inkwell.gdocs --auth` and `--load-client`, run by a
person with an admin token in `BAO_TOKEN` (for example
`BAO_TOKEN=$(baobox token)`).
"""
import json
import os
import ssl
import subprocess
import urllib.error
import urllib.request

BAO_ADDR = os.environ.get("BAO_ADDR", "https://baobox.hackswithcoffee.me:8200")
GOOGLE_PATH = "apps/inkwell/google"
ROLE_ID_ITEM = "inkwell-approle-role-id"
SECRET_ID_ITEM = "inkwell-approle-secret-id"


class VaultError(Exception):
    pass


def _ssl_context():
    # The Command Line Tools Python ships without a CA bundle of its own;
    # certifi (pulled in by the Google client) has one.
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _request(method: str, path: str, token=None, body=None) -> dict:
    req = urllib.request.Request(
        f"{BAO_ADDR}/v1/{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **({"X-Vault-Token": token} if token else {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=_ssl_context()) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {}
        raise VaultError(f"vault {method} {path} returned HTTP {e.code}")
    except (urllib.error.URLError, OSError) as e:
        raise VaultError(f"vault unreachable at {BAO_ADDR} ({e})")


def _keychain(item: str):
    out = subprocess.run(
        ["security", "find-generic-password", "-s", item, "-w"],
        capture_output=True, text=True,
    )
    return out.stdout.strip() if out.returncode == 0 else None


def configured() -> bool:
    """True when this machine has the pipeline's AppRole credentials."""
    return bool(_keychain(ROLE_ID_ITEM) and _keychain(SECRET_ID_ITEM))


def _login() -> str:
    role_id, secret_id = _keychain(ROLE_ID_ITEM), _keychain(SECRET_ID_ITEM)
    if not (role_id and secret_id):
        raise VaultError(f"AppRole credentials missing from the Keychain ({ROLE_ID_ITEM}, {SECRET_ID_ITEM})")
    auth = _request("POST", "auth/approle/login", body={"role_id": role_id, "secret_id": secret_id})
    token = auth.get("auth", {}).get("client_token")
    if not token:
        raise VaultError("AppRole login returned no token")
    return token


def read_google(token=None) -> dict:
    """The stored Google fields, or {} if none are stored yet."""
    data = _request("GET", f"secret/data/{GOOGLE_PATH}", token=token or _login())
    return (data.get("data") or {}).get("data") or {}


def store_google(**fields) -> None:
    """Merge fields into the Google secret. Needs an admin token in BAO_TOKEN."""
    admin = os.environ.get("BAO_TOKEN")
    if not admin:
        raise VaultError("writing to the vault needs BAO_TOKEN set, e.g. BAO_TOKEN=$(baobox token)")
    merged = {**read_google(token=admin), **fields}
    _request("POST", f"secret/data/{GOOGLE_PATH}", token=admin, body={"data": merged})
