"""Small Odoo client with simple retry handling.

Speaks Odoo's JSON-2 API (``/json/2``, Odoo 19 and later) and falls back to
the external XML-RPC API, which Odoo 19 deprecates and Odoo 22 removes.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import xmlrpc.client
from typing import Any, Callable

PROTOCOLS = ("auto", "json2", "xmlrpc")

# JSON-2 takes named arguments only. For the positional arguments callers pass
# (as with XML-RPC's execute_kw), these are their names, by method.
_FIRST_ARG_IS_DOMAIN = {"search", "search_read", "search_count", "read_group", "name_search"}
_SECOND_ARG = {"read": "fields", "write": "vals"}


class OdooError(Exception):
    """Raised when Odoo rejects a call or authentication fails."""


class _Unavailable(Exception):
    """JSON-2 is not offered by this server (or not with these credentials)."""


class OdooClient:
    """Minimal client for Odoo's external API.

    ``protocol`` is ``"json2"``, ``"xmlrpc"`` or ``"auto"`` (JSON-2 when the
    server offers it and accepts the key, otherwise XML-RPC). JSON-2 needs an
    API key; XML-RPC also accepts a password.

    ``proxy_factory`` lets tests inject a fake XML-RPC transport (and implies
    ``"xmlrpc"``). It receives the endpoint name (``"common"`` or ``"object"``)
    and returns a proxy. ``http_post`` lets tests replace the JSON-2 HTTP call:
    it receives ``(url, headers, body_bytes)`` and returns ``(status, body_bytes)``.
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
        protocol: str = "auto",
        http_post: Callable[[str, dict, bytes], tuple[int, bytes]] | None = None,
    ) -> None:
        if protocol not in PROTOCOLS:
            raise OdooError(f"Protocolo desconocido: {protocol} (usa {', '.join(PROTOCOLS)})")
        self.url = url.rstrip("/")
        self.db = db
        self.username = username
        self._password = password
        self.retries = max(1, retries)
        self.backoff = backoff
        self._sleep = sleep
        self._proxy_factory = proxy_factory or self._default_proxy
        self._http_post = http_post or self._default_http_post
        self._uid: int | None = None
        if proxy_factory is not None and protocol == "auto":
            protocol = "xmlrpc"
        self.protocol = protocol  # "auto" until the first call decides

    # ------------------------------------------------------------ transports
    def _default_proxy(self, endpoint: str) -> xmlrpc.client.ServerProxy:
        return xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/{endpoint}", allow_none=True)

    @staticmethod
    def _default_http_post(url: str, headers: dict, body: bytes) -> tuple[int, bytes]:
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def _with_retries(self, func: Callable[[], Any]) -> Any:
        """Retry on network-level errors only; Odoo errors are not retried."""
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
        raise OdooError(f"No se pudo conectar después de {self.retries} intentos: {last_error}")

    # ---------------------------------------------------------------- JSON-2
    def _json2(self, model: str, method: str, params: dict) -> Any:
        headers = {
            "Authorization": f"bearer {self._password}",
            "X-Odoo-Database": self.db,
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "odoo-inventory-sync",
        }
        body = json.dumps(params).encode("utf-8")
        url = f"{self.url}/json/2/{model}/{method}"

        def post():
            status, data = self._http_post(url, headers, body)
            if status in (502, 503, 504):
                raise OSError(f"HTTP {status}")
            return status, data

        status, data = self._with_retries(post)
        try:
            payload = json.loads(data.decode("utf-8")) if data else None
        except ValueError:
            payload = None
        if status == 200:
            return payload
        message = payload.get("message") if isinstance(payload, dict) else None
        # 401: key not accepted. 404 without an Odoo error: no JSON-2 on this
        # server (before Odoo 19). A 404 with one is a real error (a missing record).
        if status == 401 or (status == 404 and not message):
            raise _Unavailable(status)
        raise OdooError(message or f"Odoo respondió HTTP {status}")

    @staticmethod
    def _named(method: str, args: tuple, kwargs: dict) -> dict:
        params = dict(kwargs)
        args = list(args)
        if method == "create" and args:
            values = args.pop(0)
            params["vals_list"] = values if isinstance(values, list) else [values]
        elif args:
            first = "domain" if method in _FIRST_ARG_IS_DOMAIN else "ids"
            params[first] = args.pop(0)
        if args and method in _SECOND_ARG:
            params[_SECOND_ARG[method]] = args.pop(0)
        if args:
            raise OdooError(f"{method}: pasa los argumentos extra por nombre")
        return params

    def _call_json2(self, model: str, method: str, args: tuple, kwargs: dict) -> Any:
        result = self._json2(model, method, self._named(method, args, kwargs))
        if method == "create" and args and isinstance(args[0], dict) and isinstance(result, list):
            return result[0]  # same as XML-RPC: one record in, one id out
        return result

    # ------------------------------------------------------------ public API
    def _decide(self) -> None:
        """In "auto" mode, use JSON-2 if the server offers it and takes the key."""
        if self.protocol != "auto":
            return
        try:
            self._json2("res.users", "context_get", {})
            self.protocol = "json2"
        except _Unavailable:
            self.protocol = "xmlrpc"

    def authenticate(self) -> int:
        self._decide()
        if self.protocol == "json2":
            try:
                self._json2("res.users", "context_get", {})
            except _Unavailable as error:
                raise OdooError(
                    "No se pudo iniciar sesión: revisa la base de datos, el usuario y la clave de API"
                    f" (HTTP {error.args[0]})."
                ) from None
            self._uid = self._uid or 1
            return self._uid
        common = self._proxy_factory("common")
        uid = self._with_retries(
            lambda: common.authenticate(self.db, self.username, self._password, {})
        )
        if not uid:
            raise OdooError("No se pudo iniciar sesión: revisa la base de datos, el usuario y la clave de API.")
        self._uid = uid
        return uid

    def call(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        if self._uid is None:
            self.authenticate()
        if self.protocol == "json2":
            try:
                return self._call_json2(model, method, args, kwargs)
            except _Unavailable as error:
                raise OdooError(f"Odoo rechazó la llamada {model}.{method} (HTTP {error.args[0]})") from None
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
