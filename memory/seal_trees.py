"""
SEAL Trees — 5 estructuras de datos arbóreas para el sistema SOUL.

1. MerkleSoul   — Integridad del alma (tampering detection)
2. SplayCache   — Cache adaptativo L1 (memorias calientes)
3. TrieIndex    — Lookup por prefijo (procedures, tools MCP)
4. FenwickStats — Range queries (importancia, utilidad)
5. RSpatialIndex — Indexación espacial (Mining, Medical)

Autor: ADA (Team SEAL)
Fecha: 2026-04-08
Aprobado por: William (Dadito)
Propuesta original: JARVIS
"""

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional


# =============================================================================
# 1. MerkleSoul — Integridad del alma
# =============================================================================

class MerkleSoul:
    """
    Merkle Tree para verificar integridad del SOUL.
    Cada hoja = hash de una categoría (OCEAN, memories, beliefs, rules, etc.).
    Si algo se modifica sin pasar por canales legítimos, el root hash cambia.

    Uso:
        tree = MerkleSoul()
        tree.update_leaf("ocean", {"A": 0.505, "C": 1.0, ...})
        tree.update_leaf("rules", [...])
        root = tree.root_hash
        # Después de operación legítima:
        tree.sign_checkpoint()
        # Para verificar:
        tree.verify_integrity()  # True si nada cambió ilegítimamente
    """

    def __init__(self):
        self._leaves: dict[str, str] = {}
        self._signed_root: Optional[str] = None
        self._signed_at: Optional[float] = None
        self._checkpoint_history: list[dict] = []

    @staticmethod
    def _hash(data: str) -> str:
        return hashlib.sha256(data.encode()).hexdigest()

    def update_leaf(self, category: str, data: Any) -> str:
        """Update a leaf node with new data. Returns the leaf hash."""
        serialized = json.dumps(data, sort_keys=True, default=str)
        leaf_hash = self._hash(serialized)
        self._leaves[category] = leaf_hash
        return leaf_hash

    def remove_leaf(self, category: str) -> bool:
        """Remove a leaf node. Returns True if it existed."""
        if category in self._leaves:
            del self._leaves[category]
            return True
        return False

    @property
    def root_hash(self) -> Optional[str]:
        """Compute the Merkle root from all leaves."""
        if not self._leaves:
            return None
        sorted_keys = sorted(self._leaves.keys())
        hashes = [self._leaves[k] for k in sorted_keys]
        while len(hashes) > 1:
            next_level = []
            for i in range(0, len(hashes), 2):
                left = hashes[i]
                right = hashes[i + 1] if i + 1 < len(hashes) else left
                next_level.append(self._hash(left + right))
            hashes = next_level
        return hashes[0]

    def sign_checkpoint(self, metadata: Optional[dict] = None) -> dict:
        """Sign the current state as legitimate. Call after every valid operation."""
        root = self.root_hash
        checkpoint = {
            "root_hash": root,
            "signed_at": time.time(),
            "leaf_count": len(self._leaves),
            "categories": sorted(self._leaves.keys()),
            "metadata": metadata or {},
        }
        self._signed_root = root
        self._signed_at = checkpoint["signed_at"]
        self._checkpoint_history.append(checkpoint)
        return checkpoint

    def verify_integrity(self) -> dict:
        """Check if current state matches last signed checkpoint."""
        current_root = self.root_hash
        is_valid = current_root == self._signed_root
        return {
            "valid": is_valid,
            "current_root": current_root,
            "signed_root": self._signed_root,
            "signed_at": self._signed_at,
            "drift_detected": not is_valid and self._signed_root is not None,
        }

    def get_proof(self, category: str) -> Optional[list[tuple[str, str]]]:
        """Generate Merkle proof for a specific category."""
        if category not in self._leaves:
            return None
        sorted_keys = sorted(self._leaves.keys())
        hashes = [self._leaves[k] for k in sorted_keys]
        index = sorted_keys.index(category)
        proof = []
        while len(hashes) > 1:
            next_level = []
            for i in range(0, len(hashes), 2):
                left = hashes[i]
                right = hashes[i + 1] if i + 1 < len(hashes) else left
                next_level.append(self._hash(left + right))
            sibling_idx = index ^ 1
            if sibling_idx < len(hashes):
                direction = "right" if index % 2 == 0 else "left"
                proof.append((hashes[sibling_idx], direction))
            index //= 2
            hashes = next_level
        return proof

    def verify_proof(self, category: str, data: Any, proof: list[tuple[str, str]]) -> bool:
        """Verify a Merkle proof for given data."""
        serialized = json.dumps(data, sort_keys=True, default=str)
        current = self._hash(serialized)
        for sibling_hash, direction in proof:
            if direction == "right":
                current = self._hash(current + sibling_hash)
            else:
                current = self._hash(sibling_hash + current)
        return current == self.root_hash

    @property
    def checkpoint_count(self) -> int:
        return len(self._checkpoint_history)

    @property
    def last_checkpoint(self) -> Optional[dict]:
        return self._checkpoint_history[-1] if self._checkpoint_history else None


