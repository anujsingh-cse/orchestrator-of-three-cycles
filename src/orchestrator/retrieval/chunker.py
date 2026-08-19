"""Tree-sitter chunker with enrichment headers, FQN IDs, incremental indexing (T7, D3, D15)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tree_sitter_python as tspython
    from tree_sitter import Language, Parser
except ImportError:  # pragma: no cover - exercised at runtime
    Language = None
    Parser = None
    tspython = None


CHUNK_MAX_TOKENS = 512
CHUNK_OVERLAP_TOKENS = 50


@dataclass(frozen=True)
class Chunk:
    """A code chunk with enrichment headers and stable FQN-based ID."""

    id: str  # stable FQN-based hash
    file_path: str
    start_line: int
    end_line: int
    content: str
    language: str
    fqn: str | None  # fully qualified name (e.g., "module.Class.method")
    signature: str | None
    imports: list[str]
    enrichment_header: str
    content_hash: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "content": self.content,
            "language": self.language,
            "fqn": self.fqn,
            "signature": self.signature,
            "imports": self.imports,
            "enrichment_header": self.enrichment_header,
            "content_hash": self.content_hash,
        }


class Chunker:
    """Tree-sitter based chunker with enrichment headers and incremental indexing.

    - Parses Python (extensible to other languages via tree-sitter grammars)
    - Extracts functions, classes, methods with FQN (fully qualified names)
    - Builds enrichment headers: file_path + FQN + signature + imports
    - Generates stable chunk IDs from content hash + FQN
    - Supports incremental re-indexing via content-hash map
    """

    def __init__(
        self, max_tokens: int = CHUNK_MAX_TOKENS, overlap_tokens: int = CHUNK_OVERLAP_TOKENS
    ) -> None:
        if Parser is None or tspython is None:
            raise RuntimeError(
                "tree-sitter not installed: pip install tree-sitter tree-sitter-python"
            )
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens
        self._parser = Parser()
        self._parser.language = Language(tspython.language())

    def chunk_file(self, file_path: Path) -> list[Chunk]:
        """Parse a single file and return enriched chunks."""
        content = file_path.read_text(encoding="utf-8")
        tree = self._parser.parse(bytes(content, "utf-8"))
        chunks = self._extract_chunks(tree, content, str(file_path))
        return self._split_oversized_chunks(chunks)

    def chunk_files(self, file_paths: list[Path]) -> list[Chunk]:
        """Parse multiple files and return all chunks."""
        all_chunks: list[Chunk] = []
        for path in file_paths:
            all_chunks.extend(self.chunk_file(path))
        return all_chunks

    def _extract_chunks(
        self, tree, content: str, file_path: str
    ) -> list[Chunk]:
        """Extract function/class/method nodes as chunks with enrichment."""
        chunks: list[Chunk] = []
        imports = self._extract_imports(tree, content)

        def walk(node, parent_fqn: str = ""):
            if node.type in ("function_definition", "class_definition"):
                fqn = self._build_fqn(node, content, parent_fqn)
                signature = self._extract_signature(node, content)
                chunk_content = self._node_text(node, content)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                header = self._build_enrichment_header(
                    file_path, fqn, signature, imports
                )
                content_hash = hashlib.sha256(chunk_content.encode()).hexdigest()[:16]
                chunk_id = hashlib.sha256(f"{fqn}:{content_hash}".encode()).hexdigest()[:16]

                chunks.append(
                    Chunk(
                        id=chunk_id,
                        file_path=file_path,
                        start_line=start_line,
                        end_line=end_line,
                        content=chunk_content,
                        language="python",
                        fqn=fqn,
                        signature=signature,
                        imports=imports.copy(),
                        enrichment_header=header,
                        content_hash=content_hash,
                    )
                )

                # Recurse into class body for methods
                if node.type == "class_definition":
                    for child in node.children:
                        if child.type == "block":
                            for grandchild in child.children:
                                walk(grandchild, fqn)

            for child in node.children:
                walk(child, parent_fqn)

        walk(tree.root_node)
        return chunks

    def _extract_imports(self, tree, content: str) -> list[str]:
        imports: list[str] = []
        for node in self._find_nodes(tree.root_node, "import_statement"):
            imports.append(self._node_text(node, content).strip())
        for node in self._find_nodes(tree.root_node, "import_from_statement"):
            imports.append(self._node_text(node, content).strip())
        return imports

    def _build_fqn(self, node, content: str, parent_fqn: str) -> str:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return parent_fqn or "unknown"
        name = self._node_text(name_node, content)
        if parent_fqn:
            return f"{parent_fqn}.{name}"
        return name

    def _extract_signature(self, node, content: str) -> str | None:
        if node.type == "function_definition":
            params_node = node.child_by_field_name("parameters")
            return_type = node.child_by_field_name("return_type")
            if params_node:
                params = self._node_text(params_node, content)
                ret = f" -> {self._node_text(return_type, content)}" if return_type else ""
                return f"({params}){ret}"
        elif node.type == "class_definition":
            superclasses = node.child_by_field_name("superclasses")
            if superclasses:
                return f"({self._node_text(superclasses, content)})"
        return None

    def _build_enrichment_header(
        self, file_path: str, fqn: str | None, signature: str | None, imports: list[str]
    ) -> str:
        parts = [f"# file: {file_path}"]
        if fqn:
            parts.append(f"# fqn: {fqn}")
        if signature:
            parts.append(f"# signature: {signature}")
        if imports:
            parts.append(f"# imports: {'; '.join(imports[:10])}")  # cap at 10
        return "\n".join(parts)

    def _split_oversized_chunks(self, chunks: list[Chunk]) -> list[Chunk]:
        """Split chunks that exceed max_tokens using line-based approximation."""
        result: list[Chunk] = []
        for chunk in chunks:
            token_estimate = len(chunk.content.split()) * 1.3  # rough token estimate
            if token_estimate <= self.max_tokens:
                result.append(chunk)
            else:
                # Split by lines with overlap
                lines = chunk.content.splitlines()
                chunk_size = max(1, int(self.max_tokens / 1.3))
                overlap = max(1, int(self.overlap_tokens / 1.3))
                for i in range(0, len(lines), chunk_size - overlap):
                    sub_lines = lines[i : i + chunk_size]
                    if not sub_lines:
                        continue
                    sub_content = "\n".join(sub_lines)
                    sub_hash = hashlib.sha256(sub_content.encode()).hexdigest()[:16]
                    sub_id = hashlib.sha256(f"{chunk.fqn}:{sub_hash}".encode()).hexdigest()[:16]
                    start = chunk.start_line + i
                    end = min(chunk.end_line, start + len(sub_lines) - 1)
                    result.append(
                        Chunk(
                            id=sub_id,
                            file_path=chunk.file_path,
                            start_line=start,
                            end_line=end,
                            content=sub_content,
                            language=chunk.language,
                            fqn=chunk.fqn,
                            signature=chunk.signature,
                            imports=chunk.imports,
                            enrichment_header=chunk.enrichment_header,
                            content_hash=sub_hash,
                        )
                    )
        return result

    @staticmethod
    def _find_nodes(node, target_type: str):
        if node.type == target_type:
            yield node
        for child in node.children:
            yield from Chunker._find_nodes(child, target_type)

    @staticmethod
    def _node_text(node, content: str) -> str:
        return content[node.start_byte : node.end_byte]


def compute_content_hash(content: str) -> str:
    """Stable content hash for incremental indexing."""
    return hashlib.sha256(content.encode()).hexdigest()


class IncrementalIndex:
    """Content-hash map for incremental re-indexing (D15).

    Tracks file_path -> content_hash. On re-index, only files with changed
    hashes are re-chunked and re-embedded.
    """

    def __init__(self, index_path: Path) -> None:
        self.index_path = index_path
        self._map: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if self.index_path.exists():
            import json

            try:
                self._map = json.loads(self.index_path.read_text())
            except Exception:
                self._map = {}

    def save(self) -> None:
        import json

        self.index_path.write_text(json.dumps(self._map, indent=2))

    def get_hash(self, file_path: str) -> str | None:
        return self._map.get(file_path)

    def set_hash(self, file_path: str, content_hash: str) -> None:
        self._map[file_path] = content_hash

    def has_changed(self, file_path: str, content: str) -> bool:
        current_hash = compute_content_hash(content)
        stored_hash = self._map.get(file_path)
        return stored_hash != current_hash

    def get_changed_files(self, file_paths: list[Path]) -> list[Path]:
        """Return only files that have changed since last index."""
        changed: list[Path] = []
        for path in file_paths:
            content = path.read_text(encoding="utf-8")
            if self.has_changed(str(path), content):
                changed.append(path)
        return changed
