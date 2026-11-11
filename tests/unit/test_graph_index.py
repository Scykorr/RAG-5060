"""Unit tests for connectivity index layers (no real DB)."""

from __future__ import annotations

from collections import Counter

from apps.api.services import graph_index as gi
from apps.api.services import text_entities as te


def test_parse_eb_russian_wedding():
    meta = gi.parse_doc_meta(
        document_id="d1",
        filename="classic_0383_eb_Russian_Свадьба_50e15c757f.txt",
    )
    assert meta.theme == "classic_"
    assert meta.source == "eb"
    assert meta.author_key is None
    assert "Russian" not in meta.title
    assert "Свадьба" in meta.title
    assert meta.fragment_no is None
    assert meta.work_key.startswith("classic|_|")


def test_parse_ch_tolstoy_fragment():
    meta = gi.parse_doc_meta(
        document_id="d2",
        filename="classic_0647_ch_Tolstoy_Анна_Каренина__фрагмент_111_f2a57d47fc.txt",
    )
    assert meta.source == "ch"
    assert meta.author_key == "Tolstoy"
    assert meta.author_ru == "толстой"
    assert meta.title == "Анна Каренина"
    assert "Tolstoy" not in meta.title
    assert meta.fragment_no == 111
    assert "Tolstoy" in meta.work_key


def test_parse_plain_tolstoy():
    meta = gi.parse_doc_meta(
        document_id="d3",
        filename="classic_0185_Tolstoy_Три_смерти_9a4ad18bf3c0.txt",
    )
    assert meta.source is None
    assert meta.author_key == "Tolstoy"
    assert "Три" in meta.title and "смерти" in meta.title


def test_fragments_same_work_no_textual_edges():
    a = gi.parse_doc_meta(
        document_id="a",
        filename="classic_0100_ch_Tolstoy_Анна_Каренина__фрагмент_1_aaaaaaaa.txt",
    )
    b = gi.parse_doc_meta(
        document_id="b",
        filename="classic_0101_ch_Tolstoy_Анна_Каренина__фрагмент_2_bbbbbbbb.txt",
    )
    assert a.work_key == b.work_key
    docs = [a, b]
    chunks = [
        ("a", "c1", "Анна Каренина вошла в комнату. Левин смотрел."),
        ("b", "c2", "Анна Каренина села. Левин ответил."),
    ]
    refers = gi.layer_b_refers_to(docs=docs, chunk_iter=chunks)
    assert refers == []
    shared = gi.layer_c_shared_entity(
        docs=docs,
        doc_entities={"a": ["Левин", "Анна"], "b": ["Левин", "Анна"]},
    )
    assert shared == []
    lex, _ = gi.layer_d_lex_similar(
        docs=docs,
        doc_term_counts={
            "a": Counter({"каренина": 5, "левин": 4, "комната": 2}),
            "b": Counter({"каренина": 5, "левин": 4, "ответил": 2}),
        },
        params=gi.GraphIndexParams(lex_min_score=0.01),
    )
    assert lex == []


def test_refers_blocks_frequent_one_word_chelovek():
    docs = [
        gi.parse_doc_meta(document_id="src", filename="essay.txt"),
        gi.parse_doc_meta(document_id="hub", filename="classic_0001_Gorky_Человек_aaaaaaaa.txt"),
    ]
    # inflate df so «Человек» is not rare
    for i in range(20):
        docs.append(
            gi.parse_doc_meta(
                document_id=f"x{i}",
                filename=f"classic_{i:04d}_Pushkin_Человек_и_море_{i:08x}.txt",
            )
        )
    chunks = [("src", "c1", "Он говорил о Человеке долго.")]
    edges = gi.layer_b_refers_to(docs=docs, chunk_iter=chunks)
    assert not any(e.target == "hub" for e in edges)


def test_refers_multiword_with_case_inflection():
    docs = [
        gi.parse_doc_meta(document_id="src", filename="crit.txt"),
        gi.parse_doc_meta(
            document_id="hero",
            filename="classic_0002_Lermontov_Герой_нашего_времени_bbbbbbbb.txt",
        ),
    ]
    chunks = [
        ("src", "c1", "Критик разобрал «Герой нашего времени» подробно."),
        ("src", "c2", "Ещё раз о книге Герой нашего времени."),
    ]
    edges = gi.layer_b_refers_to(docs=docs, chunk_iter=chunks)
    hit = [e for e in edges if e.source == "src" and e.target == "hero"]
    assert hit
    assert hit[0].weight >= 1
    assert hit[0].provenance.get("phrase")


