from student_api import rag_embedding_shift, rag_length_shift


def test_rag_length_collapse_is_detected():
    baseline_batch_means = [40, 42, 39, 41, 43, 40, 42]
    current_texts = ["x y", "a b c", "one two"]
    assert rag_length_shift(current_texts, baseline_batch_means)["is_anomaly"] is True


def test_stable_embedding_norms_are_not_anomaly():
    baseline_norms = [1.0, 0.98, 1.02, 0.99, 1.01, 1.0, 0.97, 1.03]
    current_norms = [1.0, 0.99, 1.01]
    assert rag_embedding_shift(current_norms, baseline_norms)["is_anomaly"] is False


def test_embedding_mean_shift_is_detected():
    # A different embedding model/version producing much larger-magnitude vectors.
    baseline_norms = [1.0, 0.98, 1.02, 0.99, 1.01, 1.0, 0.97, 1.03]
    current_norms = [3.5, 3.6, 3.4]
    assert rag_embedding_shift(current_norms, baseline_norms)["is_anomaly"] is True


def test_embedding_dispersion_shift_is_detected():
    # Mean norm looks fine, but some vectors are near-zero (corrupted/truncated).
    baseline_norms = [1.0, 0.98, 1.02, 0.99, 1.01, 1.0, 0.97, 1.03]
    current_norms = [1.0, 0.01, 2.0, 0.02, 1.9]
    assert rag_embedding_shift(current_norms, baseline_norms)["is_anomaly"] is True
