from engine.lab_tenants import host_of, is_lab_tenant_host, lab_tenant_hosts


def test_entries_are_hosts_matched_case_insensitively():
    raw = "https://API-Deathstar.xdr.us.paloaltonetworks.com/ , ,lab.example.test"
    assert lab_tenant_hosts(raw) == frozenset(
        {"api-deathstar.xdr.us.paloaltonetworks.com", "lab.example.test"})
    assert is_lab_tenant_host("api-deathstar.XDR.us.paloaltonetworks.com", raw)


def test_empty_setting_makes_nothing_a_lab_tenant():
    assert lab_tenant_hosts("") == frozenset()
    assert not is_lab_tenant_host("api-deathstar.xdr.us.paloaltonetworks.com", "")


def test_unknown_or_missing_host_is_not_lab():
    assert not is_lab_tenant_host(None, "lab.example.test")
    assert not is_lab_tenant_host("other.example.test", "lab.example.test")


def test_host_of_reads_the_collector_url():
    assert host_of("https://API-x.example.test/logs/v1/event") == "api-x.example.test"
    assert host_of("not a url") is None


def test_setting_is_read_at_call_time(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "CORTEXSIM_DOOR_PROBE_TENANTS", "a.example.test", raising=False)
    assert is_lab_tenant_host("a.example.test")
    monkeypatch.setattr(settings, "CORTEXSIM_DOOR_PROBE_TENANTS", "", raising=False)
    assert not is_lab_tenant_host("a.example.test")
