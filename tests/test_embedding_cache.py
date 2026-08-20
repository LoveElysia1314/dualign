from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np

from dualign import config
from dualign.services.cached_encoder import CachedEncoder
from dualign.services.cache_migration import migrate_embedding_caches
from dualign.services.embedding_cache import EmbeddingCache


def _vector(value: float) -> np.ndarray:
    return np.array([value, value + 1], dtype=np.float32)


def test_global_cache_path_is_not_partitioned_by_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DUALIGN_CACHE_ROOT", str(tmp_path))

    assert config.get_embedding_cache_path() == str(tmp_path / "emb" / "vecs.db")


def test_cache_batches_large_queries_and_keeps_hashes_immutable(tmp_path):
    path = tmp_path / "vecs.db"
    items = [(f"hash-{index}", _vector(index), "model") for index in range(1005)]

    with EmbeddingCache(str(path)) as cache:
        cache.put_batch(items)
        cache.put_batch([("hash-0", _vector(9999), "model")])
        loaded = cache.get_batch([item[0] for item in items] + ["hash-0"])

        assert cache.count == 1005
        assert len(loaded) == 1005
        np.testing.assert_array_equal(loaded["hash-0"], _vector(0))

    connection = sqlite3.connect(path)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    connection.close()
    assert tables == {"vecs"}


def test_cached_encoder_only_encodes_each_repeated_miss_once(tmp_path):
    class Encoder:
        _model = "model"
        _dim = 2

        def __init__(self):
            self.calls: list[list[str]] = []

        def encode(self, texts, normalize_embeddings=True):
            self.calls.append(texts)
            return np.array([[3.0, 4.0] for _ in texts], dtype=np.float32)

    encoder = Encoder()
    with EmbeddingCache(str(tmp_path / "vecs.db")) as cache:
        result = CachedEncoder(encoder, cache).encode(["same", "same", "other"])

    assert encoder.calls == [["same", "other"]]
    assert result.shape == (3, 2)
    np.testing.assert_array_equal(result[0], result[1])


def test_cached_encoder_separates_same_model_served_by_different_endpoints(tmp_path):
    class Encoder:
        _model = "same-model"
        _instruction = "same-instruction"
        _dim = 2

        def __init__(self, url, value):
            self._url = url
            self.value = value
            self.calls = 0

        def encode(self, texts, normalize_embeddings=True):
            self.calls += 1
            return np.array([[self.value, 1.0] for _ in texts], dtype=np.float32)

    first = Encoder("http://endpoint-a", 1.0)
    second = Encoder("http://endpoint-b", 2.0)
    with EmbeddingCache(str(tmp_path / "vecs.db")) as cache:
        CachedEncoder(first, cache).encode(["text"])
        CachedEncoder(second, cache).encode(["text"])

    assert first.calls == 1
    assert second.calls == 1


def test_legacy_databases_migrate_idempotently_before_removal(tmp_path):
    embedding_dir = tmp_path / "emb"
    first_path = embedding_dir / "chapter-a" / "vecs.db"
    second_path = embedding_dir / "chapter-b" / "vecs.db"

    with EmbeddingCache(str(first_path)) as cache:
        cache.put_batch(
            [("shared", _vector(1), "model"), ("first", _vector(2), "model")]
        )
    with EmbeddingCache(str(second_path)) as cache:
        cache.put_batch(
            [("shared", _vector(1), "model"), ("second", _vector(3), "model")]
        )

    initial = migrate_embedding_caches(embedding_dir)
    assert initial.source_databases == 2
    assert initial.source_rows == 4
    assert initial.inserted_rows == 3
    assert initial.target_rows == 3
    assert initial.removed_databases == 0

    repeated = migrate_embedding_caches(embedding_dir)
    assert repeated.inserted_rows == 0
    assert repeated.target_rows == 3

    removed = migrate_embedding_caches(embedding_dir, remove_legacy=True)
    assert removed.target_rows == 3
    assert removed.removed_databases == 2
    assert not first_path.exists()
    assert not second_path.exists()

    with EmbeddingCache(str(embedding_dir / "vecs.db")) as cache:
        assert cache.count == 3
        np.testing.assert_array_equal(cache.get("first"), _vector(2))
        np.testing.assert_array_equal(cache.get("second"), _vector(3))
