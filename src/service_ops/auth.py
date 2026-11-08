import asyncio
import json
import math
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from service_ops.models import ProviderError


@dataclass(repr=False)
class Token:
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0


class TokenStore:
    def __init__(self, path: Path | None):
        self.path = path

    def load(self) -> Token | None:
        if self.path is None or not self.path.exists():
            return None
        try:
            if self.path.is_symlink() or self.path.stat().st_mode & 0o077:
                raise ValueError("private file required")
            data = json.loads(self.path.read_text())
            token = Token(**data)
            if not isinstance(token.access_token, str) or not isinstance(token.refresh_token, str):
                raise ValueError("invalid token")
            if not isinstance(token.expires_at, (int, float)):
                raise ValueError("invalid expiry")
            if not math.isfinite(token.expires_at):
                raise ValueError("invalid expiry")
            return token
        except (OSError, ValueError, TypeError):
            raise ProviderError("oauth", "invalid_private_token_file") from None

    def save(self, token: Token) -> None:
        if self.path is None:
            return
        temporary: str | None = None
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.path.is_symlink():
                raise OSError("symlink token file")
            fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".token-")
            with os.fdopen(fd, "w") as output:
                os.fchmod(output.fileno(), 0o600)
                json.dump(token.__dict__, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        except OSError:
            raise ProviderError("oauth", "token_persistence_failed") from None
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)


@dataclass(repr=False)
class OAuth:
    provider: str
    client: httpx.AsyncClient
    endpoint: str
    client_id: str
    client_secret: str
    store: TokenStore
    token: Token
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def access(self, rejected_token: str | None = None) -> str:
        async with self.lock:
            expired = self.token.expires_at <= time.time() + 30
            rejected = rejected_token is not None and rejected_token == self.token.access_token
            if not expired and not rejected:
                return self.token.access_token
            form = {"grant_type": "refresh_token", "refresh_token": self.token.refresh_token}
            auth = None
            if self.provider == "zoho":
                form.update(client_id=self.client_id, client_secret=self.client_secret)
            else:
                auth = httpx.BasicAuth(self.client_id, self.client_secret)
            try:
                response = await self.client.post(self.endpoint, data=form, auth=auth)
                if response.status_code != 200:
                    raise ProviderError(self.provider, "oauth_refresh_failed")
                body = response.json()
                access = body["access_token"]
                refresh = body.get("refresh_token", self.token.refresh_token)
                expiry = float(body["expires_in"])
                if not isinstance(access, str) or not access or not isinstance(refresh, str):
                    raise ValueError("invalid token")
                if not 30 < expiry <= 86400 or not refresh:
                    raise ValueError("invalid expiry")
                replacement = Token(access, refresh, time.time() + expiry)
            except (httpx.HTTPError, ValueError, TypeError, KeyError):
                raise ProviderError(self.provider, "oauth_refresh_failed") from None
            self.store.save(replacement)
            self.token = replacement
            return replacement.access_token
