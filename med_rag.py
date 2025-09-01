# med_rag.py
# -*- coding: utf-8 -*-
"""
RAG đơn giản cho quy chuẩn y tế:
- Nạp từ 1 file (.txt/.md/.yaml/.yml/.json) hoặc 1 thư mục chứa nhiều file.
- TF-IDF + cosine để truy hồi nhanh, hot-reload khi file đổi mtime.
- Trả về đoạn context ngắn gọn để chèn vào ChatContext.
"""

import os, time, json, glob, threading
from typing import List, Tuple, Optional, Any
from dataclasses import dataclass, field

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
except Exception:
    TfidfVectorizer = None
    cosine_similarity = None

try:
    import yaml
except Exception:
    yaml = None

def _read_text_from_path(path: str) -> str:
    if os.path.isdir(path):
        parts = []
        for p in sorted(glob.glob(os.path.join(path, "**", "*"), recursive=True)):
            if os.path.isdir(p): 
                continue
            parts.append(_read_text_from_path(p))
        return "\n\n".join([x for x in parts if x.strip()])
    # file
    _, ext = os.path.splitext(path.lower())
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        if ext in [".yaml", ".yml"] and yaml is not None:
            obj = yaml.safe_load(raw)
            return json.dumps(obj, ensure_ascii=False, indent=2)
        if ext == ".json":
            return json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
        # txt / md
        return raw
    except Exception:
        return ""

@dataclass
class MedicalRAG:
    source_path: str
    max_docs: int = 2000
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _tfidf: Optional[Any] = field(default=None, init=False)
    _doc_matrix = None
    _docs: List[str] = field(default_factory=list, init=False)  # static guideline chunks
    _mtime: float = field(default=0.0, init=False)
    dynamic_docs: List[str] = field(default_factory=list, init=False)  # personalization snippets (NOT in TF-IDF)

    def _split_docs(self, text: str, max_len: int = 1200) -> List[str]:
        # tách theo đoạn trống đôi để giữ cấu trúc guideline
        blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
        chunks: List[str] = []
        cur = []
        cur_len = 0
        for b in blocks:
            if cur_len + len(b) > max_len and cur:
                chunks.append("\n\n".join(cur))
                cur, cur_len = [], 0
            cur.append(b)
            cur_len += len(b)
        if cur:
            chunks.append("\n\n".join(cur))
        return chunks[: self.max_docs]

    def _build(self):
        text = _read_text_from_path(self.source_path)
        docs = self._split_docs(text)
        if not docs:
            # No documents found - use fallback mode
            self._docs = ["No medical guidelines available"]
            self._tfidf = None
            self._doc_matrix = None
        elif TfidfVectorizer is None:
            # fallback: keyword-only
            self._docs = docs
            self._tfidf = None
            self._doc_matrix = None
        else:
            # Check if docs contain meaningful content
            meaningful_docs = [doc for doc in docs if doc.strip() and len(doc.strip()) > 5]
            if not meaningful_docs:
                # No meaningful content - use fallback
                self._docs = ["No detailed medical guidelines available"]
                self._tfidf = None
                self._doc_matrix = None
            else:
                tfidf = TfidfVectorizer(ngram_range=(1,2), min_df=1)
                mat = tfidf.fit_transform(meaningful_docs)
                self._docs, self._tfidf, self._doc_matrix = meaningful_docs, tfidf, mat

    def _paths_mtime(self) -> float:
        if os.path.isdir(self.source_path):
            mt = 0.0
            for p in glob.glob(os.path.join(self.source_path, "**", "*"), recursive=True):
                if os.path.isdir(p): 
                    continue
                try:
                    mt = max(mt, os.path.getmtime(p))
                except Exception:
                    pass
            return mt
        try:
            return os.path.getmtime(self.source_path)
        except Exception:
            return 0.0

    def maybe_reload(self):
        mt = self._paths_mtime()
        with self._lock:
            if mt == 0.0:
                # first time build anyway
                self._build()
                self._mtime = time.time()
                return
            if mt > self._mtime:
                self._build()
                self._mtime = mt

    def query(self, question: str, k: int = 4, max_chars: int = 1200) -> str:
        """Return combined context: PERSONAL_HISTORY (if any) + top guideline chunks.

        Personal history is always surfaced (not ranked) to avoid being missed by retrieval.
        """
        self.maybe_reload()
        personal_block = ""
        with self._lock:
            if self.dynamic_docs:
                # keep only last 1 personal summary to stay concise
                latest = self.dynamic_docs[-1]
                personal_block = f"[PERSONAL_HISTORY]\n{latest}\n[/PERSONAL_HISTORY]\n\n"

            if not self._docs:
                return personal_block.strip()[:max_chars]
            base_docs = self._docs
            if self._tfidf is None:
                q = question.lower()
                scores = [(sum(q.count(w) for w in d.lower().split()), i) for i, d in enumerate(base_docs)]
                scores.sort(reverse=True)
                picks = [base_docs[i] for _, i in scores[:k]]
            else:
                qv = self._tfidf.transform([question])
                sims = cosine_similarity(qv, self._doc_matrix)[0]
                idx = sims.argsort()[::-1][:k]
                picks = [base_docs[i] for i in idx]
            guide_block = "[GUIDELINES]\n" + "\n\n---\n\n".join(picks) + "\n[/GUIDELINES]"
            combined = (personal_block + guide_block).strip()
            if len(combined) > max_chars:
                # preserve personal block start; truncate guideline tail
                if personal_block and len(personal_block) < max_chars:
                    remain = max_chars - len(personal_block)
                    combined = personal_block + guide_block[:remain]
                else:
                    combined = combined[:max_chars]
            return combined

    def add_dynamic_context(self, text: str):
        """Store personalization snippet separately (no TF-IDF rebuild). Deduplicate and cap length."""
        if not text:
            return
        snippet = text.strip()
        if not snippet:
            return
        with self._lock:
            if snippet in self.dynamic_docs:
                return
            self.dynamic_docs.append(snippet)
            # cap to last 2 snippets
            if len(self.dynamic_docs) > 2:
                self.dynamic_docs = self.dynamic_docs[-2:]
