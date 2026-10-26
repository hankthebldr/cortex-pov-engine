from engine.ttp_catalog import _parse_prevention_list, _slug

def test_prevention_objects_parse_with_module_and_slug():
    cards = _parse_prevention_list("TTP-TEST-PREV-001", [{
        "name": "BTP reverse shell from web server", "module": "btp",
        "platforms": ["linux"], "alert_name_pattern": "Behavioral Threat*"}])
    c = cards[0]
    assert c.kind == "prevention" and c.module == "btp" and c.platforms == ["linux"]
    assert c.detection_id == _slug("BTP reverse shell from web server", "prevention")
    assert c.logic == "Behavioral Threat*"