# =============================================================================
# 2. SplayCache — Cache adaptativo L1
# =============================================================================

@dataclass
class _SplayNode:
    key: Any
    value: Any
    left: Optional["_SplayNode"] = None
    right: Optional["_SplayNode"] = None
    access_count: int = 0
    last_access: float = 0.0


class SplayCache:
    """
    Splay Tree como cache L1 adaptativo para memorias.
    Elementos accedidos frecuentemente se mueven a la raíz.
    Distribución Zipf natural: 20% de memorias = 80% de accesos.

    Uso:
        cache = SplayCache(max_size=500)
        cache.put("ocean_scores", {...})
        result = cache.get("ocean_scores")  # O(1) amortizado si es caliente
        stats = cache.stats()
    """

    def __init__(self, max_size: int = 500):
        self._root: Optional[_SplayNode] = None
        self._size: int = 0
        self._max_size: int = max_size
        self._hits: int = 0
        self._misses: int = 0
        self._evictions: int = 0

    def _rotate_right(self, node: _SplayNode) -> _SplayNode:
        left = node.left
        node.left = left.right
        left.right = node
        return left

    def _rotate_left(self, node: _SplayNode) -> _SplayNode:
        right = node.right
        node.right = right.left
        right.left = node
        return right

    @staticmethod
    def _cmp_key(k: Any) -> tuple:
        """Normalize key for safe cross-type comparison."""
        return (0, k) if isinstance(k, str) else (1, str(k))

    def _lt(self, a: Any, b: Any) -> bool:
        return self._cmp_key(a) < self._cmp_key(b)

    def _gt(self, a: Any, b: Any) -> bool:
        return self._cmp_key(a) > self._cmp_key(b)

    def _splay(self, root: Optional[_SplayNode], key: Any) -> Optional[_SplayNode]:
        if root is None or root.key == key:
            return root
        if self._lt(key, root.key):
            if root.left is None:
                return root
            if self._lt(key, root.left.key):
                root.left.left = self._splay(root.left.left, key)
                root = self._rotate_right(root)
            elif self._gt(key, root.left.key):
                root.left.right = self._splay(root.left.right, key)
                if root.left.right:
                    root.left = self._rotate_left(root.left)
            return self._rotate_right(root) if root.left else root
        else:
            if root.right is None:
                return root
            if self._gt(key, root.right.key):
                root.right.right = self._splay(root.right.right, key)
                root = self._rotate_left(root)
            elif self._lt(key, root.right.key):
                root.right.left = self._splay(root.right.left, key)
                if root.right.left:
                    root.right = self._rotate_right(root.right)
            return self._rotate_left(root) if root.right else root

    def get(self, key: Any) -> Optional[Any]:
        """Get value by key. Moves accessed node to root (splay)."""
        self._root = self._splay(self._root, key)
        if self._root and self._root.key == key:
            self._hits += 1
            self._root.access_count += 1
            self._root.last_access = time.time()
            return self._root.value
        self._misses += 1
        return None

    def put(self, key: Any, value: Any) -> None:
        """Insert or update a key-value pair."""
        if self._root is None:
            self._root = _SplayNode(key=key, value=value, last_access=time.time())
            self._size = 1
            return

        self._root = self._splay(self._root, key)
        if self._root.key == key:
            self._root.value = value
            self._root.access_count += 1
            self._root.last_access = time.time()
            return

        node = _SplayNode(key=key, value=value, last_access=time.time())
        if self._lt(key, self._root.key):
            node.right = self._root
            node.left = self._root.left
            self._root.left = None
        else:
            node.left = self._root
            node.right = self._root.right
            self._root.right = None
        self._root = node
        self._size += 1

        if self._size > self._max_size:
            self._evict_coldest()

    def delete(self, key: Any) -> bool:
        """Remove a key. Returns True if found and removed."""
        if self._root is None:
            return False
        self._root = self._splay(self._root, key)
        if self._root.key != key:
            return False
        if self._root.left is None:
            self._root = self._root.right
        else:
            right = self._root.right
            self._root = self._splay(self._root.left, key)
            self._root.right = right
        self._size -= 1
        return True

    def _evict_coldest(self) -> None:
        """Remove the least recently accessed node by last_access timestamp."""
        if self._root is None:
            return
        # Find the key with oldest last_access via in-order traversal
        coldest_key = None
        coldest_time = float("inf")
        stack = [self._root]
        while stack:
            node = stack.pop()
            if node is None:
                continue
            if node.last_access < coldest_time:
                coldest_time = node.last_access
                coldest_key = node.key
            stack.append(node.left)
            stack.append(node.right)
        if coldest_time < float("inf"):
            # Splay then remove
            self._root = self._splay(self._root, coldest_key)
            if self._root and self._root.key == coldest_key:
                if self._root.left is None:
                    self._root = self._root.right
                else:
                    right = self._root.right
                    self._root = self._splay(self._root.left, coldest_key)
                    self._root.right = right
                self._size -= 1
                self._evictions += 1

    def contains(self, key: Any) -> bool:
        """Check if key exists without splaying."""
        node = self._root
        while node:
            if key == node.key:
                return True
            elif self._lt(key, node.key):
                node = node.left
            else:
                node = node.right
        return False

    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "size": self._size,
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / total if total > 0 else 0.0,
            "evictions": self._evictions,
        }

    def clear(self) -> None:
        self._root = None
        self._size = 0

    @property
    def size(self) -> int:
        return self._size


