"""Protocol peer for integration tests; uses the real shared PAKE library."""
from lantern.pairing import Pake, context, proof, remembered_key, unhex


async def accept(ws, identity, *, pin="123456", records=None):
    records = records if records is not None else {}
    hello = await ws.receive_json()
    assert hello["type"] == "pair.start" and hello["version"] == 3
    assert "pin" not in hello
    if hello["mode"] == "resume" and hello["clientId"] not in records:
        await ws.send_json({"type": "error", "version": 3, "code": "PAIRING_REQUIRED", "message": "Pair this TV again"})
        await ws.close()
        return None
    nonce = "ab" * 32
    binding = context(identity, hello, nonce)
    await ws.send_json({"type": "pair.challenge", "version": 3, "nonce": nonce})
    if hello["mode"] == "code":
        with Pake(1, pin, binding) as verifier:
            share = await ws.receive_json()
            output = verifier.step(1, unhex(share["share"], 65))
            await ws.send_json({"type": "pair.proof", "version": 3, "share": output[:65].hex(), "proof": output[65:97].hex()})
            frame = await ws.receive()
            if frame.type.name != "TEXT":
                return None
            import json
            confirm = json.loads(frame.data)
            verifier.step(3, unhex(confirm["proof"], 32))
            key = remembered_key(output[97:], binding)
            records[hello["clientId"]] = key
    else:
        key = records.get(hello["clientId"])
        if key is None:
            await ws.send_json({"type": "error", "code": "PAIRING_REQUIRED", "message": "Pair this TV again"})
            await ws.close()
            return None
        message = await ws.receive_json()
        assert message["proof"] == proof(key, "client resume", binding)
    await ws.send_json({"type": "pair.ok", "version": 3, "proof": proof(key, "server accepted", binding), "audioMimes": ["audio/mp4a-latm"]})
    return hello
