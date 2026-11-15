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
    with pytest.raises(OdooError, match="Authentication failed"):
        make_client(FakeProxy(auth_result=False)).authenticate()


def test_network_errors_are_retried_then_succeed():
    proxy = FakeProxy(fail_times=2)
    assert make_client(proxy, retries=3).authenticate() == 1
    assert proxy.attempts == 3


def test_gives_up_after_retries():
    proxy = FakeProxy(fail_times=10)
    with pytest.raises(OdooError, match="after 2 attempts"):
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
