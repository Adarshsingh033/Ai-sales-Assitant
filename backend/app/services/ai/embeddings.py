"""
Embedding providers.

  * FastEmbedProvider  — local ONNX multilingual model (default; ~50 languages incl. Hindi).
  * HashEmbeddingProvider — deterministic, offline bag-of-features vectors for tests / dev without a model download.

Embedding is CPU-bound and synchronous, so callers must run it in a worker thread
(`await asyncio.to_thread(...)`) to keep the event loop responsive.
"""
import hashlib
import logging
import math
import re
import threading
from typing import Dict, List, Optional

import numpy as np

from app.core.config import settings

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[^\s.,;:!?()\[\]{}\"'“”‘’।|/\\<>=+*&^%#@~`-]+")


def tokenize(text: str) -> List[str]:
    """Whitespace/punctuation tokenizer that keeps Devanagari and other combining-mark scripts intact."""
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if len(t) > 1 or t.isdigit()]


def to_bytes(vec) -> bytes:
    return np.asarray(vec, dtype="<f4").tobytes()


def from_bytes(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype="<f4")


def normalize(matrix: np.ndarray) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


class EmbeddingProvider:
    name: str = "base"
    dim: int = 0

    def embed(self, texts: List[str]) -> List[List[float]]:  # pragma: no cover - interface
        raise NotImplementedError


class FastEmbedProvider(EmbeddingProvider):
    def __init__(self, model_name: str, dim: int) -> None:
        self.name = model_name
        self.dim = dim
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from fastembed import TextEmbedding

                    logger.info("Initializing embedding model %s ...", self.name)
                    self._model = TextEmbedding(model_name=self.name)
        return self._model

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        model = self._load()
        with self._lock:  # ONNX session reuse across threads is not guaranteed safe
            return [[float(x) for x in emb] for emb in model.embed(texts, batch_size=32)]


class HashEmbeddingProvider(EmbeddingProvider):
    """
    Feature-hashing embeddings: words + character trigrams hashed into `dim` signed buckets, L2-normalised.
    Texts that share vocabulary get a high cosine similarity, which is enough for deterministic tests.
    """

    def __init__(self, dim: int = 384) -> None:
        self.name = f"hash-{dim}"
        self.dim = dim

    def _features(self, text: str) -> List[str]:
        words = tokenize(text)
        feats = list(words)
        for w in words:
            padded = f"#{w}#"
            feats.extend(padded[i:i + 3] for i in range(max(1, len(padded) - 2)))
        return feats

    def embed(self, texts: List[str]) -> List[List[float]]:
        out: List[List[float]] = []
        for text in texts:
            vec = np.zeros(self.dim, dtype=np.float32)
            for feat in self._features(text):
                h = int.from_bytes(hashlib.blake2b(feat.encode(), digest_size=8).digest(), "little")
                vec[h % self.dim] += 1.0 if (h >> 40) & 1 else -1.0
            norm = math.sqrt(float(np.dot(vec, vec))) or 1.0
            out.append((vec / norm).tolist())
        return out


_provider: Optional[EmbeddingProvider] = None
_provider_lock = threading.Lock()


def get_embedding_provider() -> EmbeddingProvider:
    global _provider
    if _provider is None:
        with _provider_lock:
            if _provider is None:
                if settings.EMBEDDING_PROVIDER.lower() == "hash":
                    _provider = HashEmbeddingProvider(settings.EMBEDDING_DIM)
                else:
                    _provider = FastEmbedProvider(settings.EMBEDDING_MODEL, settings.EMBEDDING_DIM)
    return _provider


def set_embedding_provider(provider: Optional[EmbeddingProvider]) -> None:
    """Swap the provider (tests / experiments). Pass None to reset to settings."""
    global _provider
    _provider = provider


def embedding_model_name() -> str:
    return get_embedding_provider().name


def embed_texts_sync(texts: List[str]) -> List[List[float]]:
    return get_embedding_provider().embed(texts)


class QueryEmbeddingCache:
    """Small LRU so repeated / rewritten queries do not re-run the model."""

    def __init__(self, capacity: int = 512) -> None:
        self.capacity = capacity
        self._data: Dict[str, np.ndarray] = {}

    def get(self, key: str) -> Optional[np.ndarray]:
        value = self._data.pop(key, None)
        if value is not None:
            self._data[key] = value  # move to MRU position
        return value

    def put(self, key: str, value: np.ndarray) -> None:
        self._data.pop(key, None)
        self._data[key] = value
        while len(self._data) > self.capacity:
            self._data.pop(next(iter(self._data)))

    def clear(self) -> None:
        self._data.clear()
