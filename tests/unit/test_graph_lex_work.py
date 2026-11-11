"""E2: work-level lex, burstiness, mutual kNN, weak floor."""

from __future__ import annotations

from collections import Counter

from apps.api.services import graph_index as gi


def _docs_terms_theme_pair():
    """Two articles same theme, different length — should link at work level."""
    docs = [
        gi.parse_doc_meta(document_id="a", filename="classic_0001_Tolstoy_О_безумии_aaaaaaaa.txt"),
        gi.parse_doc_meta(document_id="b", filename="classic_0002_Tolstoy_Патриотизм_и_правительство_bbbbbbbb.txt"),
        gi.parse_doc_meta(document_id="c", filename="classic_0003_Chekhov_Палата_номер_шесть_cccccccc.txt"),
    ]
    gi.resolve_work_keys(docs)
    terms = {
        "a": Counter(
            {
                "патриотизм": 12,
                "правительств": 10,
                "безуми": 8,
                "войн": 7,
                "народ": 6,
                "власть": 5,
                "закон": 4,
            }
        ),
        "b": Counter(
            {
                "патриотизм": 20,
                "правительств": 18,
                "безуми": 4,
                "войн": 15,
                "народ": 12,
                "власть": 11,
                "закон": 9,
                "арми": 8,
            }
        ),
        "c": Counter({"палат": 10, "больниц": 8, "врач": 7, "больной": 6, "палатк": 2}),
    }
    # sprinkle style noise into all works
    for d in docs:
        terms[d.document_id]["вскричал"] = 3
        terms[d.document_id]["внезапно"] = 4
        terms[d.document_id]["входит"] = 5
    return docs, terms


def test_theme_articles_link_via_work_lex():
    docs, terms = _docs_terms_theme_pair()
    edges, debug = gi.layer_d_lex_similar(
        docs=docs,
        doc_term_counts=terms,
        params=gi.GraphIndexParams(lex_min_score=0.01, lex_weak_floor=0.01),
    )
    pairs = {(e.source, e.target) for e in edges} | {(e.target, e.source) for e in edges}
    assert ("a", "b") in pairs or ("b", "a") in pairs
    assert debug.get("n_works") == 3


def test_burstiness_drops_style_keeps_theme():
    """Synthetic: style terms in every work; theme terms concentrated."""
    docs = []
    terms: dict[str, Counter[str]] = {}
    for i in range(12):
        did = f"d{i}"
        docs.append(
            gi.parse_doc_meta(
                document_id=did,
                filename=f"classic_{i:04d}_Tolstoy_Статья_{i}_aaaaaaaa.txt",
            )
        )
        c = Counter({"вскричал": 2, "внезапно": 2, "давеча": 2, "входит": 2})
        if i < 3:
            c["присяжн"] = 15
            c["суд"] = 12
            c["защит"] = 10
            c["заседател"] = 8
        if i >= 3 and i < 6:
            c["казак"] = 14
            c["станц"] = 11
            c["степ"] = 9
            c["сабл"] = 7
        terms[did] = c
    gi.resolve_work_keys(docs)
    edges, debug = gi.layer_d_lex_similar(
        docs=docs,
        doc_term_counts=terms,
        params=gi.GraphIndexParams(lex_min_score=0.01, lex_burst_max_ratio=0.85, lex_top_k=5),
    )
    kept = {x["term"] for x in (debug.get("burst_kept_top30") or [])}
    all_terms = set()
    for e in edges:
        all_terms |= set((e.provenance or {}).get("terms") or [])
    assert "присяжн" in (all_terms | kept) or "казак" in (all_terms | kept)
    assert "вскричал" not in all_terms
    assert edges, f"expected theme edges, debug={debug}"


def test_weak_edge_only_above_floor():
    docs = [
        gi.parse_doc_meta(document_id="iso", filename="classic_0001_Tolstoy_Холстомер_aaaaaaaa.txt"),
        gi.parse_doc_meta(document_id="n1", filename="classic_0002_Tolstoy_Поликушка_bbbbbbbb.txt"),
        gi.parse_doc_meta(document_id="n2", filename="classic_0003_Chekhov_Степь_cccccccc.txt"),
    ]
    gi.resolve_work_keys(docs)
    terms = {
        "iso": Counter({"лошад": 8, "табун": 5, "холоп": 4, "седел": 3}),
        "n1": Counter({"лошад": 6, "двор": 10, "барин": 8, "холоп": 5, "седел": 2}),
        "n2": Counter({"степ": 12, "воз": 9, "ярмарк": 7, "пшениц": 5}),
    }
    # Mutual threshold above best score → only weak path can link orphans
    edges_hi, dbg_hi = gi.layer_d_lex_similar(
        docs=docs,
        doc_term_counts=terms,
        params=gi.GraphIndexParams(lex_min_score=0.999, lex_weak_floor=0.01, lex_top_k=5),
    )
    weak = [e for e in edges_hi if (e.provenance or {}).get("weak")]
    assert weak, f"expected weak edge, debug={dbg_hi}"
    edges_lo, _ = gi.layer_d_lex_similar(
        docs=docs,
        doc_term_counts=terms,
        params=gi.GraphIndexParams(lex_min_score=0.999, lex_weak_floor=0.999, lex_top_k=5),
    )
    weak_lo = [e for e in edges_lo if (e.provenance or {}).get("weak")]
    assert not weak_lo
