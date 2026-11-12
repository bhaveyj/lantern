"""Local TLS identities and explicit, persistent receiver certificate trust."""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import secrets
import socket
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from .credentials import crypt

RECEIVER_CONNECT_TIMEOUT = 8.0
RECEIVER_TLS_TIMEOUT = 8.0


class ReceiverConnectionError(OSError):
    """A safe diagnostic that identifies the failed connection phase."""

    def __init__(self, host: str, port: int, phase: str, reason: str):
        self.phase = phase
        self.reason = reason
        address = f"{host}:{port}"
        if phase == "tls":
            message = f"The TV accepted a connection at {address}, but the encrypted connection failed. Restart Lantern TV and check that the receiver app is up to date."
        elif reason == "refused":
            message = f"The TV refused the connection at {address}. Open Lantern TV on the TV and check the receiver port."
        elif reason == "timeout":
            message = f"The TV did not respond at {address}. Check its IP address, keep Lantern TV open, and connect both devices to the same home network."
        else:
            message = f"Cannot reach the TV at {address}. Check its IP address and that both devices are on the same home network."
        super().__init__(message)


def fingerprint(value: str) -> str:
    value = value.replace(" ", "").replace(":", "").lower()
    if not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("Use the full SHA-256 fingerprint shown on the TV.")
    return value


async def receiver_fingerprint(host: str, port: int) -> str:
    # This connection sends no PIN or application data. Its certificate must be
    # approved out of band before opening an authenticated control connection.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    # Establish TCP separately so an unreachable TV is not described as an
    # outdated TLS receiver. The first bytes sent over the socket are still TLS.
    connection = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
    connection.setblocking(False)
    writer = None
    try:
        try:
            await asyncio.wait_for(asyncio.get_running_loop().sock_connect(connection, (host, port)), RECEIVER_CONNECT_TIMEOUT)
        except TimeoutError:
            raise ReceiverConnectionError(host, port, "tcp", "timeout") from None
        except ConnectionRefusedError:
            raise ReceiverConnectionError(host, port, "tcp", "refused") from None
        except OSError:
            raise ReceiverConnectionError(host, port, "tcp", "unreachable") from None
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(sock=connection, ssl=context, server_hostname=host, ssl_handshake_timeout=RECEIVER_TLS_TIMEOUT, ssl_shutdown_timeout=2), RECEIVER_TLS_TIMEOUT)
        except (OSError, TimeoutError):
            raise ReceiverConnectionError(host, port, "tls", "failed") from None
        certificate = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
        if not certificate:
            raise ReceiverConnectionError(host, port, "tls", "failed")
        return hashlib.sha256(certificate).hexdigest()
    finally:
        try:
            if writer is not None:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), 2)
                except (OSError, TimeoutError):
                    pass
        finally:
            connection.close()


class ReceiverTrust:
    def __init__(self, path: Path):
        self.path = path

    def read(self) -> dict:
        if not self.path.exists():
            return {}
        if self.path.stat().st_size > 65536:
            raise ValueError("The saved receiver identity file is invalid.")
        records = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(records, dict) or len(records) > 256:
            raise ValueError("The saved receiver identity file is invalid.")
        return {str(key): fingerprint(value) for key, value in records.items()}

    def get(self, host: str, port: int) -> str | None:
        return self.read().get(f"{host}:{port}")

    def save(self, host: str, port: int, value: str):
        records = self.read()
        key = f"{host}:{port}"
        if key not in records and len(records) >= 256:
            raise ValueError("Too many saved TV identities.")
        records[key] = fingerprint(value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        pending = self.path.with_suffix(".tmp")
        pending.write_text(json.dumps(records), encoding="utf-8")
        pending.replace(self.path)


class LocalIdentity:
    def __init__(self, folder: Path):
        folder.mkdir(parents=True, exist_ok=True)
        self.certificate = folder / "media-cert.pem"
        self.key = folder / "media-key.pem"
        self.secret = folder / "media-password.bin"
        if not self.certificate.exists():
            password = secrets.token_bytes(32)
            private = ec.generate_private_key(ec.SECP256R1())
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Lantern local media")])
            now = datetime.now(timezone.utc)
            certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(private.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=3650))
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
                .sign(private, hashes.SHA256()))
            # Never persist an unencrypted private key. Windows protects the
            # random PEM password with the current user's DPAPI identity.
            self.key.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.BestAvailableEncryption(password)))
            self.secret.write_bytes(crypt(password) if os.name == "nt" else password)
            if os.name != "nt":
                self.secret.chmod(0o600)
                self.key.chmod(0o600)
            self.certificate.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        password = self.secret.read_bytes()
        if os.name == "nt":
            password = crypt(password, decrypt=True)
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.context.load_cert_chain(self.certificate, self.key, password)
        certificate = x509.load_pem_x509_certificate(self.certificate.read_bytes())
        self.fingerprint = certificate.fingerprint(hashes.SHA256()).hex()
