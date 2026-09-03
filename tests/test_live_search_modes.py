"""Ranking modes against a live engine: semantic, BM25 keyword, and hybrid.

Needs a Montycat Semantic engine >= 1.3.4 — earlier engines reject the keyword
and hybrid commands outright, which is itself the contract these assert. The
corpus is built so each mode has a hit the others would rank lower: a rare
literal token only BM25 can anchor on, and a paraphrase only vectors can reach.
"""

from __future__ import annotations

import asyncio

import pytest

from .conftest import requires_engine

pytestmark = [pytest.mark.asyncio, requires_engine]

# A token that appears verbatim in exactly one memory and resembles nothing
# semantically — the case vector search is worst at and BM25 is built for.
RARE_TOKEN = "ENOSPC"

CORPUS = [
    {"text": f"the nightly snapshot failed with {RARE_TOKEN} on the data volume",
     "topic": "ops"},
    {"text": "we ran out of disk during the backup window", "topic": "ops"},
    {"text": "the team picked usearch HNSW for the vector index", "topic": "search"},
    {"text": "sourdough needs a wild yeast starter", "topic": "cooking"},
]


def payload(result):
    """Hits from a successful call; an engine failure raises rather than reads
    as "no results" — a masked error is a 90-second lie about embeddings."""
    if isinstance(result, dict) and result.get("status") is False:
        pytest.fail(f"engine call failed: {result.get('error')}")
    body = result.get("payload") if isinstance(result, dict) else None
    return body if isinstance(body, list) else []



async def seed(server, ks):
    for value in CORPUS:
        await server.montycat_remember(value=value, keyspace=ks)
    # Embedding is a background batch; wait for the whole corpus or the
    # mode comparisons race a half-built index.
    for _ in range(90):
        hits = payload(await server.montycat_semantic_search(
            query="disk failure during backup", keyspace=ks, limit=10))
        if len(hits) >= len(CORPUS):
            return
        await asyncio.sleep(1)
    pytest.fail("embeddings did not converge within 90s")


@pytest.fixture
async def seeded(server, keyspace):
    await seed(server, keyspace)
    return keyspace


async def test_keyword_mode_finds_the_literal_token(server, seeded):
    hits = payload(await server.montycat_semantic_search(
        query=RARE_TOKEN, keyspace=seeded, mode="keyword", limit=5))

    assert hits, "BM25 returned nothing for a token that is stored verbatim"
    assert RARE_TOKEN in hits[0]["__value__"]["text"]


async def test_semantic_mode_finds_the_paraphrase(server, seeded):
    """No shared content word with the stored text — only meaning connects them."""
    hits = payload(await server.montycat_semantic_search(
        query="which embedding library did we adopt", keyspace=seeded, limit=3))

    assert hits
    assert "usearch" in hits[0]["__value__"]["text"]


async def test_hybrid_mode_scores_are_normalized_to_zero_one(server, seeded):
    hits = payload(await server.montycat_semantic_search(
        query=f"{RARE_TOKEN} disk failure", keyspace=seeded, mode="hybrid", limit=5))

    assert hits
    assert all(0.0 <= h["__score__"] <= 1.0 for h in hits), \
        "RRF scores must arrive normalized, not raw"


async def test_hybrid_surfaces_both_halves_of_a_mixed_query(server, seeded):
    """A query with an exact token AND a paraphrase: hybrid should hold both,
    where either single mode drops one."""
    hits = payload(await server.montycat_semantic_search(
        query=f"{RARE_TOKEN} ran out of space", keyspace=seeded,
        mode="hybrid", limit=4))
    texts = " ".join(h["__value__"]["text"] for h in hits)

    assert RARE_TOKEN in texts
    assert "ran out of disk" in texts


async def test_modes_respect_metadata_filters(server, seeded):
    """The filter bounds the candidate set in every mode; it does not force a
    result set to empty. Hybrid still ranks whatever survives the filter by
    vector similarity, so a topic-restricted search for a token that lives in
    another topic returns that topic's records — never the token's."""
    for mode in ("keyword", "hybrid", "semantic"):
        hits = payload(await server.montycat_semantic_search(
            query=RARE_TOKEN, keyspace=seeded, mode=mode,
            filters={"topic": "cooking"}, limit=5))
        topics = {h["__value__"]["topic"] for h in hits}
        assert topics <= {"cooking"}, f"{mode} returned records outside the filter"
        assert all(RARE_TOKEN not in h["__value__"]["text"] for h in hits)

    # BM25 has no candidate to score once the filter excludes the only record
    # containing the token, so keyword mode is the one that comes back empty.
    keyword_hits = payload(await server.montycat_semantic_search(
        query=RARE_TOKEN, keyspace=seeded, mode="keyword",
        filters={"topic": "cooking"}, limit=5))
    assert keyword_hits == []


async def test_bm25_floor_can_exceed_one(server, seeded):
    """The unbounded-BM25 contract, end to end: a floor above 1.0 is a valid
    request in keyword mode, and the engine must not reject it."""
    result = await server.montycat_semantic_search(
        query=RARE_TOKEN, keyspace=seeded, mode="keyword", min_score=1.5, limit=5)

    payload(result)  # fails loudly if the engine rejected the floor
