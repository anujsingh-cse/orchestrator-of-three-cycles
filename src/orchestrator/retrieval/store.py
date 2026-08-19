"""Qdrant embedded store with dense + sparse vectors and RRF fusion (T8, D4)."""

from __future__ import annotations

import contextlib
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    SparseVector,
    VectorParams,
)

try:
    from fastembed import SparseTextEmbedding, TextEmbedding
except ImportError:  # pragma: no cover
    TextEmbedding = None
    SparseTextEmbedding = None

from fastembed import TextEmbedding

from orchestrator.retrieval.chunker import Chunk
from orchestrator.roster import EMBEDDING_DIM, EMBEDDING_MODEL

COLLECTION_NAME = "repo_vectors"
DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"


@dataclass(frozen=True)
class RetrievalQuery:
    text: str
    top_k: int = 10
    filter_path: str | None = None  # filter by file path prefix


@dataclass(frozen=True)
class RetrievalResult:
    chunk: Chunk
    score: float
    rank_dense: int
    rank_sparse: int
    rrf_score: float


class RetrievalStore:
    """Hybrid retrieval store: dense (FastEmbed) + sparse (BM25-style) in one Qdrant collection.

    - Embedded Qdrant (file-backed, no server)
    - Dense vectors: all-MiniLM-L6-v2 (384 dim)
    - Sparse vectors: BM25 via fastembed SparseTextEmbedding (Qdrant/bm25)
    - Fusion: Reciprocal Rank Fusion (RRF) with k=60
    - Exact-match boost: function/class name queries get priority
    """

    SPARSE_MODEL = "Qdrant/bm25"

    def __init__(
        self,
        path: Path,
        collection_name: str = COLLECTION_NAME,
        dense_model: str = EMBEDDING_MODEL,
        dense_dim: int = EMBEDDING_DIM,
    ) -> None:
        if TextEmbedding is None or SparseTextEmbedding is None:
            raise RuntimeError("fastembed not installed: pip install fastembed")

        self.path = path
        self.collection_name = collection_name
        self._client = QdrantClient(path=str(path))
        self._dense_embedder = TextEmbedding(model_name=dense_model)
        self._sparse_embedder = SparseTextEmbedding(model_name=self.SPARSE_MODEL)
        self._ensure_collection(dense_dim)

    def _ensure_collection(self, dense_dim: int) -> None:
        collections = self._client.get_collections().collections
        names = {c.name for c in collections}
        if self.collection_name not in names:
            self._client.create_collection(
                collection_name=self.collection_name,
                vectors_config={
                    DENSE_VECTOR_NAME: VectorParams(size=dense_dim, distance=Distance.COSINE)
                },
                sparse_vectors_config={
                    SPARSE_VECTOR_NAME: {}
                },
            )

    def upsert_chunks(self, chunks: list[Chunk]) -> int:
        """Insert or update chunks with dense + sparse vectors."""
        if not chunks:
            return 0

        texts = [c.enrichment_header + "\n\n" + c.content for c in chunks]
        dense_vecs = list(self._dense_embedder.embed(texts))
        sparse_vecs = list(self._sparse_embedder.embed(texts))

        points: list[PointStruct] = []
        for chunk, dense_vec, sparse_vec in zip(
            chunks, dense_vecs, sparse_vecs, strict=True
        ):
            point_id = self._deterministic_id(chunk)
            payload = chunk.to_payload()
            points.append(
                PointStruct(
                    id=point_id,
                    vector={
                        DENSE_VECTOR_NAME: dense_vec.tolist(),
                        SPARSE_VECTOR_NAME: SparseVector(
                            indices=sparse_vec.indices.tolist(),
                            values=sparse_vec.values.tolist(),
                        ),
                    },
                    payload=payload,
                )
            )

        self._client.upsert(collection_name=self.collection_name, points=points, wait=True)
        return len(points)

    def _deterministic_id(self, chunk: Chunk) -> str:
        """Stable ID from FQN + content hash for idempotent upserts."""
        key = f"{chunk.fqn or chunk.file_path}:{chunk.content_hash}"
        return hashlib.sha256(key.encode()).hexdigest()[:32]

    def query(self, query: RetrievalQuery) -> list[RetrievalResult]:
        """Hybrid search with RRF fusion."""
        dense_vec = list(self._dense_embedder.embed([query.text]))[0]
        sparse_vec = list(self._sparse_embedder.embed([query.text]))[0]

        # Dense search
        dense_response = self._client.query_points(
            collection_name=self.collection_name,
            query=dense_vec.tolist(),
            using=DENSE_VECTOR_NAME,
            limit=query.top_k * 3,  # oversample for fusion
            query_filter=self._build_filter(query),
            with_payload=True,
        )
        dense_results = dense_response.points

        # Sparse search
        sparse_response = self._client.query_points(
            collection_name=self.collection_name,
            query=SparseVector(
                indices=sparse_vec.indices.tolist(),
                values=sparse_vec.values.tolist(),
            ),
            using=SPARSE_VECTOR_NAME,
            limit=query.top_k * 3,
            query_filter=self._build_filter(query),
            with_payload=True,
        )
        sparse_results = sparse_response.points

        # RRF fusion
        return self._rrf_fusion(dense_results, sparse_results, query)

    def _build_filter(self, query: RetrievalQuery) -> Filter | None:
        if query.filter_path:
            return Filter(
                must=[
                    FieldCondition(
                        key="file_path",
                        match=MatchValue(value=query.filter_path),
                    )
                ]
            )
        return None

    def _rrf_fusion(
        self, dense_results, sparse_results, query: RetrievalQuery, k: int = 60
    ) -> list[RetrievalResult]:
        """Reciprocal Rank Fusion with exact-match boost."""
        top_k = query.top_k
        dense_ranks = {r.id: i + 1 for i, r in enumerate(dense_results)}
        sparse_ranks = {r.id: i + 1 for i, r in enumerate(sparse_results)}

        all_ids = set(dense_ranks.keys()) | set(sparse_ranks.keys())
        scored: list[tuple[float, Any, int, int]] = []

        for pid in all_ids:
            dr = dense_ranks.get(pid, k + 1)
            sr = sparse_ranks.get(pid, k + 1)
            rrf = 1.0 / (k + dr) + 1.0 / (k + sr)
            scored.append((rrf, pid, dr, sr))

        scored.sort(reverse=True, key=lambda x: x[0])

        # Fetch payloads for top results
        top_ids = [pid for _, pid, _, _ in scored[:top_k]]
        points = self._client.retrieve(
            collection_name=self.collection_name,
            ids=top_ids,
            with_payload=True,
        )
        point_map = {p.id: p for p in points}

        results: list[RetrievalResult] = []
        for rrf_score, pid, dr, sr in scored[:top_k]:
            if pid not in point_map:
                continue
            payload = point_map[pid].payload
            chunk = Chunk(
                id=payload["id"],
                file_path=payload["file_path"],
                start_line=payload["start_line"],
                end_line=payload["end_line"],
                content=payload["content"],
                language=payload["language"],
                fqn=payload["fqn"],
                signature=payload["signature"],
                imports=payload["imports"],
                enrichment_header=payload["enrichment_header"],
                content_hash=payload["content_hash"],
            )
            results.append(
                RetrievalResult(
                    chunk=chunk,
                    score=rrf_score,
                    rank_dense=dr if dr <= k else -1,
                    rank_sparse=sr if sr <= k else -1,
                    rrf_score=rrf_score,
                )
            )

        # Exact-match boost: if query looks like a function/class name, boost exact FQN matches
        query_words = query.text.strip().split()
        if len(query_words) <= 3:
            for i, r in enumerate(results):
                if r.chunk.fqn and any(w.lower() in r.chunk.fqn.lower() for w in query_words):
                    # Boost exact matches by 20%
                    results[i] = RetrievalResult(
                        chunk=r.chunk,
                        score=r.score * 1.2,
                        rank_dense=r.rank_dense,
                        rank_sparse=r.rank_sparse,
                        rrf_score=r.rrf_score * 1.2,
                    )

        results.sort(key=lambda x: x.score, reverse=True)
        return results

    def delete_file(self, file_path: str) -> int:
        """Delete all chunks for a file (for incremental re-index)."""
        result = self._client.delete(
            collection_name=self.collection_name,
            points_selector=Filter(
                must=[FieldCondition(key="file_path", match=MatchValue(value=file_path))]
            ),
            wait=True,
        )
        return result.operation_id or 0

    def close(self) -> None:
        """Close the Qdrant client to release file locks."""
        with contextlib.suppress(Exception):
            self._client.close()

    def health(self) -> bool:
        try:
            self._client.get_collections()
            return True
        except Exception:
            return False

    def stats(self) -> dict[str, Any]:
        info = self._client.get_collection(self.collection_name)
        return {
            "collection": self.collection_name,
            "points_count": info.points_count,
            "vectors_count": info.vectors_count,
            "status": info.status,
        }
