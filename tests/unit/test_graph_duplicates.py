"""A6 near-duplicate editions + A7 isolation metrics."""

from __future__ import annotations

from apps.api.services import graph_duplicates as gd
from apps.api.services import graph_index as gi


def test_shingle_near_duplicates_mark_canonical():
    docs = [
        gi.parse_doc_meta(
            document_id="full",
            filename="classic_0001_ch_Gogol_Мёртвые_души_aaaaaaaa.txt",
        ),
        gi.parse_doc_meta(
            document_id="copy",
            filename="classic_0002_eb_Gogol_Мертвые_души_bbbbbbbb.txt",
        ),
    ]
    gi.resolve_work_keys(docs)
    assert docs[0].work_key == docs[1].work_key
    text = (
        "Помещик Чичиков приехал в губернский город NN и посетил местных чиновников. "
        "Далее следует длинный одинаковый текст для шинглов и проверки дублей изданий. "
    ) * 20
    almost = text[:-30] + " небольшая правка в конце текста для шума."
    texts = {"full": text, "copy": almost}
    m = gd.mark_canonical_duplicates(docs, texts, threshold=0.80)
    canons = [k for k, v in m.items() if v["canonical"]]
    dups = [k for k, v in m.items() if not v["canonical"]]
    assert len(canons) == 1 and len(dups) == 1
    assert m[dups[0]]["duplicate_of"] == canons[0]
    assert m[dups[0]]["sim"] >= 0.80


def test_df_counts_canonical_once():
    """Two near-identical docs in one work → only canonical contributes to case_stats path."""
    docs = [
        gi.parse_doc_meta(
            document_id="a",
            filename="classic_0001_ch_Gogol_Нос_aaaaaaaa.txt",
        ),
        gi.parse_doc_meta(
            document_id="b",
            filename="classic_0002_eb_Gogol_Нос_bbbbbbbb.txt",
        ),
        gi.parse_doc_meta(
            document_id="c",
            filename="classic_0003_Pushkin_Пир_cccccccc.txt",
        ),
    ]
    gi.resolve_work_keys(docs)
    body = ("Майор Ковалев потерял свой нос. " * 40)
    texts = {"a": body, "b": body, "c": "Совершенно другой текст пира во время чумы. " * 20}
    m = gd.mark_canonical_duplicates(docs, texts, threshold=0.85)
    canon = gd.canonical_doc_ids(m)
    assert "a" in canon or "b" in canon
    assert len(canon & {"a", "b"}) == 1
    assert "c" in canon


def test_assemble_isolated_content_vs_structural():
    docs = [
        gi.parse_doc_meta(document_id="lonely", filename="classic_0001_Pushkin_Пир_aaaaaaaa.txt"),
        gi.parse_doc_meta(document_id="other", filename="classic_0002_Tolstoy_Казаки_bbbbbbbb.txt"),
    ]
    for d in docs:
        d.chunk_count = 5
        d.canonical = True
    edges = gi.layer_a_edges(docs)  # structural only
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
    assert stats["isolated_structural"] == 0  # in_theme / wrote present
    assert "top_authors_by_degree" in stats
    # author nodes listed separately, not inside work degree as works
    for row in stats.get("top_works_by_degree") or []:
        assert not str(row["node"]).startswith("author:")
