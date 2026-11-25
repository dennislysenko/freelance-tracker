"""Signing helper for the Upwork fill extension (no network)."""

import base64
import hashlib
import hmac
import io
import json
import zipfile

import sign_upwork_extension as s


def _decode(part):
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def test_jwt_is_hs256_signed_and_short_lived():
    token = s.make_jwt("user:12345:67", "secret", now=1_700_000_000)
    header, payload, sig = token.split(".")
    assert _decode(header) == {"alg": "HS256", "typ": "JWT"}
    body = _decode(payload)
    assert body["iss"] == "user:12345:67" and body["exp"] - body["iat"] == 60 and body["jti"]
    expected = hmac.new(b"secret", f"{header}.{payload}".encode(), hashlib.sha256).digest()
    assert base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)) == expected


def test_bump_version():
    assert s.bump_version("1.0.0") == "1.0.1"
    assert s.bump_version("1.2.9") == "1.2.10"


def test_zip_holds_only_the_runtime_files_and_a_signable_manifest():
    names = sorted(zipfile.ZipFile(io.BytesIO(s.build_zip())).namelist())
    assert names == sorted(s.INCLUDE)
    manifest = json.loads((s.EXT_DIR / "manifest.json").read_text())
    gecko = manifest["browser_specific_settings"]["gecko"]
    assert "@" in gecko["id"] and gecko["data_collection_permissions"] == {"required": ["none"]}
