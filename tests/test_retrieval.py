"""Tests for retrieval module (T7, T8)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from orchestrator.retrieval.chunker import Chunker, IncrementalIndex, compute_content_hash
from orchestrator.retrieval.store import RetrievalQuery, RetrievalStore


@pytest.fixture
def sample_python_file() -> Path:
    content = (
        "import os\n"
        "import sys\n"
        "from typing import List\n"
        "\n"
        'def hello_world(name: str) -> str:\n'
        '    """Say hello."""\n'
        '    return f"Hello, {name}!"\n'
        "\n"
        "class Greeter:\n"
        '    def __init__(self, greeting: str = "Hello"):\n'
        "        self.greeting = greeting\n"
        "\n"
        '    def greet(self, name: str) -> str:\n'
        '        return f"{self.greeting}, {name}!"\n'
        "\n"
        '    def farewell(self, name: str) -> str:\n'
        '        return f"Goodbye, {name}!"\n'
        "\n"
        "def main() -> None:\n"
        '    greeter = Greeter()\n'
        '    print(greeter.greet("World"))\n'
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(content)
        return Path(f.name)


def test_chunker_extracts_functions_and_classes(sample_python_file: Path) -> None:
    chunker = Chunker()
    chunks = chunker.chunk_file(sample_python_file)

    assert len(chunks) >= 4  # hello_world, Greeter, greet, farewell, main

    fqns = {c.fqn for c in chunks}
    assert "hello_world" in fqns
    assert "Greeter" in fqns
    assert "Greeter.greet" in fqns
    assert "Greeter.farewell" in fqns
    assert "main" in fqns


def test_chunker_enrichment_headers(sample_python_file: Path) -> None:
    chunker = Chunker()
    chunks = chunker.chunk_file(sample_python_file)

    for chunk in chunks:
        assert chunk.enrichment_header
        assert "# file:" in chunk.enrichment_header
        assert "# fqn:" in chunk.enrichment_header
        assert "# imports:" in chunk.enrichment_header


def test_chunker_stable_ids(sample_python_file: Path) -> None:
    chunker = Chunker()
    chunks1 = chunker.chunk_file(sample_python_file)
    chunks2 = chunker.chunk_file(sample_python_file)

    ids1 = {c.id for c in chunks1}
    ids2 = {c.id for c in chunks2}
    assert ids1 == ids2  # stable IDs across runs


def test_incremental_index(sample_python_file: Path) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        index_path = Path(tmpdir) / "index.json"
        index = IncrementalIndex(index_path)

        content = sample_python_file.read_text()
        assert index.has_changed(str(sample_python_file), content)

        index.set_hash(str(sample_python_file), compute_content_hash(content))
        index.save()

        index2 = IncrementalIndex(index_path)
        assert not index2.has_changed(str(sample_python_file), content)

        # Modified content
        assert index2.has_changed(str(sample_python_file), content + "\n# changed")


def test_store_upsert_and_query(sample_python_file: Path) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = RetrievalStore(Path(tmpdir) / "qdrant")
        try:
            chunker = Chunker()
            chunks = chunker.chunk_file(sample_python_file)

            count = store.upsert_chunks(chunks)
            assert count == len(chunks)

            # Query for a function
            results = store.query(RetrievalQuery(text="hello_world", top_k=5))
            assert len(results) > 0
            assert any(r.chunk.fqn == "hello_world" for r in results)

            # Query for class method
            results = store.query(RetrievalQuery(text="greet", top_k=5))
            assert len(results) > 0
            assert any(r.chunk.fqn == "Greeter.greet" for r in results)
        finally:
            store.close()


def test_store_exact_match_boost(sample_python_file: Path) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = RetrievalStore(Path(tmpdir) / "qdrant")
        try:
            chunker = Chunker()
            chunks = chunker.chunk_file(sample_python_file)
            store.upsert_chunks(chunks)

            # Exact function name query should rank that function higher
            results = store.query(RetrievalQuery(text="farewell", top_k=5))
            assert len(results) > 0
            top = results[0]
            assert "farewell" in (top.chunk.fqn or "").lower()
        finally:
            store.close()


def test_store_filter_by_path(sample_python_file: Path) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = RetrievalStore(Path(tmpdir) / "qdrant")
        try:
            chunker = Chunker()
            chunks = chunker.chunk_file(sample_python_file)
            store.upsert_chunks(chunks)

            # Filter by file path
            results = store.query(
                RetrievalQuery(text="hello", top_k=5, filter_path=str(sample_python_file))
            )
            assert len(results) > 0
            for r in results:
                assert r.chunk.file_path == str(sample_python_file)
        finally:
            store.close()


def test_store_delete_file(sample_python_file: Path) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = RetrievalStore(Path(tmpdir) / "qdrant")
        try:
            chunker = Chunker()
            chunks = chunker.chunk_file(sample_python_file)
            store.upsert_chunks(chunks)

            # Verify chunks exist
            results = store.query(RetrievalQuery(text="hello", top_k=5))
            assert len(results) > 0

            # Delete file
            store.delete_file(str(sample_python_file))

            # Verify chunks are gone
            results = store.query(RetrievalQuery(text="hello", top_k=5))
            assert len(results) == 0
        finally:
            store.close()


def test_chunker_handles_large_chunks() -> None:
    """Test that oversized chunks are split with overlap."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        # Create a large function
        lines = ["def large_function() -> None:"]
        for i in range(200):
            lines.append(f"    x_{i} = {i}")
        lines.append("    return None")
        f.write("\n".join(lines))
        path = Path(f.name)

    try:
        chunker = Chunker(max_tokens=100, overlap_tokens=20)
        chunks = chunker.chunk_file(path)

        # Should be split into multiple chunks
        assert len(chunks) > 1
        # All chunks should have the same FQN
        fqns = {c.fqn for c in chunks}
        assert fqns == {"large_function"}
    finally:
        path.unlink()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
