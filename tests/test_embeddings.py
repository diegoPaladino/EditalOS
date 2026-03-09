from editalos.services.embeddings import EmbeddingService


def test_chunk_text_returns_non_empty_chunks():
    text = "A" * 2500
    chunks = EmbeddingService.chunk_text(text, chunk_size=1000, overlap=100)
    assert len(chunks) >= 3
    assert all(chunks)


def test_cosine_similarity_identity():
    sim = EmbeddingService.cosine_similarity([1.0, 0.0], [1.0, 0.0])
    assert round(sim, 5) == 1.0
