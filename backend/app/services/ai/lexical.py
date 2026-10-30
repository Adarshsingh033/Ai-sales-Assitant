"""
Small in-memory BM25 index (inverted index, k1=1.5, b=0.75).
Complements dense retrieval: exact SKUs, product names, numbers and rare terms that embeddings blur.
"""
import math
from collections import Counter, defaultdict
from typing import Dict, List, Set, Tuple

from app.services.ai.embeddings import tokenize

_STOPWORDS = frozenset(
    "the a an and or of to in on for with is are was were be do does did i you we it this that at by from as "
    "what which how can could would should please me my our your us hai hain ka ki ke ko se me mein aur".split()
)


def lexical_tokens(text: str) -> List[str]:
    return [t for t in tokenize(text) if t not in _STOPWORDS]


class BM25Index:
    def __init__(self, docs: List[List[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.n = len(docs)
        self.doc_len = [len(d) for d in docs]
        self.doc_terms = [set(d) for d in docs]
        self.avgdl = (sum(self.doc_len) / self.n) if self.n else 0.0
        self.postings: Dict[str, List[Tuple[int, int]]] = defaultdict(list)
        for idx, tokens in enumerate(docs):
            for term, tf in Counter(tokens).items():
                self.postings[term].append((idx, tf))

    def coverage(self, idx: int, query_terms: Set[str]) -> float:
        """Fraction of the (unique) query terms that occur in document `idx`."""
        if not query_terms:
            return 0.0
        return len(query_terms & self.doc_terms[idx]) / len(query_terms)

    def search(self, query_tokens: List[str], k: int = 20) -> List[Tuple[int, float]]:
        if not self.n or not query_tokens:
            return []
        scores: Dict[int, float] = defaultdict(float)
        for term in set(query_tokens):
            posting = self.postings.get(term)
            if not posting:
                continue
            df = len(posting)
            idf = math.log(1 + (self.n - df + 0.5) / (df + 0.5))
            for idx, tf in posting:
                norm = tf + self.k1 * (1 - self.b + self.b * self.doc_len[idx] / (self.avgdl or 1.0))
                scores[idx] += idf * tf * (self.k1 + 1) / norm
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        return ranked[:k]
