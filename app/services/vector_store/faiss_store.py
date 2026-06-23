"""
Simple FAISS-like vector store with a memory fallback.
Provides create_index(analysis_id, vectors, metadatas), exists, search.
Persistent storage implemented with pickle under data/vector_store/.
Note: This intentionally avoids a faiss dependency; if faiss is available, an adapter can be added.
"""

import os
import pickle
import threading
from typing import List, Dict, Any

BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "vector_store")
os.makedirs(BASE_DIR, exist_ok=True)

_lock = threading.Lock()


class FaissStore:
    def __init__(self):
        # in-memory mapping: analysis_id -> {vectors: [...], metadatas: [...]}
        self._indexes: Dict[str, Dict[str, Any]] = {}

    def _path_for(self, analysis_id: str) -> str:
        safe = analysis_id.replace("/", "_")
        return os.path.join(BASE_DIR, f"{safe}.pkl")

    def exists(self, analysis_id: str) -> bool:
        if analysis_id in self._indexes:
            return True
        return os.path.exists(self._path_for(analysis_id))

    def create_index(self, analysis_id: str, vectors: List[List[float]], metadatas: List[Dict[str, Any]]):
        with _lock:
            self._indexes[analysis_id] = {"vectors": vectors, "metadatas": metadatas}
            # persist
            with open(self._path_for(analysis_id), "wb") as f:
                pickle.dump(self._indexes[analysis_id], f)

    def _load_index(self, analysis_id: str):
        path = self._path_for(analysis_id)
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            obj = pickle.load(f)
        self._indexes[analysis_id] = obj
        return obj

    def _cosine_sim(self, a: List[float], b: List[float]) -> float:
        # assume normalized
        return sum(x * y for x, y in zip(a, b))

    def search(self, analysis_id: str, query_vector: List[float], top_k: int = 4) -> List[Dict[str, Any]]:
        if analysis_id not in self._indexes:
            loaded = self._load_index(analysis_id)
            if not loaded:
                return []
        idx = self._indexes[analysis_id]
        vecs = idx.get("vectors", [])
        metas = idx.get("metadatas", [])
        results = []
        for i, v in enumerate(vecs):
            score = self._cosine_sim(query_vector, v)
            results.append((score, metas[i]))
        # sort desc
        results.sort(key=lambda x: x[0], reverse=True)
        out = []
        for score, meta in results[:top_k]:
            out.append({"score": float(score), "metadata": meta})
        return out

    def delete_index(self, analysis_id: str):
        with _lock:
            if analysis_id in self._indexes:
                del self._indexes[analysis_id]
            path = self._path_for(analysis_id)
            if os.path.exists(path):
                os.remove(path)