def test_entity_cleanup_and_case_fold():
    assert te.clean_entity_surface("Когда") is None or te.norm_ru(te.clean_entity_surface("Когда") or "") in te.EDITION_STOP | te.RU_FUNCTION_WORDS
    assert te.clean_entity_surface("Впрочем") is None
    assert te.clean_entity_surface("Том") is None
    assert te.clean_entity_surface("ДЕЙСТВУЮЩИЕ ЛИЦА") is None
    assert te.clean_entity_surface("Гоголь\n\nМертвые") is None
    assert gi._entity_key("Левина") == gi._entity_key("Левину")


def test_shared_entity_keeps_real_names():
    docs = [
        gi.parse_doc_meta(document_id="d1", filename="classic_0001_Tolstoy_Анна_aaaaaaaa.txt"),
        gi.parse_doc_meta(document_id="d2", filename="classic_0002_Tolstoy_Война_bbbbbbbb.txt"),
    ]
    # different works
    assert docs[0].work_key != docs[1].work_key
    edges = gi.layer_c_shared_entity(
        docs=docs,
        doc_entities={
            "d1": ["Левин", "Кити", "Когда", "Том"],
            "d2": ["Левину", "Кити", "Впрочем"],
        },
    )
    assert edges
    ents = edges[0].provenance.get("entities") or []
    joined = " ".join(ents).lower()
    assert "левин" in joined or "кити" in joined
    assert "когда" not in joined
    assert "том" not in joined


def test_lex_similar_russian_texts():
    docs = [
        gi.parse_doc_meta(document_id="a", filename="classic_0001_Pushkin_Онегин_aaaaaaaa.txt"),
        gi.parse_doc_meta(document_id="b", filename="classic_0002_Pushkin_Пир_bbbbbbbb.txt"),
        gi.parse_doc_meta(document_id="c", filename="wildlife_0001_bear_taiga_cccccccc.txt"),
    ]
    # many fake works to create df>1 for shared rare stems
    extra = []
    for i in range(8):
        extra.append(
            gi.parse_doc_meta(
                document_id=f"e{i}",
                filename=f"classic_{i+10:04d}_Chekhov_Рассказ_{i}_dddddddd.txt",
            )
        )
    docs.extend(extra)
    # Content stems only (no character names — those are excluded by A2).
    terms = {
        "a": Counter({"дуэль": 5, "садик": 4, "романс": 4, "строф": 3}),
        "b": Counter({"дуэль": 5, "садик": 4, "романс": 3, "пиршество": 3}),
        "c": Counter({"медведь": 6, "тайга": 5, "берлога": 3}),
    }
    for i in range(8):
        terms[f"e{i}"] = Counter({"рассказик": 3, "дачник": 2, f"уник{i}": 2})
    counter = gi.LexCandidateCounter()
    edges, debug = gi.layer_d_lex_similar(
        docs=docs,
        doc_term_counts=terms,
        params=gi.GraphIndexParams(lex_min_score=0.05, lex_top_m=20),
        counter=counter,
    )
    pairs = {tuple(sorted((e.source, e.target))) for e in edges}
    assert ("a", "b") in pairs
    assert ("a", "c") not in pairs
    assert debug.get("bugfix")
    assert counter.comparisons >= 0


def test_co_cited_live_vs_eval():
    assert gi.is_eval_chat_session("classic-smoke")
    assert gi.is_eval_chat_session("intent-smoke")
    assert gi.is_eval_chat_session("ping")
    assert not gi.is_eval_chat_session("сколько произведений Чехова в хранилище?")
    edges, _ = gi.layer_e_co_cited(
        [(["d1", "d2"], False), (["d1", "d2"], True), (["d3", "d4"], True)],
        include_eval=False,
    )
    assert any(e.type == "co_cited" and set((e.source, e.target)) == {"d1", "d2"} for e in edges)
    assert not any(e.type == "co_cited_eval" for e in edges)
    edges2, _ = gi.layer_e_co_cited(
        [(["d3", "d4"], True)],
        include_eval=True,
    )
    assert any(e.type == "co_cited_eval" for e in edges2)


def test_layer_a_part_of_and_work_meta():
    docs = [
        gi.parse_doc_meta(
            document_id="a",
            filename="classic_0100_ch_Tolstoy_Анна_Каренина__фрагмент_1_aaaaaaaa.txt",
        ),
        gi.parse_doc_meta(
            document_id="b",
            filename="classic_0101_ch_Tolstoy_Анна_Каренина__фрагмент_2_bbbbbbbb.txt",
        ),
    ]
    edges = gi.layer_a_edges(docs)
    assert any(e.type == "part_of" for e in edges)
    meta = [e for e in edges if e.type == "work_meta"]
    assert meta and meta[0].provenance.get("fragment_count") == 2


