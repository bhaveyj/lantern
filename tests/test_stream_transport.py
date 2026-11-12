import gzip
import math
import struct
import zlib
from pathlib import Path

import pytest

from lantern import stream_transport as transport
from lantern.stream_transport import TransportDecodeError, looks_wrapped_segment, unwrap_segment


def ts(packets=4):
    return b"".join(b"\x47" + bytes((i,)) + bytes(range(186)) for i in range(packets))


def chunk(tag, body):
    return (struct.pack(">I", len(body)) + tag + body
            + struct.pack(">I", zlib.crc32(body, zlib.crc32(tag)) & 0xffffffff))


def png(payload, *, filter_type=0, alpha=False, tail=b"", width=17):
    compressed = gzip.compress(payload, mtime=0)
    rgb = transport.PIXELS + struct.pack(">I", len(compressed)) + compressed
    height = math.ceil(len(rgb) / (width * 3))
    rgb = rgb.ljust(width * height * 3, b"\0")
    channels = 4 if alpha else 3
    previous = bytes(width * channels)
    scanlines = bytearray()
    for y in range(height):
        row = rgb[y * width * 3:(y + 1) * width * 3]
        if alpha:
            row = b"".join(row[x:x + 3] + b"\xff" for x in range(0, len(row), 3))
        encoded = bytearray(row)
        for x in range(len(row)):
            a = row[x - channels] if x >= channels else 0
            b = previous[x]
            c = previous[x - channels] if x >= channels else 0
            # Encode independently of the decoder's Paeth implementation.
            prediction = a + b - c
            paeth = min((a, b, c), key=lambda value: abs(prediction - value))
            predictors = (0, a, b, (a + b) // 2, paeth)
            encoded[x] = (row[x] - predictors[filter_type]) & 255
        scanlines += bytes((filter_type,)) + encoded
        previous = row
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6 if alpha else 2, 0, 0, 0)
    return (transport.PNG + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(scanlines))
            + chunk(b"IEND", b"") + tail)


@pytest.mark.parametrize("filter_type", range(5))
@pytest.mark.parametrize("alpha", (False, True))
def test_png_pixels_all_filters(filter_type, alpha):
    original = ts()
    wrapped = png(original, filter_type=filter_type, alpha=alpha)
    result = unwrap_segment(wrapped, "image/png")
    assert result.data == original
    assert result.changed
    assert result.content_type == "video/mp2t"


@pytest.mark.parametrize("wrap", [
    lambda payload: gzip.compress(payload),
    lambda payload: b"public-wrapper" + transport.GZIP + gzip.compress(payload),
    lambda payload: b"public-wrapper" + transport.RAW + payload,
    lambda payload: png(ts(), tail=payload),
])
def test_lossless_gzip_and_raw_wrappers(wrap):
    original = ts()
    assert unwrap_segment(wrap(original)).data == original


def test_webp_exif_wrapper():
    original = ts()
    body = b"WEBP" + b"EXIF" + struct.pack("<I", len(original)) + original
    wrapped = b"RIFF" + struct.pack("<I", len(body)) + body
    assert unwrap_segment(wrapped, "image/webp").data == original


@pytest.mark.parametrize("data,mime", [
    (ts(), "video/mp2t"),
    (b"\xff\xf1\x50\x80\x00\x1f\xfc", "audio/aac"),
    (struct.pack(">I", 8) + b"styp" + struct.pack(">I", 12) + b"mdat" + b"abcd", "video/mp4"),
    (b"ordinary unrecognized media", "application/octet-stream"),
])
def test_plain_media_passes_unchanged(data, mime):
    result = unwrap_segment(data, mime)
    assert result.data is data
    assert result.content_type == mime
    assert not result.changed


def test_media_signature_takes_precedence_over_misleading_image_mime():
    assert not looks_wrapped_segment(ts(), "image/png")


@pytest.mark.parametrize("data", [
    transport.PNG,
    png(ts())[:-1],
    b"\x1f\x8b\x08truncated",
    transport.GZIP + gzip.compress(ts())[:-3],
    transport.RAW + b"\x47broken transport stream",
    b"RIFF\x00\x00\x00\x00WEBP",
    b"not actually an image",
])
def test_malformed_wrappers_fail_safely(data):
    with pytest.raises(TransportDecodeError, match="media transport wrapper"):
        unwrap_segment(data, "image/png")


def test_crc_corruption_rejected():
    wrapped = bytearray(png(ts()))
    wrapped[29] ^= 1
    with pytest.raises(TransportDecodeError):
        unwrap_segment(bytes(wrapped))


def test_png_dimension_limit():
    ihdr = struct.pack(">IIBBBBB", transport.MAX_PNG_DIMENSION + 1, 1, 8, 2, 0, 0, 0)
    wrapped = transport.PNG + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")
    with pytest.raises(TransportDecodeError):
        unwrap_segment(wrapped)


def test_png_chunk_limit(monkeypatch):
    monkeypatch.setattr(transport, "MAX_CHUNKS", 2)
    with pytest.raises(TransportDecodeError):
        unwrap_segment(png(ts()))


def test_compressed_output_bomb_bound(monkeypatch):
    monkeypatch.setattr(transport, "MAX_DECODED_OUTPUT", 1024)
    with pytest.raises(TransportDecodeError):
        unwrap_segment(gzip.compress(b"\0" * 100_000))


def test_input_limit(monkeypatch):
    monkeypatch.setattr(transport, "MAX_WRAPPED_INPUT", 16)
    with pytest.raises(TransportDecodeError):
        unwrap_segment(png(ts()))


def test_decode_time_bound(monkeypatch):
    monkeypatch.setattr(transport, "MAX_DECODE_SECONDS", -1)
    with pytest.raises(TransportDecodeError, match="decoding limit"):
        unwrap_segment(png(ts()))


def test_rejects_trailing_gzip_stream_and_invalid_unwrapped_media():
    for data in (gzip.compress(ts()) + gzip.compress(ts()), gzip.compress(b"not media")):
        with pytest.raises(TransportDecodeError):
            unwrap_segment(data)


def test_decoded_fmp4_boxes_are_validated():
    valid = struct.pack(">I", 8) + b"styp" + struct.pack(">I", 12) + b"mdat" + b"abcd"
    assert unwrap_segment(gzip.compress(valid)).data == valid
    with pytest.raises(TransportDecodeError):
        unwrap_segment(gzip.compress(valid[:-1]))


@pytest.mark.parametrize("prefix", [transport.PNG, b"RIFF\0\0\0\0WEBP", b"\x1f\x8b", transport.RAW, transport.GZIP])
def test_wrapper_peek(prefix):
    assert looks_wrapped_segment(prefix)


def test_downloaded_public_fixture_when_available():
    fixture = Path(__file__).resolve().parents[1] / ".tmp" / "ripple-wrapped-segment.bin"
    if not fixture.exists():
        pytest.skip("Live diagnostic fixture is intentionally not committed.")
    result = unwrap_segment(fixture.read_bytes(), "image/png")
    assert result.changed and result.content_type == "video/mp2t"
    assert len(result.data) > 188 * 3
    assert result.data[0] == result.data[188] == 0x47
