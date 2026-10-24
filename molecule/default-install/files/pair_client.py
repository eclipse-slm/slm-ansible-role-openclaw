"""Connect to the OpenClaw gateway through the reverse proxy like the Control UI.

Usage: pair_client.py <wss-url> <origin> <user> <password>

Generates a fresh Ed25519 device identity, signs the gateway's connect
challenge and prints the connect response as JSON. A build mismatch is
retried once with the gateway's build id, as the Control UI does on reload. Exits 0 when the gateway
answers hello-ok, 1 otherwise. Test-only helper for molecule verify.
"""

import asyncio
import base64
import hashlib
import json
import ssl
import sys
import uuid

import websockets
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

CLIENT_ID = "openclaw-control-ui"
CLIENT_MODE = "webchat"
ROLE = "operator"
SCOPES = ["operator.read"]


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


async def connect(url: str, origin: str, user: str, password: str, key: Ed25519PrivateKey,
                  build_id: str | None = None) -> dict:
    raw_public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    device_id = hashlib.sha256(raw_public).hexdigest()

    tls = ssl.create_default_context()
    tls.check_hostname = False
    tls.verify_mode = ssl.CERT_NONE
    basic = base64.b64encode(f"{user}:{password}".encode()).decode()

    async with websockets.connect(
        url,
        ssl=tls,
        origin=origin,
        additional_headers={"Authorization": f"Basic {basic}"},
    ) as ws:
        challenge = json.loads(await asyncio.wait_for(ws.recv(), 15))
        if challenge.get("event") != "connect.challenge":
            return {"ok": False, "error": f"unexpected first frame: {challenge}"}
        nonce = challenge["payload"]["nonce"]
        signed_at = challenge["payload"]["ts"]

        # v2 device auth payload, see buildDeviceAuthPayload in the openclaw package
        payload = "|".join(
            ["v2", device_id, CLIENT_ID, CLIENT_MODE, ROLE, ",".join(SCOPES), str(signed_at), "", nonce]
        )
        request_id = str(uuid.uuid4())
        await ws.send(json.dumps({
            "type": "req",
            "id": request_id,
            "method": "connect",
            "params": {
                "minProtocol": 4,
                "maxProtocol": 4,
                "client": {"id": CLIENT_ID, "version": "molecule", "platform": "linux", "mode": CLIENT_MODE,
                           **({"buildId": build_id} if build_id else {})},
                "role": ROLE,
                "scopes": SCOPES,
                "caps": [],
                "device": {
                    "id": device_id,
                    "publicKey": b64url(raw_public),
                    "signature": b64url(key.sign(payload.encode())),
                    "signedAt": signed_at,
                    "nonce": nonce,
                },
            },
        }))
        while True:
            frame = json.loads(await asyncio.wait_for(ws.recv(), 30))
            if frame.get("type") == "res" and frame.get("id") == request_id:
                frame["deviceId"] = device_id
                return frame


def main() -> int:
    url, origin, user, password = sys.argv[1:5]
    key = Ed25519PrivateKey.generate()
    result = asyncio.run(connect(url, origin, user, password, key))
    # The Control UI must match the gateway build; a browser reloads, we retry once
    details = (result.get("error") or {}).get("details") or {}
    if details.get("code") == "PROTOCOL_MISMATCH" and details.get("gatewayBuildId"):
        result = asyncio.run(connect(url, origin, user, password, key, details["gatewayBuildId"]))
    print(json.dumps(result))
    ok = result.get("ok") and (result.get("payload") or {}).get("type") == "hello-ok"
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
