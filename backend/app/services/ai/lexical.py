"""
Small in-memory BM25 index (inverted index, k1=1.5, b=0.75).
Complements dense retrieval: exact SKUs, product names, numbers and rare terms that embeddings blur.
"""
import math
import re
from collections import Counter, defaultdict
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

from app.services.ai.embeddings import tokenize

_STOPWORDS = frozenset(
    "the a an and or of to in on for with is are was were be do does did i you we it this that at by from as "
    "what which how can could would should please me my our your us hai hain ka ki ke ko se me mein aur".split()
)


def lexical_tokens(text: str) -> List[str]:
    return [t for t in tokenize(text) if t not in _STOPWORDS]


# ---------------------------------------------------------------------------
# Spelling correction against the tenant's own vocabulary
# ---------------------------------------------------------------------------
# Neither BM25 nor the multilingual embedder copes with a misspelled brand or product name ("brighlooop"): the word matches
# nothing lexically and is split into meaningless sub-pieces by the embedder. Words the knowledge base has never seen are
# therefore replaced by their closest known term, but only when that term is very close.
_WORD = re.compile(r"[A-Za-z]{5,}")


def _edit_distance(a: str, b: str, limit: int) -> int:
    """Insert/delete/substitute/adjacent-swap distance, abandoned (returns limit + 1) once it must exceed `limit`."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2: List[int] = []
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


def correct_spelling(query: str, vocabulary: Mapping[str, Sequence]) -> str:
    """
    Replace Latin-script words of 5+ letters that do not occur in `vocabulary` (term -> postings) with the closest term
    that does: at most 1 edit for words up to 7 letters (same first letter), 2 edits for longer ones. Words that are known,
    stopwords, shorter or non-Latin (Hindi...) are left alone; ties go to the term found in more chunks.
    """
    if not query or not vocabulary:
        return query
    candidates: Optional[List[str]] = None

    def fix(match: "re.Match[str]") -> str:
        nonlocal candidates
        word = match.group(0)
        low = word.lower()
        if low in vocabulary or low in _STOPWORDS:
            return word
        if candidates is None:
            candidates = [t for t in vocabulary if len(t) >= 4 and t.isascii() and t.isalpha()]
        limit = 1 if len(low) <= 7 else 2
        best: Optional[Tuple[Tuple[int, int, str], str]] = None
        for term in candidates:
            if abs(len(term) - len(low)) > limit or (limit == 1 and term[0] != low[0]):
                continue
            dist = _edit_distance(low, term, limit)
            if dist > limit:
                continue
            rank = (dist, -len(vocabulary[term]), term)
            if best is None or rank < best[0]:
                best = (rank, term)
        if best is None:
            return word
        fixed = best[1]
        return fixed.upper() if word.isupper() else fixed.capitalize() if word[0].isupper() else fixed

    return _WORD.sub(fix, query)


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
