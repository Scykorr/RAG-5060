from apps.api.services.quality_scoring import contain_hit, level_from_score, norm_ru


def test_norm_ru():
    assert "еж" in norm_ru("Ёж")
    assert "vat" in norm_ru("VAT 20%")


def test_contain_hit():
    assert contain_hit("Итого 12500 руб, НДС 20%", ["12500", "20%"])
    assert not contain_hit("нет данных", ["12500"])


def test_levels():
    assert level_from_score(0.95) == "strong"
    assert level_from_score(0.8) == "good"
    assert level_from_score(0.6) == "ok"
    assert level_from_score(0.2) == "low"