def test_cache_write_read_and_purge(tmp_path, monkeypatch):
    monkeypatch.setattr("apps.api.services.storage.data_root", lambda: tmp_path)
    monkeypatch.setattr("apps.api.services.graph_index_cache.data_root", lambda: tmp_path)
    (tmp_path / "graph").mkdir(parents=True)
    tenant = "11111111-1111-1111-1111-111111111111"
    docs = [gi.parse_doc_meta(document_id="d1", filename="classic_0001_Pushkin_Пир_aa111111.txt")]
    params = gi.GraphIndexParams()
    payload = gi.assemble_index(
        tenant_id=tenant,
        fingerprint="fp_old",
        docs=docs,
        edges=gi.layer_a_edges(docs),
        params=params,
        built_ms=1,
    )
    old = gi.index_path(tenant, "fp_old", params=params)
    gi.write_index_atomic(old, payload)
    assert gi.read_index(old)["fingerprint"] == "fp_old"
    new_payload = dict(payload)
    new_payload["fingerprint"] = "fp_new"
    new = gi.index_path(tenant, "fp_new", params=params)
    gi.write_index_atomic(new, new_payload)
    removed = gi.purge_old_indexes(tenant, new, keep_n=1)
    assert removed >= 1
    assert not old.exists()
    assert new.exists()


def test_mentions_author_not_refers_to_work():
    docs = [
        gi.parse_doc_meta(
            document_id="poor",
            filename="classic_0001_Dostoevsky_Бедные_люди_aaaaaaaa.txt",
        ),
        gi.parse_doc_meta(
            document_id="onegin",
            filename="classic_0002_Pushkin_Евгений_Онегин_bbbbbbbb.txt",
        ),
    ]
    chunks = [
        ("poor", "c1", "Варенька читала Пушкина и плакала над стихами."),
    ]
    mentions = gi.layer_mentions_author(docs=docs, chunk_iter=chunks)
    assert any(e.target == "author:Pushkin" and e.source == "poor" for e in mentions)
    refers = gi.layer_b_refers_to(docs=docs, chunk_iter=chunks)
    assert not any(e.source == "poor" and e.target == "onegin" for e in refers)


def test_self_author_mention_skipped():
    docs = [
        gi.parse_doc_meta(
            document_id="p",
            filename="classic_0001_Pushkin_Пир_aaaaaaaa.txt",
        ),
    ]
    chunks = [("p", "c1", "Пушкин писал эти строки зимой.")]
    mentions = gi.layer_mentions_author(docs=docs, chunk_iter=chunks)
    assert mentions == []


def test_refers_requires_strong_form_for_common_and_one_word():
    docs = [
        gi.parse_doc_meta(document_id="src", filename="essay.txt"),
        gi.parse_doc_meta(
            document_id="rail",
            filename="classic_0001_Nekrasov_Железная_дорога_aaaaaaaa.txt",
        ),
        gi.parse_doc_meta(
            document_id="idiot",
            filename="classic_0002_Dostoevsky_Идиот_bbbbbbbb.txt",
        ),
    ]
    weak = [("src", "c1", "Он шёл вдоль железной дороги и думал о будущем.")]
    assert gi.layer_b_refers_to(docs=docs, chunk_iter=weak) == []

    strong = [("src", "c2", "В поэме «Железная дорога» Некрасов говорит о труде.")]
    edges = gi.layer_b_refers_to(docs=docs, chunk_iter=strong)
    assert any(e.target == "rail" for e in edges)
    assert edges[0].provenance.get("match_form") in {"quoted", "genre_marker", "near_author"}

    one_weak = [("src", "c3", "Идиот стоял у двери и молчал.")]
    assert not any(e.target == "idiot" for e in gi.layer_b_refers_to(docs=docs, chunk_iter=one_weak))

    one_strong = [("src", "c4", "Критик разобрал роман «Идиот» Достоевского.")]
    edges2 = gi.layer_b_refers_to(docs=docs, chunk_iter=one_strong)
    assert any(e.target == "idiot" for e in edges2)


def test_gorky_not_from_gorko_adjective():
    docs = [
        gi.parse_doc_meta(document_id="src", filename="wedding.txt"),
        gi.parse_doc_meta(
            document_id="mother",
            filename="classic_0001_Gorky_Мать_aaaaaaaa.txt",
        ),
    ]
    weak = [("src", "c1", "Горько! Горько! — кричали гости на свадьбе.")]
    mentions = gi.layer_mentions_author(docs=docs, chunk_iter=weak)
    assert not any(e.target == "author:Gorky" for e in mentions)

    strong = [("src", "c2", "М. Горький описал это в повести.")]
    mentions2 = gi.layer_mentions_author(docs=docs, chunk_iter=strong)
    assert any(e.target == "author:Gorky" for e in mentions2)