# =============================================================================
# 3. TrieIndex — Lookup por prefijo
# =============================================================================

@dataclass
class _TrieNode:
    children: dict[str, "_TrieNode"] = field(default_factory=dict)
    is_end: bool = False
    value: Any = None
    count: int = 0


class TrieIndex:
    """
    Trie para búsqueda O(L) por prefijo de procedures, tools MCP, comandos.

    Uso:
        trie = TrieIndex()
        trie.insert("memory_search", {"tool": "memory_search", "category": "memory"})
        trie.insert("memory_store", {"tool": "memory_store", "category": "memory"})
        results = trie.search_prefix("memory_")  # Ambos
        exact = trie.search("memory_search")  # Uno
    """

    def __init__(self):
        self._root = _TrieNode()
        self._size = 0

    def insert(self, key: str, value: Any = None) -> None:
        """Insert a key with optional associated value."""
        node = self._root
        for char in key:
            if char not in node.children:
                node.children[char] = _TrieNode()
            node = node.children[char]
            node.count += 1
        node.is_end = True
        node.value = value
        self._size += 1

    def search(self, key: str) -> Optional[Any]:
        """Exact search. Returns value if found, None otherwise."""
        node = self._traverse(key)
        if node and node.is_end:
            return node.value
        return None

    def has_key(self, key: str) -> bool:
        """Check if exact key exists."""
        node = self._traverse(key)
        return node is not None and node.is_end

    def search_prefix(self, prefix: str) -> list[tuple[str, Any]]:
        """Find all entries matching a prefix. Returns list of (key, value)."""
        node = self._traverse(prefix)
        if not node:
            return []
        results = []
        self._collect(node, prefix, results)
        return results

    def delete(self, key: str) -> bool:
        """Delete a key. Returns True if found and removed."""
        if not self.has_key(key):
            return False
        self._delete(self._root, key, 0)
        return True

    def autocomplete(self, prefix: str, limit: int = 10) -> list[str]:
        """Return up to `limit` keys matching prefix."""
        results = self.search_prefix(prefix)
        return [k for k, _ in results[:limit]]

    def _traverse(self, prefix: str) -> Optional[_TrieNode]:
        node = self._root
        for char in prefix:
            if char not in node.children:
                return None
            node = node.children[char]
        return node

    def _collect(self, node: _TrieNode, prefix: str, results: list) -> None:
        if node.is_end:
            results.append((prefix, node.value))
        for char in sorted(node.children.keys()):
            self._collect(node.children[char], prefix + char, results)

    def _delete(self, node: _TrieNode, key: str, depth: int) -> bool:
        if depth == len(key):
            if not node.is_end:
                return False
            node.is_end = False
            node.value = None
            self._size -= 1
            return len(node.children) == 0
        char = key[depth]
        if char not in node.children:
            return False
        should_delete = self._delete(node.children[char], key, depth + 1)
        if should_delete:
            del node.children[char]
            node.count -= 1
            return not node.is_end and len(node.children) == 0
        node.count -= 1
        return False

    def count_prefix(self, prefix: str) -> int:
        """Count how many keys have this prefix."""
        node = self._traverse(prefix)
        return node.count if node else 0

    @property
    def size(self) -> int:
        return self._size


# =============================================================================
# 4. FenwickStats — Range queries de utilidad/importancia
# =============================================================================

