import numpy as np

from dualign.services.similarity import SimilarityScorer


def test_score_pairs_combines_both_sides_into_one_encoder_batch(tmp_path):
    class Encoder:
        _model = "combined-batch"
        _dim = 2

        def __init__(self):
            self.calls = []

        def encode(self, texts, normalize_embeddings=True):
            self.calls.append(list(texts))
            vectors = {
                "a": [1.0, 0.0],
                "b": [0.0, 1.0],
                "x": [1.0, 0.0],
                "y": [0.0, 1.0],
            }
            return np.array([vectors[text] for text in texts], dtype=np.float32)

    encoder = Encoder()
    scorer = SimilarityScorer(encoder_model=encoder, cache_dir=str(tmp_path))
    try:
        scores = scorer.score_pairs(["a", "b"], ["x", "y"])
    finally:
        scorer.close()

    assert encoder.calls == [["a", "b", "x", "y"]]
    np.testing.assert_allclose(scores, [1.0, 1.0])
