"""Per-tenant request bucket on the tenant quota ledger."""
import pytest

from connectors import tuning
from integrations.xsiam import ledger as ledger_mod
from integrations.xsiam.ledger import (
    QuotaBreakerOpen,
    RequestBudgetExhausted,
    TenantQuotaLedger,
)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_bucket_admits_per_minute_then_refuses_with_retry_after():
    led = TenantQuotaLedger(clock=Clock())
    led.acquire_request("t1", per_minute=2)
    led.acquire_request("t1", per_minute=2)
    with pytest.raises(RequestBudgetExhausted) as exc:
        led.acquire_request("t1", per_minute=2)
    assert exc.value.tenant == "t1"
    assert isinstance(exc.value.retry_after_seconds, int)
    assert exc.value.retry_after_seconds >= 1


def test_bucket_refills_with_time():
    clock = Clock()
    led = TenantQuotaLedger(clock=clock)
    led.acquire_request("t1", per_minute=1)
    with pytest.raises(RequestBudgetExhausted):
        led.acquire_request("t1", per_minute=1)
    clock.t += 61
    led.acquire_request("t1", per_minute=1)


def test_buckets_are_per_tenant():
    led = TenantQuotaLedger(clock=Clock())
    led.acquire_request("a", per_minute=1)
    led.acquire_request("b", per_minute=1)
    with pytest.raises(RequestBudgetExhausted):
        led.acquire_request("a", per_minute=1)


def test_changing_per_minute_rebuilds_the_bucket():
    led = TenantQuotaLedger(clock=Clock())
    led.acquire_request("a", per_minute=1)
    with pytest.raises(RequestBudgetExhausted):
        led.acquire_request("a", per_minute=1)
    led.acquire_request("a", per_minute=5)


def test_open_breaker_refuses_a_request_before_the_bucket():
    led = TenantQuotaLedger(clock=Clock())
    led.trip("t1", cooldown_seconds=60)
    with pytest.raises(QuotaBreakerOpen):
        led.acquire_request("t1", per_minute=10)


def test_reset_clears_request_buckets():
    led = TenantQuotaLedger(clock=Clock())
    led.acquire_request("t1", per_minute=1)
    with pytest.raises(RequestBudgetExhausted):
        led.acquire_request("t1", per_minute=1)
    led.reset()
    led.acquire_request("t1", per_minute=1)


def test_default_ledger_still_constructs_without_args():
    assert ledger_mod.TenantQuotaLedger() is not None


def test_max_requests_per_minute_reads_env_default_60(monkeypatch):
    monkeypatch.delenv("CORTEXSIM_XSIAM_MAX_REQUESTS_PER_MINUTE", raising=False)
    assert tuning.xsiam_max_requests_per_minute() == 60
    monkeypatch.setenv("CORTEXSIM_XSIAM_MAX_REQUESTS_PER_MINUTE", "7")
    assert tuning.xsiam_max_requests_per_minute() == 7
    monkeypatch.setenv("CORTEXSIM_XSIAM_MAX_REQUESTS_PER_MINUTE", "0")
    assert tuning.xsiam_max_requests_per_minute() == 1
    assert tuning.xsiam_max_requests_per_minute(5) == 5
