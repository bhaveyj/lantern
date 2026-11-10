"""Bounded decoding of public, lossless media transport wrappers.

Some HLS players ship MPEG-TS in an image or gzip envelope and unwrap it
before playback. This module only reads those data formats; it never runs
page code, obtains credentials, or handles encrypted media.
"""
from __future__ import annotations

import struct
import time
import zlib
from dataclasses import dataclass

MAX_WRAPPED_INPUT = 16 * 1024 * 1024
MAX_DECODED_OUTPUT = 32 * 1024 * 1024
MAX_PNG_PIXELS = 8 * 1024 * 1024
MAX_PNG_DIMENSION = 8192
MAX_CHUNKS = 4096
MAX_DECODE_SECONDS = 10.0
PNG = b"\x89PNG\r\n\x1a\n"
PIXELS = b"TIKTIKPX"
RAW = b"TIKTIKRAW"
GZIP = b"TIKTIKTSGZ"


class TransportDecodeError(ValueError):
    """Safe error text, without upstream addresses or payload contents."""


@dataclass(frozen=True)
class DecodedSegment:
    data: bytes
    content_type: str
    changed: bool


def _ts(data: bytes) -> bool:
    return len(data) >= 376 and len(data) % 188 == 0 and all(
        data[i] == 0x47 for i in range(0, len(data), 188)
    )


def _mp4(data: bytes) -> bool:
    if len(data) < 8 or data[4:8] not in {b"ftyp", b"styp", b"moof", b"sidx"}:
        return False
    offset = count = 0
    while offset < len(data):
        if offset + 8 > len(data) or count >= MAX_CHUNKS:
            return False
        size = int.from_bytes(data[offset:offset + 4], "big")
        header = 8
        if size == 1:
            if offset + 16 > len(data):
                return False
            size = int.from_bytes(data[offset + 8:offset + 16], "big")
            header = 16
        elif size == 0:
            size = len(data) - offset
        if size < header or size > len(data) - offset:
            return False
        offset += size
        count += 1
    return offset == len(data)


def _plain_signature(data: bytes) -> bool:
    return ((len(data) >= 189 and data[0] == data[188] == 0x47)
            or (len(data) >= 8 and data[4:8] in {b"ftyp", b"styp", b"moof", b"sidx"})
            or (len(data) >= 2 and data[0] == 0xff and data[1] & 0xf6 == 0xf0))


