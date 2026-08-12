def create_batches(*args, **kwargs):
    """Return empty batches: RAG disabled in chat-only deployment."""
    return iter([])
