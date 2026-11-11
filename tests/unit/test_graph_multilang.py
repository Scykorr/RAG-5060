"""Slice 1e-M: multilingual (ru/en) connectivity helpers and layers."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from apps.api.services import graph_index as gi
from apps.api.services import graph_lang as glang
from apps.api.services.graph_lexicon import reload_graph_lexicon

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "graph_multilang"


def setup_module() -> None:
    reload_graph_lexicon()


def test_detect_language_ru_en_mixed():
    ru = (FIX / "ru_story.txt").read_text(encoding="utf-8")
    en = (FIX / "en_tls_spec.txt").read_text(encoding="utf-8")
    mixed = (FIX / "ru_tls_guide.txt").read_text(encoding="utf-8")
    assert glang.detect_language(ru) == "ru"
    assert glang.detect_language(en) == "en"
    assert glang.detect_language(mixed) == "mixed"
    bal = "Привет world this is a test of mixed language документ с API и TLS"
    assert glang.detect_language(bal) == "mixed"


def test_en_stopwords_no_spurious_lex_theme_pair_links():
    docs = [
        gi.parse_doc_meta(document_id="a", filename="tech_0001_en_TLS_Guide_aaaaaaaa.txt"),
        gi.parse_doc_meta(document_id="b", filename="tech_0002_en_HTTP_Guide_bbbbbbbb.txt"),
        gi.parse_doc_meta(document_id="c", filename="tech_0003_en_Unrelated_cccccccc.txt"),
    ]
    for d in docs:
        d.language = "en"
        d.doc_kind = "technical"
    gi.resolve_work_keys(docs)
    terms = {
        "a": Counter(
            {
                "encrypt": 10,
                "handshake": 8,
                "certif": 7,
                "protocol": 6,
                "payload": 5,
                "said": 20,
                "the": 50,
                "and": 40,
            }
        ),
        "b": Counter(
            {
                "encrypt": 9,
                "handshake": 7,
                "certif": 6,
                "protocol": 8,
                "payload": 4,
                "said": 18,
                "the": 45,
                "and": 35,
            }
        ),
        "c": Counter({"garden": 12, "flower": 10, "said": 15, "the": 40, "and": 30}),
    }
    # Strip stops via tokenize path — feed already-stemmed thematic terms only for a/b
    edges, debug = gi.layer_d_lex_similar(
        docs=docs,
        doc_term_counts=terms,
        params=gi.GraphIndexParams(lex_min_score=0.05, lex_weak_floor=0.01),
        term_counts_by_lang={
            "a": {"en": terms["a"]},
            "b": {"en": terms["b"]},
            "c": {"en": terms["c"]},
        },
    )
    pairs = {(e.source, e.target) for e in edges} | {(e.target, e.source) for e in edges}
    assert ("a", "b") in pairs or ("b", "a") in pairs
    # style/stop alone should not dominate provenance
    for e in edges:
        terms_p = set((e.provenance or {}).get("terms") or [])
        assert "said" not in terms_p
        assert "the" not in terms_p


def test_cross_lang_shared_entity_not_lex():
    docs = [
        gi.parse_doc_meta(document_id="ru", filename="tech_ru_TLS_Guide_aaaaaaaa.txt"),
        gi.parse_doc_meta(document_id="en", filename="tech_en_TLS_Spec_bbbbbbbb.txt"),
        gi.parse_doc_meta(document_id="x", filename="tech_en_Other_cccccccc.txt"),
    ]
    docs[0].language = "ru"
    docs[0].doc_kind = "technical"
    docs[1].language = "en"
    docs[1].doc_kind = "technical"
    docs[2].language = "en"
    docs[2].doc_kind = "technical"
    gi.resolve_work_keys(docs)
    # Lex: different languages → no edge even with shared stems
    lex_edges, _ = gi.layer_d_lex_similar(
        docs=docs,
        doc_term_counts={
            "ru": Counter({"протокол": 5, "шифр": 4}),
            "en": Counter({"protocol": 5, "cipher": 4}),
            "x": Counter({"garden": 5}),
        },
        term_counts_by_lang={
            "ru": {"ru": Counter({"протокол": 5, "шифр": 4})},
            "en": {"en": Counter({"protocol": 5, "cipher": 4})},
            "x": {"en": Counter({"garden": 5})},
        },
        params=gi.GraphIndexParams(lex_min_score=0.01),
    )
    lex_pairs = {(e.source, e.target) for e in lex_edges} | {(e.target, e.source) for e in lex_edges}
    assert ("ru", "en") not in lex_pairs and ("en", "ru") not in lex_pairs

    ent_edges = gi.layer_c_shared_entity(
        docs=docs,
        doc_entities={"ru": [], "en": [], "x": []},
        doc_identifiers={
            "ru": ["RFC 8446", "TLS 1.3"],
            "en": ["RFC 8446", "TLS 1.3"],
            "x": ["ISO 9001"],
        },
        identifier_kinds={
            "ru": {"RFC 8446": "standard", "TLS 1.3": "standard"},
            "en": {"RFC 8446": "standard", "TLS 1.3": "standard"},
            "x": {"ISO 9001": "standard"},
        },
    )
    hit = [
        e
        for e in ent_edges
        if {e.source, e.target} == {"ru", "en"}
    ]
    assert hit, f"expected cross-lang entity edge, got {ent_edges}"
    assert hit[0].provenance.get("cross_lang") is True


def test_spec_versions_share_work_key_edition_of():
    a = gi.parse_doc_meta(document_id="v12", filename="tech_Spec_v1.2_aaaaaaaa.txt")
    b = gi.parse_doc_meta(document_id="v13", filename="tech_Spec_v1.3_bbbbbbbb.txt")
    a.title = "Spec v1.2"
    b.title = "Spec v1.3"
    a.doc_kind = "technical"
    b.doc_kind = "technical"
    a.doc_version = "1.2"
    b.doc_version = "1.3"
    base_a, ver_a = glang.strip_doc_version("Spec v1.2")
    base_b, ver_b = glang.strip_doc_version("Spec v1.3")
    assert ver_a == "1.2" and ver_b == "1.3"
    assert base_a == base_b == "Spec"
    a.work_key = gi.work_key_for(pack=a.pack, author_key=None, title=base_a)
    b.work_key = gi.work_key_for(pack=b.pack, author_key=None, title=base_b)
    assert a.work_key == b.work_key
    dup = {
        "v12": {"canonical": True, "duplicate_of": None, "contained_in": None, "relation": "canonical"},
        "v13": {"canonical": True, "duplicate_of": None, "contained_in": None, "relation": "canonical"},
    }
    out = gi._rewrite_technical_editions([a, b], dup)
    assert out["v12"].get("edition_of") or out["v13"].get("edition_of")
    edges = gi._edition_of_edges([a, b], out)
    assert any(e.type == "edition_of" for e in edges)


def test_patronymic_rules_skip_english():
    assert glang.literary_rules_enabled("fiction", "en") is False
    assert glang.literary_rules_enabled("technical", "ru") is False
    assert glang.literary_rules_enabled("fiction", "ru") is True


def test_doc_kind_technical_vs_fiction():
    en = (FIX / "en_tls_spec.txt").read_text(encoding="utf-8")
    ru = (FIX / "ru_story.txt").read_text(encoding="utf-8")
    assert glang.detect_doc_kind(en, language="en", filename="tls_spec.txt") == "technical"
    assert glang.detect_doc_kind(ru, language="ru", filename="classic_story.txt") in {
        "fiction",
        "unknown",
        "nonfiction",
    }


def test_identifiers_extract_standards():
    text = "See RFC 8446 and TLS 1.3; also ISO 26262 and openssl s_client."
    ids = glang.extract_identifiers(text)
    kinds = {s.upper().replace(" ", ""): k for s, k in ids}
    assert any("RFC8446" in k for k in kinds)
    assert any(k.startswith("TLS") for k in kinds)


def test_identifiers_skip_roman_and_web_paths():
    text = "Глава XIV. См. https://www.rvb.ru/pushkin и /www.rvb.ru/pushkin/path"
    ids = glang.extract_identifiers(text)
    surfaces = {s.upper() for s, _ in ids}
    assert "XIV" not in surfaces
    assert not any("RVB.RU" in s or "WWW." in s for s in surfaces)