class FenwickStats:
    """
    Fenwick Tree (BIT) para range queries O(log n) sobre utilidad/importancia.
    Soporta: prefix sums, range sums, point updates.

    Uso:
        ft = FenwickStats(1000)  # Para hasta 1000 memorias
        ft.update(42, 8)   # Memoria #42 tiene importancia 8
        ft.update(100, 5)  # Memoria #100 tiene importancia 5
        total = ft.prefix_sum(100)  # Suma de importancia de memorias 1..100
        rango = ft.range_sum(42, 100)  # Suma de importancia de memorias 42..100
    """

    def __init__(self, n: int):
        self._n = n
        self._tree = [0] * (n + 1)  # 1-indexed
        self._count = 0

    def update(self, i: int, delta: int) -> None:
        """Add delta to position i. O(log n)."""
        if i < 1 or i > self._n:
            raise IndexError(f"Index {i} out of range [1, {self._n}]")
        while i <= self._n:
            self._tree[i] += delta
            i += i & (-i)
        self._count += 1

    def prefix_sum(self, i: int) -> int:
        """Sum of elements [1..i]. O(log n)."""
        if i < 0:
            return 0
        i = min(i, self._n)
        total = 0
        while i > 0:
            total += self._tree[i]
            i -= i & (-i)
        return total

    def range_sum(self, left: int, right: int) -> int:
        """Sum of elements [left..right]. O(log n)."""
        if left > right:
            return 0
        return self.prefix_sum(right) - self.prefix_sum(left - 1)

    def point_query(self, i: int) -> int:
        """Get value at position i. O(log n)."""
        return self.range_sum(i, i)

    def find_kth(self, k: int) -> int:
        """Find smallest index with prefix sum >= k. O(log^2 n)."""
        lo, hi = 1, self._n
        while lo < hi:
            mid = (lo + hi) // 2
            if self.prefix_sum(mid) >= k:
                hi = mid
            else:
                lo = mid + 1
        return lo

    @classmethod
    def from_array(cls, arr: list[int]) -> "FenwickStats":
        """Build from array in O(n)."""
        n = len(arr)
        ft = cls(n)
        for i in range(1, n + 1):
            ft._tree[i] = arr[i - 1]
        for i in range(1, n + 1):
            j = i + (i & (-i))
            if j <= n:
                ft._tree[j] += ft._tree[i]
        ft._count = n
        return ft

    @property
    def capacity(self) -> int:
        return self._n

    @property
    def total(self) -> int:
        return self.prefix_sum(self._n)


# =============================================================================
# 5. RSpatialIndex — Indexación espacial (R-Tree simplificado)
# =============================================================================

@dataclass
class BoundingBox:
    """Minimum Bounding Rectangle (MBR) for spatial objects."""
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def area(self) -> float:
        return max(0, self.max_x - self.min_x) * max(0, self.max_y - self.min_y)

    def contains(self, other: "BoundingBox") -> bool:
        return (self.min_x <= other.min_x and self.min_y <= other.min_y and
                self.max_x >= other.max_x and self.max_y >= other.max_y)

    def intersects(self, other: "BoundingBox") -> bool:
        return not (self.max_x < other.min_x or other.max_x < self.min_x or
                    self.max_y < other.min_y or other.max_y < self.min_y)

    def expand_to_include(self, other: "BoundingBox") -> "BoundingBox":
        return BoundingBox(
            min(self.min_x, other.min_x),
            min(self.min_y, other.min_y),
            max(self.max_x, other.max_x),
            max(self.max_y, other.max_y),
        )

    def expansion_area(self, other: "BoundingBox") -> float:
        """How much area increases if we include other."""
        expanded = self.expand_to_include(other)
        return expanded.area() - self.area()

    def contains_point(self, x: float, y: float) -> bool:
        return self.min_x <= x <= self.max_x and self.min_y <= y <= self.max_y

    @classmethod
    def from_point(cls, x: float, y: float) -> "BoundingBox":
        return cls(x, y, x, y)


@dataclass
class SpatialEntry:
    """An entry in the R-Tree: a bounding box + associated data."""
    bbox: BoundingBox
    data: Any = None


@dataclass
class _RNode:
    entries: list = field(default_factory=list)
    children: list["_RNode"] = field(default_factory=list)
    is_leaf: bool = True
    bbox: Optional[BoundingBox] = None

    @property
    def is_full(self) -> bool:
        if self.is_leaf:
            return len(self.entries) >= 4
        return len(self.children) >= 4


