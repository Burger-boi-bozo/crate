"""Optional WebAuthn/passkey enrollment and sign-in for the private admin panel."""
from __future__ import annotations

import base64
import json
import secrets
import time

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.admin_auth import check_admin_ip, require_admin, session_response


class PasskeyName(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="Passkey", min_length=1, max_length=80)


class PasskeyCredential(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge_id: str = Field(min_length=16, max_length=128)
    credential: dict


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _origin(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _rp(request: Request) -> str:
    return request.url.hostname or "localhost"


def install(app, queue, config, key):
    challenges: dict[str, dict] = {}

    def remember(kind: str, challenge: bytes, request: Request, name: str | None = None) -> str:
        challenge_id = secrets.token_urlsafe(24)
        challenges[challenge_id] = {
            "kind": kind, "challenge": challenge, "rp": _rp(request),
            "origin": _origin(request), "name": name, "expires": time.time() + 300,
        }
        if len(challenges) > 100:
            now = time.time()
            for item_id in list(challenges):
                if challenges[item_id]["expires"] < now:
                    challenges.pop(item_id, None)
        return challenge_id

    def consume(challenge_id: str, kind: str):
        item = challenges.pop(challenge_id, None)
        if not item or item["kind"] != kind or item["expires"] < time.time():
            raise HTTPException(400, "Passkey challenge expired. Start again.")
        return item

    @app.get("/api/admin/passkeys")
    async def list_passkeys(request: Request):
        require_admin(request, key)
        return [{"id": item["id"], "name": item["name"], "created_at": item["created_at"],
                 "last_used": item.get("last_used")} for item in queue.store.passkeys()]

    @app.delete("/api/admin/passkeys/{item_id}", status_code=204)
    async def delete_passkey(item_id: str, request: Request):
        require_admin(request, key)
        if not queue.store.delete_passkey(item_id):
            raise HTTPException(404, "Passkey not found.")
        queue.store.audit("admin", "passkey.delete", item_id)
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(status_code=204)

    @app.post("/api/admin/passkeys/register/options")
    async def register_options(body: PasskeyName, request: Request):
        require_admin(request, key)
        try:
            from webauthn import generate_registration_options, options_to_json
            from webauthn.helpers import base64url_to_bytes
            from webauthn.helpers.structs import PublicKeyCredentialDescriptor
        except ImportError as exc:
            raise HTTPException(503, "Passkey support is not installed.") from exc
        exclude = [PublicKeyCredentialDescriptor(id=base64url_to_bytes(item["credential_id"]))
                   for item in queue.store.passkeys()]
        options = generate_registration_options(
            rp_id=_rp(request), rp_name="Crate", user_name="crate-admin",
            user_id=b"crate-admin", user_display_name="Crate admin",
            exclude_credentials=exclude,
        )
        challenge_id = remember("register", options.challenge, request, body.name.strip())
        return {"challenge_id": challenge_id, "options": json.loads(options_to_json(options))}

    @app.post("/api/admin/passkeys/register/verify")
    async def register_verify(body: PasskeyCredential, request: Request):
        require_admin(request, key)
        item = consume(body.challenge_id, "register")
        try:
            from webauthn import verify_registration_response
            verified = verify_registration_response(
                credential=body.credential,
                expected_challenge=item["challenge"], expected_rp_id=item["rp"],
                expected_origin=item["origin"], require_user_verification=True,
            )
        except Exception as exc:
            raise HTTPException(400, "Passkey registration could not be verified.") from exc
        record = {
            "id": secrets.token_hex(8), "name": item.get("name") or "Passkey",
            "credential_id": _b64(verified.credential_id),
            "public_key": _b64(verified.credential_public_key),
            "sign_count": int(verified.sign_count), "created_at": time.time(), "last_used": None,
        }
        queue.store.save_passkey(record)
        queue.store.audit("admin", "passkey.create", record["id"], {"name": record["name"]})
        return {"id": record["id"], "name": record["name"], "created_at": record["created_at"]}

    @app.post("/api/admin/passkeys/auth/options")
    async def auth_options(request: Request):
        check_admin_ip(request, config)
        records = queue.store.passkeys()
        if not records:
            raise HTTPException(404, "No admin passkeys are enrolled.")
        try:
            from webauthn import generate_authentication_options, options_to_json
            from webauthn.helpers import base64url_to_bytes
            from webauthn.helpers.structs import PublicKeyCredentialDescriptor
        except ImportError as exc:
            raise HTTPException(503, "Passkey support is not installed.") from exc
        allow = [PublicKeyCredentialDescriptor(id=base64url_to_bytes(item["credential_id"])) for item in records]
        options = generate_authentication_options(rp_id=_rp(request), allow_credentials=allow)
        challenge_id = remember("authenticate", options.challenge, request)
        return {"challenge_id": challenge_id, "options": json.loads(options_to_json(options))}

    @app.post("/api/admin/passkeys/auth/verify")
    async def auth_verify(body: PasskeyCredential, request: Request):
        check_admin_ip(request, config)
        item = consume(body.challenge_id, "authenticate")
        credential_id = str(body.credential.get("id") or "")
        record = queue.store.passkey_by_credential(credential_id)
        if not record:
            raise HTTPException(401, "Unknown admin passkey.")
        try:
            from webauthn import verify_authentication_response
            from webauthn.helpers import base64url_to_bytes
            verified = verify_authentication_response(
                credential=body.credential,
                expected_challenge=item["challenge"], expected_rp_id=item["rp"],
                expected_origin=item["origin"], credential_public_key=base64url_to_bytes(record["public_key"]),
                credential_current_sign_count=int(record.get("sign_count") or 0), require_user_verification=True,
            )
        except Exception as exc:
            raise HTTPException(401, "Passkey sign-in could not be verified.") from exc
        queue.store.update_passkey_use(record["id"], int(verified.new_sign_count))
        queue.store.audit("admin", "passkey.login", record["id"])
        return session_response(config, key)
