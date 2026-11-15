"""Thin XML-RPC client for Odoo with simple retry handling."""
from __future__ import annotations

import time
import xmlrpc.client
from typing import Any, Callable


class OdooError(Exception):
    """Raised when Odoo rejects a call or authentication fails."""


class OdooClient:
    """Minimal wrapper around Odoo's external XML-RPC API.

    ``proxy_factory`` lets tests inject a fake transport. It receives the
    endpoint name (``"common"`` or ``"object"``) and returns a proxy.
    """

    def __init__(
        self,
        url: str,
        db: str,
        username: str,
        password: str,
        retries: int = 3,
        backoff: float = 1.0,
        proxy_factory: Callable[[str], Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.url = url.rstrip("/")
        self.db = db
        self.username = username
        self._password = password
        self.retries = max(1, retries)
        self.backoff = backoff
        self._sleep = sleep
        self._proxy_factory = proxy_factory or self._default_proxy
        self._uid: int | None = None

    def _default_proxy(self, endpoint: str) -> xmlrpc.client.ServerProxy:
        return xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/{endpoint}")

    def _with_retries(self, func: Callable[[], Any]) -> Any:
        """Retry on network-level errors only; Odoo faults are not retried."""
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                return func()
            except xmlrpc.client.Fault as fault:
                raise OdooError(fault.faultString) from fault
            except (OSError, xmlrpc.client.ProtocolError) as error:
                last_error = error
                if attempt < self.retries - 1:
                    self._sleep(self.backoff * (2**attempt))
        raise OdooError(f"Connection failed after {self.retries} attempts: {last_error}")

    def authenticate(self) -> int:
        common = self._proxy_factory("common")
        uid = self._with_retries(
            lambda: common.authenticate(self.db, self.username, self._password, {})
        )
        if not uid:
            raise OdooError("Authentication failed: check database, user and password.")
        self._uid = uid
        return uid

    def call(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        if self._uid is None:
            self.authenticate()
        models = self._proxy_factory("object")
        return self._with_retries(
            lambda: models.execute_kw(
                self.db, self._uid, self._password, model, method, list(args), kwargs
            )
        )

    def search_read(
        self, model: str, domain: list, fields: list[str], limit: int | None = None
    ) -> list[dict]:
        kwargs: dict[str, Any] = {"fields": fields}
        if limit is not None:
            kwargs["limit"] = limit
        return self.call(model, "search_read", domain, **kwargs)

    def create(self, model: str, values: dict) -> int:
        return self.call(model, "create", values)

    def write(self, model: str, ids: list[int], values: dict) -> bool:
        return self.call(model, "write", ids, values)
