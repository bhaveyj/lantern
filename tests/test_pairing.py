import hashlib
import hmac
import json

import pytest

from lantern.pairing import Pake, PairedReceivers, authenticate, context, proof, remembered_key


def test_native_pake_mutual_confirmation_and_wrong_codes_or_contexts():
    for pin, binding, valid in [("123456", b"channel", True), ("654321", b"channel", False), ("123456", b"altered channel", False)]:
        with Pake(0, "123456", b"channel") as client, Pake(1, pin, binding) as server:
            response = server.step(1, client.step(0))
            if not valid:
                with pytest.raises(ValueError): client.step(2, response[:97])
                continue
            result = client.step(2, response[:97])
            server.step(3, result[:32])
            assert hmac.compare_digest(result[32:], response[97:])
            with pytest.raises(ValueError): server.step(3, result[:32])


def test_tampered_points_and_confirmation_are_rejected():
    with Pake(1, "123456", b"context") as server:
        with pytest.raises(ValueError): server.step(1, bytes(65))
    with Pake(0, "123456", b"context") as client, Pake(1, "123456", b"context") as server:
        response = server.step(1, client.step(0))
        with pytest.raises(ValueError): client.step(2, response[:65] + bytes(32))


def test_pairing_transcript_binds_certificates_ports_nonces_identity_and_name():
    hello = {"name": "Laptop", "mediaPort": 12345, "mediaFingerprint": "a" * 64, "clientId": "b" * 32, "clientNonce": "c" * 64}
    original = context("d" * 64, hello, "e" * 64)
    assert original.startswith(b"\x00&Lantern Pairing")
    for field, value in [("name", "Other"), ("mediaPort", 12346), ("mediaFingerprint", "f" * 64), ("clientId", "a" * 32), ("clientNonce", "a" * 64)]:
        assert context("d" * 64, {**hello, field: value}, "e" * 64) != original
    assert context("a" * 64, hello, "e" * 64) != original
    assert context("d" * 64, hello, "a" * 64) != original
    assert proof(bytes(32), "client resume", original) != proof(bytes(32), "server accepted", original)


def test_remembered_credentials_are_encrypted_and_can_be_forgotten(tmp_path):
    store = PairedReceivers(tmp_path / "paired.bin")
    key = hashlib.sha256(b"dummy test credential").digest()
    store.save("a" * 64, "b" * 32, key, {"id": "tv", "name": "Living room"})
    assert key.hex().encode() not in store.path.read_bytes()
    restored = PairedReceivers(store.path)
    assert restored.get("a" * 64)["key"] == key.hex()
    restored.forget("a" * 64)
    assert restored.read() == {}


async def test_unauthenticated_peer_cannot_supply_pairing_ui_text_or_links():
    sent = []
    class FakeSocket:
        async def send_json(self, message): sent.append(message)
        async def receive_json(self):
            return {"type": "error", "version": 3, "code": "PAIRING_REJECTED", "message": "<a href='https://attacker.example'>Send a secret here</a>"}
    with pytest.raises(ValueError) as error:
        await authenticate(FakeSocket(), "a" * 64, "123456", None, "PC", 12345, "b" * 64)
    assert "attacker" not in str(error.value) and "<a" not in str(error.value)
    assert "pin" not in sent[0]
