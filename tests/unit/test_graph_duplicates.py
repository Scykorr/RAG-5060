"""E1: duplicates + contained_in + contains; DF once on canonical."""

from __future__ import annotations

from apps.api.services import graph_duplicates as gd
from apps.api.services import graph_index as gi


def _anna_texts():
    # Distinct long body so fragments are proper subsets
    body = (
        "Все счастливые семьи похожи друг на друга, каждая несчастливая семья несчастлива по-своему. "
        "Все смешалось в доме Облонских. Жена узнала, что муж был в связи с бывшею в их доме француженкою-гувернанткой, "
        "и объявила мужу, что не может жить с ним в одном доме. Положение это продолжалось уже третий день "
        "и мучительно чувствовалось самими супругами, всеми членами семьи и домочадцами. "
        "Все члены семьи и домочадцы чувствовали, что нет более смысла в их сожительстве и что на каждом постоялом дворе "
        "случайно сошедшиеся люди более связаны между собой, чем они, члены семьи и домочадцы Облонских. "
        "Жена не выходила из своих комнат, мужа третий день не было дома. Дети бегали по всему дому как потерянные; "
        "англичанка поссорилась с экономкой и написала записку приятельнице, прося приискать ей новое место; "
        "повар ушел еще вчера со двора, во время обеда; черная кухарка и кучер просили расчета. "
    )
    # repeat to get many 8-shingles
    full = (body * 12).strip()
    eb = full[:-80] + " небольшая издательская правка в конце полного текста романа."
    frag1 = body * 2
    frag2 = (
        "Все смешалось в доме Облонских. Жена узнала, что муж был в связи с бывшею в их доме француженкою-гувернанткой, "
        "и объявила мужу, что не может жить с ним в одном доме. Положение это продолжалось уже третий день "
        "и мучительно чувствовалось самими супругами, всеми членами семьи и домочадцами. "
    ) * 3
    return full, eb, frag1, frag2


def test_anna_karenina_canonical_duplicate_contained():
    full, eb, frag1, frag2 = _anna_texts()
    docs = [
        gi.parse_doc_meta(document_id="full", filename="classic_0041_Tolstoy_Анна_Каренина_aaaaaaaa.txt"),
        gi.parse_doc_meta(document_id="eb", filename="classic_0423_eb_Tolstoy_Анна_Каренина_bbbbbbbb.txt"),
        gi.parse_doc_meta(document_id="f1", filename="classic_0537_ch_Tolstoy_Анна_Каренина__фрагмент_1_cccccccc.txt"),
        gi.parse_doc_meta(document_id="f2", filename="classic_0538_ch_Tolstoy_Анна_Каренина__фрагмент_2_dddddddd.txt"),
    ]
    gi.resolve_work_keys(docs)
    assert len({d.work_key for d in docs}) == 1
    texts = {"full": full, "eb": eb, "f1": frag1, "f2": frag2}
    m = gd.detect_relations(docs, texts)
    assert m["full"]["canonical"] is True
    assert m["eb"]["relation"] == "duplicate"
    assert m["eb"]["duplicate_of"] == "full"
    assert m["f1"]["relation"] == "contained_in"
    assert m["f1"]["contained_in"] == "full"
    assert m["f2"]["relation"] == "contained_in"
    assert m["f2"]["contained_in"] == "full"
    canon = gd.canonical_doc_ids(m)
    assert canon == {"full"}


def test_df_counts_canonical_once():
    docs = [
        gi.parse_doc_meta(document_id="a", filename="classic_0001_ch_Gogol_Нос_aaaaaaaa.txt"),
        gi.parse_doc_meta(document_id="b", filename="classic_0002_eb_Gogol_Нос_bbbbbbbb.txt"),
        gi.parse_doc_meta(document_id="c", filename="classic_0003_Pushkin_Пир_cccccccc.txt"),
    ]
    gi.resolve_work_keys(docs)
    body = ("Майор Ковалев потерял свой нос на Невском проспекте утром. " * 40)
    texts = {"a": body, "b": body, "c": "Совершенно другой текст пира во время чумы и пирашества. " * 20}
    m = gd.detect_relations(docs, texts)
    canon = gd.canonical_doc_ids(m)
    assert len(canon & {"a", "b"}) == 1
    assert "c" in canon


def test_story_inside_collection_becomes_contains_not_refers():
    story = (
        "Началась драма на охоте ранним утром когда граф вышел с ружьем. "
        "Далее следует полный текст рассказа драма на охоте с диалогами героев и описанием леса. "
        "Охотники спорили о выстреле и о следах зверя у опушки. "
    ) * 10
    collection = (
        "Сборник рассказов и юморесок. Оглавление. Драма на охоте. "
        + story
        + " Конец сборника с другими произведениями автора и послесловием редактора. " * 5
    )
    docs = [
        gi.parse_doc_meta(
            document_id="col",
            filename="classic_0100_Chekhov_Рассказы_и_юморески_aaaaaaaa.txt",
        ),
        gi.parse_doc_meta(
            document_id="story",
            filename="classic_0101_Chekhov_Драма_на_охоте_bbbbbbbb.txt",
        ),
    ]
    gi.resolve_work_keys(docs)
    texts = {"col": collection, "story": story}
    m = gd.detect_relations(docs, texts)
    assert m["story"]["relation"] == "contained_in"
    assert m["story"]["contained_in"] == "col"
    edges = gd.contains_edges_from_map(m, edge_cls=gi.Edge)
    assert any(e.type == "contains" and e.source == "col" and e.target == "story" for e in edges)
    # refers_to from collection to story → rewritten to contains
    fake_refers = [
        gi.Edge(
            source="col",
            target="story",
            type="refers_to",
            weight=1.0,
            provenance={"phrase": "Драма на охоте"},
        )
    ]
    rewritten = gd.rewrite_refers_to_contains(fake_refers, m, edge_cls=gi.Edge)
    assert len(rewritten) == 1
    assert rewritten[0].type == "contains"
    assert not any(e.type == "refers_to" for e in rewritten)


def test_assemble_isolated_content_vs_structural():
    docs = [
        gi.parse_doc_meta(document_id="lonely", filename="classic_0001_Pushkin_Пир_aaaaaaaa.txt"),
        gi.parse_doc_meta(document_id="other", filename="classic_0002_Tolstoy_Казаки_bbbbbbbb.txt"),
    ]
    for d in docs:
        d.chunk_count = 5
        d.canonical = True
    edges = gi.layer_a_edges(docs)
    payload = gi.assemble_index(
        tenant_id="t",
        fingerprint="fp",
        docs=docs,
        edges=edges,
        params=gi.GraphIndexParams(),
        built_ms=1,
    )
    stats = payload["stats"]
    assert stats["isolated_content"] == 2
    assert stats["isolated_structural"] == 0
    for row in stats.get("top_works_by_degree") or []:
        assert not str(row["node"]).startswith("author:")
