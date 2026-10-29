"""Encrypt the published data files so the site needs a username and password.

    python -m ingest.lock [--site site]

Reads SITE_USERNAME and SITE_PASSWORD from the environment; if either is missing the site is left open. Every file
under site/data/ is replaced by AES-256-GCM ciphertext (12-byte IV + ciphertext + tag), and site/auth.json tells the
page how to derive the key and holds an encrypted check value. js/auth.js asks for the login and decrypts in the
browser. The salt depends only on the username, so the key (kept in the browser after logging in) survives rebuilds
until the username or password changes.
"""
import argparse
import base64
import hashlib
import json
import logging
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

log = logging.getLogger("lock")
ITERATIONS = 310_000
CHECK = b"wasatch-wx"


def derive_key(username, password):
    salt = hashlib.sha256(b"wasatch-wx-login:" + username.encode()).digest()
    return salt, hashlib.pbkdf2_hmac("sha256", f"{username}\n{password}".encode(), salt, ITERATIONS, 32)


def encrypt(aes, data):
    iv = os.urandom(12)
    return iv + aes.encrypt(iv, data, None)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="site")
    a = ap.parse_args()
    site = Path(a.site)
    user, pw = os.environ.get("SITE_USERNAME", ""), os.environ.get("SITE_PASSWORD", "")
    auth = site / "auth.json"
    if not user or not pw:
        log.warning("SITE_USERNAME / SITE_PASSWORD not set: publishing without a login")
        auth.unlink(missing_ok=True)
        return
    salt, key = derive_key(user, pw)
    aes = AESGCM(key)
    n = size = 0
    for p in sorted((site / "data").rglob("*")):
        if p.is_file():
            raw = p.read_bytes()
            p.write_bytes(encrypt(aes, raw))
            n += 1
            size += len(raw)
    auth.write_text(json.dumps({
        "v": 1, "iterations": ITERATIONS, "salt": base64.b64encode(salt).decode(),
        "check": base64.b64encode(encrypt(aes, CHECK)).decode(),
    }))
    log.info("encrypted %d data files (%.1f MB)", n, size / 1e6)


if __name__ == "__main__":
    main()