def test_sentence_start_entities_dropped_by_position_rule():
    from apps.api.services.text_entities import norm_ru

    docs = [
        gi.parse_doc_meta(document_id="d1", filename="classic_0001_Tolstoy_Анна_aaaaaaaa.txt"),
        gi.parse_doc_meta(document_id="d2", filename="classic_0002_Dostoevsky_Бесы_bbbbbbbb.txt"),
    ]
    noise = [
        "Зачем",
        "Наконец",
        "Разумеется",
        "Почему",
        "Кто",
        "Пусть",
        "Видите",
        "Прощай",
        "Войдя",
        "Поравнявшись",
        "Конечно",
        "Таков",
        "Погода",
        "Вронский",
        "Смердяков",
        "Покровское",
    ]
    keep = {"Вронский", "Смердяков", "Покровское"}
    pos = {
        norm_ru(x): {"sent_start": 20, "mid": 1}
        for x in noise
        if x not in keep
    }
    pos[norm_ru("Вронский")] = {"sent_start": 2, "mid": 18}
    pos[norm_ru("Смердяков")] = {"sent_start": 1, "mid": 15}
    pos[norm_ru("Покровское")] = {"sent_start": 3, "mid": 12}
    edges = gi.layer_c_shared_entity(
        docs=docs,
        doc_entities={"d1": noise, "d2": noise},
        pos_stats=pos,
        params=gi.GraphIndexParams(shared_min_entities=2),
    )
    assert edges
    joined = " ".join((edges[0].provenance.get("entities") or [])).lower()
    for bad in ("зачем", "наконец", "разумеется", "почему", "кто", "пусть", "видите", "прощай"):
        assert bad not in joined
    assert "вронский" in joined or "смердяков" in joined or "покровское" in joined


def test_lex_ignores_name_only_overlap():
    docs = [
        gi.parse_doc_meta(document_id="a", filename="classic_0001_Turgenev_Накануне_aaaaaaaa.txt"),
        gi.parse_doc_meta(document_id="b", filename="classic_0002_Tolstoy_Анна_Каренина_bbbbbbbb.txt"),
    ]
    # only shared name stems
    terms = {
        "a": Counter({"анна": 5, "александрович": 4, "ивановн": 3}),
        "b": Counter({"анна": 6, "александрович": 5, "ивановн": 4}),
    }
    case = {
        "анна": {"upper": 20, "lower": 1},
        "александрович": {"upper": 15, "lower": 0},
        "ивановн": {"upper": 12, "lower": 0},
    }
    edges, _ = gi.layer_d_lex_similar(
        docs=docs,
        doc_term_counts=terms,
        params=gi.GraphIndexParams(lex_min_score=0.01),
        case_stats=case,
    )
    assert edges == []


def test_work_parts_and_volumes_merge():
    a = gi.parse_doc_meta(
        document_id="a",
        filename="classic_0001_Dostoevsky_Братья_Карамазовы_aaaaaaaa.txt",
    )
    b = gi.parse_doc_meta(
        document_id="b",
        filename="classic_0002_Dostoevsky_Братья_Карамазовы_-_Часть_3_bbbbbbbb.txt",
    )
    c = gi.parse_doc_meta(
        document_id="c",
        filename="classic_0003_Tolstoy_Война_и_мир_Том_2_cccccccc.txt",
    )
    d = gi.parse_doc_meta(
        document_id="d",
        filename="classic_0004_Tolstoy_Война_и_мир_Том_3_dddddddd.txt",
    )
    docs = [a, b, c, d]
    gi.resolve_work_keys(docs)
    assert a.work_key == b.work_key
    assert "часть" not in a.work_key
    assert c.work_key == d.work_key
    assert c.volume_no == 2
    assert d.volume_no == 3


def test_work_weight_not_linear_in_fragments():
    docs = []
    edges = []
    # 20 fragment pairs all referring with weight 1 → raw_sum=20
    for i in range(20):
        a = gi.parse_doc_meta(
            document_id=f"a{i}",
            filename=f"classic_{i:04d}_ch_Tolstoy_Анна_Каренина__фрагмент_{i}_aaaaaaaa.txt",
        )
        b = gi.parse_doc_meta(
            document_id=f"b{i}",
            filename=f"classic_{100+i:04d}_Dostoevsky_Идиот__фрагмент_{i}_bbbbbbbb.txt",
        )
        docs.extend([a, b])
        edges.append(
            gi.Edge(
                source=a.document_id,
                target=b.document_id,
                type="refers_to",
                weight=1.0,
                provenance={"phrase": "Идиот", "match_form": "quoted"},
            )
        )
    work_edges = gi.aggregate_work_edges(docs, edges)
    wr = [e for e in work_edges if e.type == "work_refers_to"]
    assert wr
    # linear sum would be 20; log1p(20)/sqrt(20*20) ≈ 3.04/20 ≈ 0.15
    assert wr[0].weight < 5.0
    assert wr[0].provenance.get("agg") == "log1p_sum_over_sqrt_frags"