class RSpatialIndex:
    """
    Simplified R-Tree for spatial indexing.
    Supports: insert, search (range query), nearest, point query.

    Uso (Mining — concesiones INGEMMET):
        rtree = RSpatialIndex()
        rtree.insert(BoundingBox(-6.77, -79.84, -6.75, -79.82), {"name": "Concesión A"})
        results = rtree.search(BoundingBox(-7.0, -80.0, -6.5, -79.5))  # Todas en el rango

    Uso (Medical — localización anatómica):
        rtree = RSpatialIndex()
        rtree.insert(BoundingBox(100, 200, 150, 250), {"organ": "liver", "slice": 42})
    """

    def __init__(self, max_entries: int = 4):
        self._root = _RNode()
        self._size = 0
        self._max_entries = max_entries

    def insert(self, bbox: BoundingBox, data: Any = None) -> None:
        """Insert a spatial entry."""
        entry = SpatialEntry(bbox=bbox, data=data)
        split_result = self._insert_recursive(self._root, entry)
        if split_result is not None:
            old_root = self._root
            self._root = _RNode(is_leaf=False, children=[old_root, split_result])
            self._update_bbox(self._root)
        self._size += 1

    def search(self, query_bbox: BoundingBox) -> list[SpatialEntry]:
        """Range query: find all entries intersecting the query box."""
        results = []
        self._search_recursive(self._root, query_bbox, results)
        return results

    def search_point(self, x: float, y: float) -> list[SpatialEntry]:
        """Find all entries containing the given point."""
        point_bbox = BoundingBox.from_point(x, y)
        return self.search(point_bbox)

    def nearest(self, x: float, y: float, k: int = 1) -> list[tuple[float, SpatialEntry]]:
        """Find k nearest entries to a point. Returns list of (distance, entry)."""
        all_entries = []
        self._collect_all(self._root, all_entries)

        def dist_to_entry(entry: SpatialEntry) -> float:
            cx = (entry.bbox.min_x + entry.bbox.max_x) / 2
            cy = (entry.bbox.min_y + entry.bbox.max_y) / 2
            return ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5

        scored = [(dist_to_entry(e), e) for e in all_entries]
        scored.sort(key=lambda t: t[0])
        return scored[:k]

    def _insert_recursive(self, node: _RNode, entry: SpatialEntry) -> Optional[_RNode]:
        """Insert entry into subtree. Returns a new sibling node if split occurred, else None."""
        if node.is_leaf:
            node.entries.append(entry)
            self._update_bbox(node)
            if len(node.entries) > self._max_entries:
                return self._split_node(node)
            return None

        # Choose best child (least expansion)
        best_child = node.children[0]
        best_expansion = float("inf")
        for child in node.children:
            if child.bbox:
                expansion = child.bbox.expansion_area(entry.bbox)
                if expansion < best_expansion:
                    best_expansion = expansion
                    best_child = child

        split_result = self._insert_recursive(best_child, entry)
        self._update_bbox(node)

        if split_result is not None:
            node.children.append(split_result)
            self._update_bbox(node)
            if len(node.children) > self._max_entries:
                return self._split_node(node)
        return None

    def _update_bbox(self, node: _RNode) -> None:
        boxes = []
        for entry in node.entries:
            boxes.append(entry.bbox)
        for child in node.children:
            if child.bbox:
                boxes.append(child.bbox)
        if boxes:
            node.bbox = boxes[0]
            for b in boxes[1:]:
                node.bbox = node.bbox.expand_to_include(b)

    def _split_node(self, node: _RNode) -> _RNode:
        """Split a node and return the new sibling. Original node keeps first half."""
        if node.is_leaf:
            entries = node.entries
            entries.sort(key=lambda e: e.bbox.min_x)
            mid = len(entries) // 2
            node.entries = entries[:mid]
            new_node = _RNode(entries=entries[mid:])
        else:
            children = node.children
            children.sort(key=lambda c: c.bbox.min_x if c.bbox else 0)
            mid = len(children) // 2
            node.children = children[:mid]
            new_node = _RNode(is_leaf=False, children=children[mid:])
        self._update_bbox(node)
        self._update_bbox(new_node)
        return new_node

    def _search_recursive(self, node: _RNode, query: BoundingBox, results: list) -> None:
        if node.bbox and not node.bbox.intersects(query):
            return
        for entry in node.entries:
            if entry.bbox.intersects(query):
                results.append(entry)
        for child in node.children:
            self._search_recursive(child, query, results)

    def _collect_all(self, node: _RNode, results: list) -> None:
        results.extend(node.entries)
        for child in node.children:
            self._collect_all(child, results)

    @property
    def size(self) -> int:
        return self._size

    def clear(self) -> None:
        self._root = _RNode()
        self._size = 0
