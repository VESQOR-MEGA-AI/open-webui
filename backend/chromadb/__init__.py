"""Minimal chromadb stub for chat-only deployment (no RAG)."""
DEFAULT_TENANT = "default_tenant"
DEFAULT_DATABASE = "default_database"

class Settings:
    def __init__(self, **kwargs):
        pass

class _DummyClient:
    """Inert client: RAG is disabled in chat-only deployment."""
    def __getattr__(self, name):
        def _dummy(*args, **kwargs):
            raise NotImplementedError("RAG is disabled in chat-only deployment")
        return _dummy

def PersistentClient(*args, **kwargs):
    return _DummyClient()

def HttpClient(*args, **kwargs):
    return _DummyClient()
