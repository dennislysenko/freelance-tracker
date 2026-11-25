"""Sign the Upwork fill extension for Firefox-based browsers (Zen, Firefox).

Release builds of Firefox-based browsers only keep a signed add-on installed;
an unsigned one loaded from about:debugging is removed when the browser
quits. This script has Mozilla sign `upwork_extension/` as an **unlisted**
add-on (self-distributed: it is never listed or published on
addons.mozilla.org) and saves the signed .xpi to `dist/`.

One-time setup, by you:
  1. Create an addons.mozilla.org account and API credentials at
     https://addons.mozilla.org/developers/addon/api/key/
  2. Store them in the macOS Keychain (in your own terminal; each command
     asks for the value without showing it):
       security add-generic-password -s amo-jwt-issuer -a "$USER" -w
       security add-generic-password -s amo-jwt-secret -a "$USER" -w

Then:  python sign_upwork_extension.py
Install the .xpi it prints from about:addons → gear → Install Add-on From File.

Each signing needs a new version number in upwork_extension/manifest.json;
pass --bump to raise the last number automatically. Standard library only.
The credentials are read from the Keychain at run time and never written to
disk or printed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import io
import json
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXT_DIR = ROOT / "upwork_extension"
DIST = ROOT / "dist"
API = "https://addons.mozilla.org/api/v5"
INCLUDE = ("manifest.json", "background.js", "content.js", "popup.html", "popup.js")
POLL_SECONDS = 5
POLL_LIMIT = 120  # 10 minutes


# --- pure helpers (tested) -------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_jwt(issuer: str, secret: str, now: int | None = None) -> str:
    """AMO's API auth: an HS256 JWT valid for 60 seconds."""
    now = int(time.time()) if now is None else now
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64(json.dumps({"iss": issuer, "jti": secrets.token_hex(16),
                               "iat": now, "exp": now + 60}, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode()
    sig = _b64(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


def bump_version(version: str) -> str:
    """1.0.0 -> 1.0.1"""
    parts = version.split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    return ".".join(parts)


def build_zip(ext_dir: Path = EXT_DIR) -> bytes:
    """The extension's runtime files only (no README, no tooling)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in INCLUDE:
            zf.write(ext_dir / name, name)
    return buf.getvalue()


def multipart(fields: dict, file_field: str, filename: str, data: bytes):
    boundary = uuid.uuid4().hex
    out = io.BytesIO()
    for key, value in fields.items():
        out.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode())
    out.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
              f"filename=\"{filename}\"\r\nContent-Type: application/zip\r\n\r\n".encode())
    out.write(data)
    out.write(f"\r\n--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


# --- Keychain and HTTP -------------------------------------------------------


def keychain(service: str) -> str:
    try:
        return subprocess.run(["security", "find-generic-password", "-s", service, "-w"],
                              check=True, capture_output=True, text=True).stdout.strip()
    except subprocess.CalledProcessError:
        sys.exit(f"No '{service}' in the Keychain. See the setup steps at the top of this file.")


class AmoError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Amo:
    def __init__(self, issuer, secret):
        self.issuer, self.secret = issuer, secret

    def call(self, method, path, body=None, content_type="application/json", raw=False):
        url = path if path.startswith("http") else API + path
        data = json.dumps(body).encode() if isinstance(body, dict) else body
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"JWT {make_jwt(self.issuer, self.secret)}")
        if data is not None:
            req.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                content = resp.read()
                return content if raw else (json.loads(content) if content else {})
        except urllib.error.HTTPError as err:
            detail = err.read().decode(errors="replace")[:800]
            raise AmoError(err.code, f"Mozilla said {err.code} for {method} {url}:\n{detail}")


def poll(fn, done, what):
    for _ in range(POLL_LIMIT):
        result = fn()
        if done(result):
            return result
        time.sleep(POLL_SECONDS)
    sys.exit(f"Timed out waiting for {what}. Run the script again later to pick it up.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bump", action="store_true", help="raise the manifest version before signing")
    args = parser.parse_args(argv)

    manifest_path = EXT_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if args.bump:
        manifest["version"] = bump_version(manifest["version"])
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    guid = manifest["browser_specific_settings"]["gecko"]["id"]
    version = manifest["version"]

    amo = Amo(keychain("amo-jwt-issuer"), keychain("amo-jwt-secret"))
    print(f"Uploading {guid} {version} for unlisted signing…")
    body, ctype = multipart({"channel": "unlisted"}, "upload", "upwork_extension.zip", build_zip())
    upload = amo.call("POST", "/addons/upload/", body, ctype)
    upload = poll(lambda: amo.call("GET", f"/addons/upload/{upload['uuid']}/"),
                  lambda u: u.get("processed"), "Mozilla's validation")
    if not upload.get("valid"):
        messages = (upload.get("validation") or {}).get("messages") or []
        errors = [m.get("message") for m in messages if m.get("type") == "error"]
        sys.exit("Mozilla's validation failed:\n  " + "\n  ".join(errors or [json.dumps(upload)[:800]]))

    # First time: create the (unlisted) add-on; afterwards: add a version.
    try:
        existing = amo.call("GET", f"/addons/addon/{guid}/")
    except AmoError as exc:
        if exc.code != 404:
            raise
        existing = None
    if existing:
        created = amo.call("POST", f"/addons/addon/{guid}/versions/", {"upload": upload["uuid"]})
        version_id = created["id"]
    else:
        created = amo.call("PUT", f"/addons/addon/{guid}/", {"version": {"upload": upload["uuid"]}})
        version_id = created["current_version"]["id"] if created.get("current_version") else None
        if version_id is None:
            versions = amo.call("GET", f"/addons/addon/{guid}/versions/?filter=all_with_unlisted")
            version_id = versions["results"][0]["id"]

    print("Waiting for Mozilla to sign it (usually under a minute)…")
    signed = poll(lambda: amo.call("GET", f"/addons/addon/{guid}/versions/{version_id}/"),
                  lambda v: (v.get("file") or {}).get("status") == "public" and (v.get("file") or {}).get("url"),
                  "signing")
    DIST.mkdir(exist_ok=True)
    out = DIST / f"upwork_fill-{version}.xpi"
    out.write_bytes(amo.call("GET", signed["file"]["url"], raw=True))
    print(f"Signed: {out}")
    print("Install it from about:addons → gear → Install Add-on From File (remove the temporary one first).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AmoError as exc:
        raise SystemExit(str(exc))
