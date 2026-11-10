"""Chat text helpers live in text_entities; behaviour must stay stable."""

from apps.api.services import text_entities as te


def test_norm_ru_yo():
    assert te.norm_ru("Ёлка") == "елка"
    assert te._norm_ru("Тест") == "тест"


def test_tokenize_filters_stop():
    toks = te.tokenize("Пушкин и что это Евгений Онегин")
    assert "пушкин" in toks
    assert "евгений" in toks
    assert "онегин" in toks
    assert "что" not in toks
    assert "это" not in toks


def test_extract_entity_candidates_finds_person_like():
    texts = ["Татьяна Ларина встретила Онегина в саду."]
    found = te.extract_entity_candidates(texts, limit=20)
    norms = {te.norm_ru(x) for x in found}
    assert "татьяна ларина" in norms or "онегина" in norms or "онегин" in norms


def test_extract_skips_deny_and_short():
    texts = ["Документ источник вопрос ID."]
    found = te.extract_entity_candidates(texts, limit=20)
    norms = {te.norm_ru(x) for x in found}
    assert "документ" not in norms
    assert "вопрос" not in norms
    assert "id" not in norms


def test_passage_only_keeps_body():
    raw = "Текст рассказа.\n@highlight\nВажная строка\ncloze: meta"
    body = te.passage_only(raw)
    assert "Текст рассказа" in body
    assert "Важная строка" in body
