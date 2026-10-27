"""Code-only SPAKE2+ pairing and channel-bound remembered-device authentication."""
from __future__ import annotations

import ctypes
from functools import lru_cache
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import struct
import sys

from .credentials import crypt

VERSION = 3
DOMAIN = b"Lantern Pairing v3 SPAKE2+-P256-SHA256"


def unhex(value: str, size: int) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{%d}" % (size * 2), value):
        raise ValueError("Invalid pairing message.")
    return bytes.fromhex(value)


def context(receiver: str, hello: dict, nonce: str) -> bytes:
    name = hello["name"].encode("utf-8")
    port = hello["mediaPort"]
    if not name or len(name) > 128 or type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Invalid pairing message.")
    fields = [DOMAIN, unhex(receiver, 32), unhex(hello["mediaFingerprint"], 32),
              struct.pack(">H", port), unhex(hello["clientId"], 16),
              unhex(hello["clientNonce"], 32), unhex(nonce, 32), name]
    return b"".join(struct.pack(">H", len(field)) + field for field in fields)


def proof(key: bytes, label: str, binding: bytes) -> str:
    return hmac.digest(key, b"Lantern v3 " + label.encode("ascii") + hashlib.sha256(binding).digest(), "sha256").hex()


def remembered_key(shared: bytes, binding: bytes) -> bytes:
    return bytes.fromhex(proof(shared, "remembered key", binding))


@lru_cache(maxsize=1)
def native():
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    path = root / ("pairing" if getattr(sys, "frozen", False) else ".tools/pairing") / "lantern_pake.dll"
    if not path.is_file():
        raise RuntimeError("The pairing library is missing. Reinstall Lantern or run scripts/build-pairing.ps1.")
    library = ctypes.CDLL(str(path))
    library.lp_new.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
    library.lp_new.restype = ctypes.c_void_p
    library.lp_step.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
    library.lp_step.restype = ctypes.c_int
    library.lp_free.argtypes = [ctypes.c_void_p]
    library.lp_free.restype = None
    return library


class Pake:
    def __init__(self, role: int, pin: str, binding: bytes):
        if not re.fullmatch(r"[0-9]{6}", pin) or len(binding) > 1024:
            raise ValueError("Enter the six-digit code shown on your TV.")
        self.library = native()
        self.handle = self.library.lp_new(role, pin.encode("ascii"), 6, binding, len(binding))
        if not self.handle:
            raise ValueError("Secure pairing could not start.")

    def step(self, operation: int, data: bytes = b"") -> bytes:
        lengths = {0: (0, 65), 1: (65, 129), 2: (97, 64), 3: (32, 0)}
        incoming, outgoing = lengths[operation]
        if not self.handle or len(data) != incoming:
            raise ValueError("Invalid pairing exchange.")
        output = ctypes.create_string_buffer(outgoing or 1)
        if not self.library.lp_step(self.handle, operation, data, len(data), output, outgoing):
            raise ValueError("The code didn’t match. Check the TV and try again.")
        return output.raw[:outgoing]

    def close(self):
        if self.handle:
            self.library.lp_free(self.handle)
            self.handle = None

    def __enter__(self): return self
    def __exit__(self, *_): self.close()


class PairedReceivers:
    """DPAPI-encrypted reconnect keys, indexed by the verified certificate."""
    def __init__(self, path: Path):
        self.path = path

    def read(self) -> dict:
        if not self.path.exists():
            return {}
        if self.path.stat().st_size > 65536:
            raise ValueError("Saved TV pairings could not be read. Forget them and pair again.")
        data = self.path.read_bytes()
        try:
            records = json.loads(crypt(data, decrypt=True))
        except (ValueError, UnicodeError):
            raise ValueError("Saved TV pairings could not be read. Use Connection settings → Reset saved pairings, then pair again.") from None
        if not isinstance(records, dict) or len(records) > 32:
            raise ValueError("Invalid saved TV pairings.")
        for identity, record in records.items():
            unhex(identity, 32)
            unhex(record["clientId"], 16)
            unhex(record["key"], 32)
            if not all(isinstance(record.get(key), str) and len(record[key]) <= 512 for key in ("deviceId", "name")):
                raise ValueError("Invalid saved TV pairing.")
        return records

    def get(self, identity: str):
        return self.read().get(identity)

    def save(self, identity: str, client_id: str, key: bytes, device: dict):
        records = self.read()
        records = {known: record for known, record in records.items() if known == identity or record["deviceId"] != device["id"][:512]}
        if identity not in records and len(records) >= 32:
            raise ValueError("You have 32 saved TVs. Forget an unused TV before pairing another.")
        unhex(identity, 32)
        unhex(client_id, 16)
        if len(key) != 32:
            raise ValueError("Invalid pairing key.")
        records[identity] = {"clientId": client_id, "key": key.hex(), "deviceId": device["id"][:512], "name": device["name"][:512]}
        self.write(records)

    def write(self, records):
        data = json.dumps(records).encode("utf-8")
        data = crypt(data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(data)
        temporary.replace(self.path)

    def forget(self, identity: str):
        records = self.read()
        records.pop(identity, None)
        self.write(records)


async def authenticate(ws, receiver: str, pin: str, saved: dict | None, name: str, media_port: int, media_identity: str):
    from asyncio import wait_for
    client_id = saved["clientId"] if saved and not pin else secrets.token_hex(16)
    mode = "code" if pin else "resume"
    hello = {"type": "pair.start", "version": VERSION, "mode": mode, "name": name,
             "clientId": client_id, "clientNonce": secrets.token_hex(32),
             "mediaPort": media_port, "mediaFingerprint": media_identity}
    await ws.send_json(hello)

    async def receive(kind):
        message = await wait_for(ws.receive_json(), 20)
        if message.get("type") == "error":
            if message.get("version") != VERSION:
                raise ValueError("Update Lantern on both your PC and TV to pair securely.")
            if message.get("code") == "PAIRING_REQUIRED":
                raise PairingRequired()
            # This peer is not authenticated yet. Never display peer-controlled
            # text or links in the trusted desktop pairing UI.
            messages = {
                "RATE_LIMITED": "Too many pairing attempts. Wait a minute and try again.",
                "CODE_EXPIRED": "The TV code expired. Enter the new code shown on its screen.",
                "PAIRING_REJECTED": "Pairing could not complete. Check the TV code and try again.",
            }
            raise ValueError(messages.get(message.get("code"), "Pairing could not complete. Try again."))
        if message.get("type") != kind or message.get("version") != VERSION:
            raise ValueError("Update Lantern on both your PC and TV to pair securely.")
        return message

    challenge = await receive("pair.challenge")
    binding = context(receiver, hello, challenge["nonce"])
    if mode == "code":
        with Pake(0, pin, binding) as pake:
            await ws.send_json({"type": "pair.prove", "share": pake.step(0).hex()})
            response = await receive("pair.proof")
            result = pake.step(2, unhex(response["share"], 65) + unhex(response["proof"], 32))
            key = remembered_key(result[32:], binding)
            await ws.send_json({"type": "pair.confirm", "proof": result[:32].hex()})
    else:
        if saved is None: raise PairingRequired()
        key = unhex(saved["key"], 32)
        await ws.send_json({"type": "pair.resume", "proof": proof(key, "client resume", binding)})
    response = await receive("pair.ok")
    if not hmac.compare_digest(proof(key, "server accepted", binding), response.get("proof", "")):
        raise ValueError("The TV could not be verified. Pair it again using the code on its screen.")
    return response, client_id, key


class PairingRequired(Exception):
    pass
