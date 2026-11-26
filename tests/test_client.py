import xmlrpc.client

import pytest

from odoo_inventory_sync.client import OdooClient, OdooError


class FakeProxy:
    def __init__(self, auth_result=1, fail_times=0, fault=None):
        self.auth_result = auth_result
        self.fail_times = fail_times
        self.fault = fault
        self.attempts = 0

    def authenticate(self, db, user, password, ctx):
        self.attempts += 1
        if self.fault:
            raise self.fault
        if self.attempts <= self.fail_times:
            raise ConnectionError("network down")
        return self.auth_result

    def execute_kw(self, db, uid, password, model, method, args, kwargs):
        return [model, method, args, kwargs]


def make_client(proxy, retries=3):
    return OdooClient(
        "https://odoo.example.com/", "db", "user", "pass",
        retries=retries, proxy_factory=lambda endpoint: proxy, sleep=lambda s: None,
    )


def test_authenticate_success():
    assert make_client(FakeProxy(auth_result=5)).authenticate() == 5


def test_authenticate_rejected_credentials():
    with pytest.raises(OdooError, match="No se pudo iniciar sesión"):
        make_client(FakeProxy(auth_result=False)).authenticate()


def test_network_errors_are_retried_then_succeed():
    proxy = FakeProxy(fail_times=2)
    assert make_client(proxy, retries=3).authenticate() == 1
    assert proxy.attempts == 3


def test_gives_up_after_retries():
    proxy = FakeProxy(fail_times=10)
    with pytest.raises(OdooError, match="después de 2 intentos"):
        make_client(proxy, retries=2).authenticate()
    assert proxy.attempts == 2


def test_odoo_faults_are_not_retried():
    proxy = FakeProxy(fault=xmlrpc.client.Fault(1, "Access denied"))
    with pytest.raises(OdooError, match="Access denied"):
        make_client(proxy).authenticate()
    assert proxy.attempts == 1


def test_call_authenticates_lazily_and_passes_arguments():
    client = make_client(FakeProxy())
    out = client.search_read("product.product", [["a", "=", 1]], ["name"], limit=1)
    assert out[1] == "search_read"
    assert out[3] == {"fields": ["name"], "limit": 1}


# ---------------------------------------------------------------- JSON-2

import json


class FakeHttp:
    """Answers JSON-2 requests; records (url, headers, params)."""

    def __init__(self, routes):
        self.routes = routes  # {(model, method): (status, payload) or callable(params)}
        self.calls = []

    def __call__(self, url, headers, body):
        params = json.loads(body)
        model, method = url.split("/json/2/")[1].split("/")
        self.calls.append((model, method, params, headers))
        answer = self.routes.get((model, method), (404, None))
        if callable(answer):
            answer = answer(params)
        status, payload = answer
        if isinstance(payload, (bytes, str)):
            data = payload.encode() if isinstance(payload, str) else payload
        else:
            data = json.dumps(payload).encode() if payload is not None else b""
        return status, data


def json2_client(http, protocol="auto", retries=3):
    return OdooClient("https://odoo.example.com/", "jats", "user", "key", retries=retries,
                      protocol=protocol, http_post=http, sleep=lambda s: None)


OK_LOGIN = {("res.users", "context_get"): (200, {"lang": "es_EC"})}


def test_auto_uses_json2_when_offered():
    http = FakeHttp({**OK_LOGIN, ("product.product", "search_read"): (200, [{"id": 7}])})
    client = json2_client(http)
    assert client.search_read("product.product", [["active", "=", True]], ["id"], limit=1) == [{"id": 7}]
    assert client.protocol == "json2"
    model, method, params, headers = http.calls[-1]
    assert params == {"domain": [["active", "=", True]], "fields": ["id"], "limit": 1}
    assert headers["Authorization"] == "bearer key"
    assert headers["X-Odoo-Database"] == "jats"


def test_auto_falls_back_to_xmlrpc_on_older_odoo():
    http = FakeHttp({("res.users", "context_get"): (404, "<html>Not Found</html>")})
    proxy = FakeProxy(auth_result=9)
    client = OdooClient("https://odoo.example.com", "db", "u", "p", http_post=http,
                        sleep=lambda s: None)
    client._proxy_factory = lambda endpoint: proxy
    assert client.authenticate() == 9
    assert client.protocol == "xmlrpc"


def test_auto_falls_back_to_xmlrpc_when_the_key_is_a_password():
    http = FakeHttp({("res.users", "context_get"): (401, {"message": "Invalid apikey"})})
    client = OdooClient("https://odoo.example.com", "db", "u", "p", http_post=http,
                        sleep=lambda s: None)
    client._proxy_factory = lambda endpoint: FakeProxy(auth_result=3)
    assert client.authenticate() == 3
    assert client.protocol == "xmlrpc"


def test_json2_rejected_key_is_a_clear_login_error():
    http = FakeHttp({("res.users", "context_get"): (401, {"message": "Invalid apikey"})})
    with pytest.raises(OdooError, match="No se pudo iniciar sesión"):
        json2_client(http, protocol="json2").authenticate()


def test_positional_arguments_get_their_json2_names():
    http = FakeHttp({
        **OK_LOGIN,
        ("product.product", "read"): (200, [{"id": 1}]),
        ("product.product", "write"): (200, True),
        ("product.template", "create"): (200, [42]),
        ("stock.quant", "action_apply_inventory"): (200, None),
    })
    client = json2_client(http)
    client.call("product.product", "read", [1, 2], ["name"])
    assert http.calls[-1][2] == {"ids": [1, 2], "fields": ["name"]}
    client.write("product.product", [1], {"lst_price": 48})
    assert http.calls[-1][2] == {"ids": [1], "vals": {"lst_price": 48}}
    # One record in, one id out, as with XML-RPC.
    assert client.create("product.template", {"name": "Valentina"}) == 42
    assert http.calls[-1][2] == {"vals_list": [{"name": "Valentina"}]}
    assert client.call("stock.quant", "action_apply_inventory", [5],
                       context={"inventory_mode": True}) is None
    assert http.calls[-1][2] == {"ids": [5], "context": {"inventory_mode": True}}


def test_json2_errors_carry_odoo_message_and_are_not_retried():
    http = FakeHttp({**OK_LOGIN, ("product.category", "write"): (
        404, {"name": "odoo.exceptions.MissingError", "message": "Record does not exist"})})
    client = json2_client(http)
    with pytest.raises(OdooError, match="Record does not exist"):
        client.write("product.category", [999], {"name": "x"})
    assert sum(1 for c in http.calls if c[1] == "write") == 1


def test_json2_gateway_errors_are_retried():
    answers = iter([(503, None), (502, None), (200, [{"id": 1}])])
    http = FakeHttp({**OK_LOGIN, ("res.partner", "search_read"): lambda params: next(answers)})
    assert json2_client(http).search_read("res.partner", [], ["id"]) == [{"id": 1}]


def test_unknown_protocol_is_refused():
    with pytest.raises(OdooError, match="Protocolo desconocido"):
        OdooClient("https://x", "db", "u", "p", protocol="soap")