def looks_wrapped_segment(prefix: bytes, content_type: str = "") -> bool:
    """Recognize an envelope from a bounded peek, without inspecting URLs."""
    if _plain_signature(prefix):
        return False
    return (prefix.startswith(PNG) or prefix.startswith(b"\x1f\x8b")
            or (prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP")
            or RAW in prefix or GZIP in prefix
            or content_type.partition(";")[0].strip().lower().startswith("image/"))


def _fail() -> None:
    raise TransportDecodeError("The video uses an invalid or unsupported media transport wrapper.")


def _check_time(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise TransportDecodeError("The media transport wrapper exceeded its decoding limit.")


def _inflate(data: bytes, limit: int, *, gzip: bool = False) -> bytes:
    try:
        decoder = zlib.decompressobj(31 if gzip else zlib.MAX_WBITS)
        output = decoder.decompress(data, limit + 1)
        if len(output) > limit or decoder.unconsumed_tail or not decoder.eof or decoder.unused_data:
            _fail()
        return output
    except zlib.error:
        _fail()


def _validated(data: bytes) -> DecodedSegment:
    if len(data) > MAX_DECODED_OUTPUT:
        _fail()
    if _ts(data):
        return DecodedSegment(data, "video/mp2t", True)
    if _mp4(data):
        return DecodedSegment(data, "video/mp4", True)
    _fail()


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else (b if pb <= pc else c)


def _png(data: bytes, deadline: float) -> bytes:
    offset = 8
    width = height = channels = 0
    idats: list[bytes] = []
    ended = False
    for count in range(MAX_CHUNKS):
        _check_time(deadline)
        if offset + 12 > len(data):
            _fail()
        length = int.from_bytes(data[offset:offset + 4], "big")
        tag = data[offset + 4:offset + 8]
        if length > len(data) - offset - 12:
            _fail()
        body = data[offset + 8:offset + 8 + length]
        crc = int.from_bytes(data[offset + 8 + length:offset + 12 + length], "big")
        if zlib.crc32(body, zlib.crc32(tag)) & 0xffffffff != crc:
            _fail()
        offset += 12 + length
        if count == 0 and tag != b"IHDR":
            _fail()
        if tag == b"IHDR":
            if count != 0 or length != 13:
                _fail()
            width, height, depth, colour, compression, filtering, interlace = struct.unpack(">IIBBBBB", body)
            if (not width or not height or max(width, height) > MAX_PNG_DIMENSION
                    or width * height > MAX_PNG_PIXELS or depth != 8 or colour not in (2, 6)
                    or compression or filtering or interlace):
                _fail()
            channels = 3 if colour == 2 else 4
        elif tag == b"IDAT":
            idats.append(body)
        elif tag == b"IEND":
            if length:
                _fail()
            ended = True
            break
        elif tag[0] & 32 == 0:
            # Unknown critical PNG chunks could change pixel interpretation.
            _fail()
    if not ended or not idats:
        _fail()
    if offset < len(data):
        tail = data[offset:]
        if _ts(tail):
            return tail
        _fail()
    stride = width * channels
    expected = (stride + 1) * height
    if expected > MAX_DECODED_OUTPUT + MAX_PNG_DIMENSION:
        _fail()
    inflated = _inflate(b"".join(idats), expected)
    if len(inflated) != expected:
        _fail()
    rgb = bytearray(width * height * 3)
    previous = bytearray(stride)
    target = 0
    for y in range(height):
        _check_time(deadline)
        start = y * (stride + 1)
        filter_type = inflated[start]
        row = bytearray(inflated[start + 1:start + 1 + stride])
        if filter_type > 4:
            _fail()
        if filter_type:
            for x in range(stride):
                a = row[x - channels] if x >= channels else 0
                b = previous[x]
                c = previous[x - channels] if x >= channels else 0
                predictor = (a if filter_type == 1 else b if filter_type == 2
                             else (a + b) // 2 if filter_type == 3 else _paeth(a, b, c))
                row[x] = (row[x] + predictor) & 255
        if channels == 3:
            rgb[target:target + stride] = row
            target += stride
        else:
            for x in range(0, stride, 4):
                rgb[target:target + 3] = row[x:x + 3]
                target += 3
        previous = row
    if rgb[:8] != PIXELS or len(rgb) < 12:
        _fail()
    length = int.from_bytes(rgb[8:12], "big")
    if not length or length > len(rgb) - 12:
        _fail()
    return _inflate(bytes(rgb[12:12 + length]), MAX_DECODED_OUTPUT, gzip=True)


def _webp(data: bytes, deadline: float) -> bytes:
    if len(data) < 12 or int.from_bytes(data[4:8], "little") + 8 != len(data):
        _fail()
    offset = 12
    for _ in range(MAX_CHUNKS):
        _check_time(deadline)
        if offset + 8 > len(data):
            _fail()
        tag = data[offset:offset + 4]
        length = int.from_bytes(data[offset + 4:offset + 8], "little")
        offset += 8
        if length > len(data) - offset:
            _fail()
        if tag == b"EXIF":
            return data[offset:offset + length]
        offset += length + (length & 1)
    _fail()


def unwrap_segment(data: bytes, content_type: str = "") -> DecodedSegment:
    """Unwrap known image/gzip media envelopes, leaving ordinary media intact.

    Callers must bound buffering to MAX_WRAPPED_INPUT before invoking this
    synchronous function and run it off the event loop with bounded concurrency.
    """
    if not looks_wrapped_segment(data[:65536], content_type):
        return DecodedSegment(data, content_type, False)
    if len(data) > MAX_WRAPPED_INPUT:
        _fail()
    deadline = time.monotonic() + MAX_DECODE_SECONDS
    if data.startswith(PNG):
        result = _png(data, deadline)
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        result = _webp(data, deadline)
    elif data.startswith(b"\x1f\x8b"):
        result = _inflate(data, MAX_DECODED_OUTPUT, gzip=True)
    else:
        raw = data.find(RAW, 0, 65536)
        compressed = data.find(GZIP, 0, 65536)
        if raw >= 0:
            result = data[raw + len(RAW):]
        elif compressed >= 0:
            result = _inflate(data[compressed + len(GZIP):], MAX_DECODED_OUTPUT, gzip=True)
        else:
            _fail()
    _check_time(deadline)
    return _validated(result)
