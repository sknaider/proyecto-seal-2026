"""
Tests para seal_trees.py — 5 estructuras de datos arbóreas SEAL.
Autor: ADA (Team SEAL)
Fecha: 2026-04-08
"""

import sys
import time

sys.path.insert(0, "/home/dadito/IA/proyecto-seal/memory")

from seal_trees import (
    MerkleSoul,
    SplayCache,
    TrieIndex,
    FenwickStats,
    RSpatialIndex,
    BoundingBox,
)

PASS = 0
FAIL = 0


def assert_eq(name, got, expected):
    global PASS, FAIL
    if got == expected:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL {name}: got {got!r}, expected {expected!r}")


def assert_true(name, val):
    assert_eq(name, bool(val), True)


def assert_false(name, val):
    assert_eq(name, bool(val), False)


def assert_close(name, got, expected, tol=0.01):
    global PASS, FAIL
    if abs(got - expected) < tol:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL {name}: got {got}, expected ~{expected}")


# =============================================================================
# 1. MerkleSoul
# =============================================================================
print("=== MerkleSoul ===")

m = MerkleSoul()

# Empty tree
assert_eq("empty root", m.root_hash, None)

# Add leaves
m.update_leaf("ocean", {"A": 0.505, "C": 1.0, "E": 0.78})
m.update_leaf("rules", ["rule1", "rule2"])
m.update_leaf("beliefs", ["belief1"])
root1 = m.root_hash
assert_true("root not none", root1)

# Same data = same hash (deterministic)
m2 = MerkleSoul()
m2.update_leaf("ocean", {"A": 0.505, "C": 1.0, "E": 0.78})
m2.update_leaf("rules", ["rule1", "rule2"])
m2.update_leaf("beliefs", ["belief1"])
assert_eq("deterministic hash", m2.root_hash, root1)

# Modified data = different hash
m.update_leaf("ocean", {"A": 0.6, "C": 1.0, "E": 0.78})
root2 = m.root_hash
assert_true("different hash after change", root2 != root1)

# Sign checkpoint and verify
m.update_leaf("ocean", {"A": 0.505, "C": 1.0, "E": 0.78})
cp = m.sign_checkpoint(metadata={"reason": "boot"})
assert_true("checkpoint has root", cp["root_hash"])
assert_eq("checkpoint leaf count", cp["leaf_count"], 3)
result = m.verify_integrity()
assert_true("integrity valid", result["valid"])
assert_false("no drift", result["drift_detected"])

# Tamper detection
m.update_leaf("ocean", {"A": 0.999, "C": 0.0})
result = m.verify_integrity()
assert_false("tamper detected - not valid", result["valid"])
assert_true("drift detected", result["drift_detected"])

# Merkle proof
m.update_leaf("ocean", {"A": 0.505, "C": 1.0, "E": 0.78})
m.sign_checkpoint()
proof = m.get_proof("ocean")
assert_true("proof exists", proof is not None)
verified = m.verify_proof("ocean", {"A": 0.505, "C": 1.0, "E": 0.78}, proof)
assert_true("proof verifies", verified)
bad_verify = m.verify_proof("ocean", {"A": 0.999}, proof)
assert_false("bad proof fails", bad_verify)

# Remove leaf
removed = m.remove_leaf("beliefs")
assert_true("remove existing leaf", removed)
assert_false("remove nonexistent", m.remove_leaf("nonexistent"))

# Checkpoint history
assert_eq("checkpoint count", m.checkpoint_count, 2)
assert_true("last checkpoint exists", m.last_checkpoint is not None)

print(f"  MerkleSoul: {PASS} passed")

# =============================================================================
# 2. SplayCache
# =============================================================================
print("\n=== SplayCache ===")
prev_pass = PASS

cache = SplayCache(max_size=5)

# Basic put/get
cache.put("ocean", {"A": 0.505})
cache.put("rules", ["rule1"])
cache.put("beliefs", ["belief1"])
assert_eq("get ocean", cache.get("ocean"), {"A": 0.505})
assert_eq("get miss", cache.get("nonexistent"), None)

# Hit rate
stats = cache.stats()
assert_eq("hits", stats["hits"], 1)
assert_eq("misses", stats["misses"], 1)
assert_close("hit rate", stats["hit_rate"], 0.5)

# Splay property: accessed node should be fast on second access
cache.get("ocean")
cache.get("ocean")
assert_eq("size", cache.size, 3)

# Eviction at max_size
cache.put("a", 1)
cache.put("b", 2)
assert_eq("at max", cache.size, 5)
cache.put("c", 3)  # Should trigger eviction
assert_eq("after eviction", cache.size, 5)
stats = cache.stats()
assert_true("evictions happened", stats["evictions"] > 0)

# Contains (non-splaying)
assert_true("contains ocean", cache.contains("ocean"))
assert_false("not contains z", cache.contains("zzz"))

# Delete
deleted = cache.delete("ocean")
assert_true("delete existing", deleted)
assert_eq("get after delete", cache.get("ocean"), None)
assert_false("delete nonexistent", cache.delete("zzz"))

# Update existing key
cache.put("rules", ["rule1", "rule2"])
assert_eq("updated value", cache.get("rules"), ["rule1", "rule2"])

# Clear
cache.clear()
assert_eq("cleared size", cache.size, 0)

print(f"  SplayCache: {PASS - prev_pass} passed")

# =============================================================================
# 3. TrieIndex
# =============================================================================
print("\n=== TrieIndex ===")
prev_pass = PASS

trie = TrieIndex()

# Insert and search
trie.insert("memory_search", {"tool": True})
trie.insert("memory_store", {"tool": True})
trie.insert("memory_list", {"tool": True})
trie.insert("soul_snapshot", {"tool": True})
trie.insert("soul_check", {"tool": True})

assert_eq("exact search", trie.search("memory_search"), {"tool": True})
assert_eq("search miss", trie.search("memory_"), None)
assert_true("has key", trie.has_key("soul_check"))
assert_false("no key", trie.has_key("soul_"))

# Prefix search
mem_tools = trie.search_prefix("memory_")
assert_eq("prefix count", len(mem_tools), 3)
soul_tools = trie.search_prefix("soul_")
assert_eq("soul prefix count", len(soul_tools), 2)

# Autocomplete
completions = trie.autocomplete("mem", limit=2)
assert_eq("autocomplete count", len(completions), 2)
assert_true("autocomplete has memory_list", "memory_list" in completions)

# Count prefix
assert_eq("count memory_", trie.count_prefix("memory_"), 3)
assert_eq("count soul_", trie.count_prefix("soul_"), 2)
assert_eq("count nonexistent", trie.count_prefix("zzz"), 0)

# Delete
assert_true("delete existing", trie.delete("memory_list"))
assert_false("gone after delete", trie.has_key("memory_list"))
assert_eq("prefix after delete", len(trie.search_prefix("memory_")), 2)
assert_false("delete nonexistent", trie.delete("zzz"))

# Size
assert_eq("size", trie.size, 4)

# Empty prefix
all_results = trie.search_prefix("")
assert_eq("all entries", len(all_results), 4)

print(f"  TrieIndex: {PASS - prev_pass} passed")

# =============================================================================
# 4. FenwickStats
# =============================================================================
print("\n=== FenwickStats ===")
prev_pass = PASS

# Basic operations
ft = FenwickStats(10)
ft.update(1, 3)
ft.update(2, 5)
ft.update(3, 7)
ft.update(5, 2)
ft.update(10, 4)

assert_eq("prefix_sum(3)", ft.prefix_sum(3), 15)
assert_eq("prefix_sum(5)", ft.prefix_sum(5), 17)
assert_eq("prefix_sum(10)", ft.prefix_sum(10), 21)
assert_eq("range_sum(2,5)", ft.range_sum(2, 5), 14)
assert_eq("range_sum(1,1)", ft.range_sum(1, 1), 3)
assert_eq("point_query(3)", ft.point_query(3), 7)
assert_eq("total", ft.total, 21)

# Negative delta (decrement)
ft.update(3, -2)
assert_eq("after decrement", ft.point_query(3), 5)

# From array
arr = [1, 2, 3, 4, 5]
ft2 = FenwickStats.from_array(arr)
assert_eq("from_array prefix(3)", ft2.prefix_sum(3), 6)
assert_eq("from_array total", ft2.total, 15)
assert_eq("from_array range(2,4)", ft2.range_sum(2, 4), 9)

# Edge cases
assert_eq("prefix_sum(0)", ft.prefix_sum(0), 0)
assert_eq("range_sum reversed", ft.range_sum(5, 2), 0)

# Boundary
try:
    ft.update(0, 1)
    assert_true("should raise for 0", False)
except IndexError:
    PASS += 1

try:
    ft.update(11, 1)
    assert_true("should raise for out of range", False)
except IndexError:
    PASS += 1

# find_kth
ft3 = FenwickStats(5)
ft3.update(1, 1)
ft3.update(2, 1)
ft3.update(3, 1)
ft3.update(4, 1)
ft3.update(5, 1)
assert_eq("find_kth(3)", ft3.find_kth(3), 3)

# Capacity
assert_eq("capacity", ft.capacity, 10)

print(f"  FenwickStats: {PASS - prev_pass} passed")

# =============================================================================
# 5. RSpatialIndex
# =============================================================================
print("\n=== RSpatialIndex ===")
prev_pass = PASS

# BoundingBox tests
bb1 = BoundingBox(0, 0, 10, 10)
bb2 = BoundingBox(5, 5, 15, 15)
bb3 = BoundingBox(20, 20, 30, 30)

assert_eq("area", bb1.area(), 100.0)
assert_true("intersects", bb1.intersects(bb2))
assert_false("not intersects", bb1.intersects(bb3))
assert_true("contains point", bb1.contains_point(5, 5))
assert_false("not contains point", bb1.contains_point(15, 15))

expanded = bb1.expand_to_include(bb2)
assert_eq("expanded min_x", expanded.min_x, 0)
assert_eq("expanded max_x", expanded.max_x, 15)

bb_small = BoundingBox(2, 2, 8, 8)
assert_true("contains bbox", bb1.contains(bb_small))
assert_false("not contains bbox", bb_small.contains(bb1))

point_bb = BoundingBox.from_point(5, 5)
assert_eq("from_point", point_bb.area(), 0.0)

# R-Tree insert and search
rtree = RSpatialIndex()

# Mining concessions (Perú coords approximation)
rtree.insert(BoundingBox(-6.77, -79.84, -6.75, -79.82), {"name": "Concesión A"})
rtree.insert(BoundingBox(-6.80, -79.90, -6.78, -79.88), {"name": "Concesión B"})
rtree.insert(BoundingBox(-7.10, -80.10, -7.05, -80.05), {"name": "Concesión C"})
rtree.insert(BoundingBox(-5.50, -78.50, -5.45, -78.45), {"name": "Concesión D"})

assert_eq("rtree size", rtree.size, 4)

# Range query — should find A and B (near Chiclayo)
results = rtree.search(BoundingBox(-6.85, -79.95, -6.70, -79.75))
names = {r.data["name"] for r in results}
assert_true("found Concesión A", "Concesión A" in names)
assert_true("found Concesión B", "Concesión B" in names)
assert_false("not found C", "Concesión C" in names)

# Point query
point_results = rtree.search_point(-6.76, -79.83)
assert_true("point finds A", any(r.data["name"] == "Concesión A" for r in point_results))

# Nearest
nearest = rtree.nearest(-6.76, -79.83, k=2)
assert_eq("nearest count", len(nearest), 2)
assert_eq("nearest first", nearest[0][1].data["name"], "Concesión A")

# Empty query
empty = rtree.search(BoundingBox(100, 100, 200, 200))
assert_eq("empty search", len(empty), 0)

# Medical DICOM example
rtree2 = RSpatialIndex()
rtree2.insert(BoundingBox(100, 200, 150, 250), {"organ": "liver", "slice": 42})
rtree2.insert(BoundingBox(200, 300, 280, 380), {"organ": "kidney", "slice": 42})
results = rtree2.search(BoundingBox(90, 190, 160, 260))
assert_eq("medical search", len(results), 1)
assert_eq("found liver", results[0].data["organ"], "liver")

# Clear
rtree.clear()
assert_eq("cleared", rtree.size, 0)

# Overflow / split test (more than max_entries)
rtree3 = RSpatialIndex(max_entries=2)
for i in range(10):
    rtree3.insert(BoundingBox(i, i, i + 1, i + 1), {"id": i})
assert_eq("bulk insert size", rtree3.size, 10)
all_results = rtree3.search(BoundingBox(-1, -1, 20, 20))
assert_eq("bulk search all", len(all_results), 10)

print(f"  RSpatialIndex: {PASS - prev_pass} passed")

# =============================================================================
# RESUMEN
# =============================================================================
print(f"\n{'=' * 50}")
total = PASS + FAIL
print(f"  TOTAL: {PASS}/{total} passed, {FAIL} failed")
if FAIL == 0:
    print("  ✅ ALL TESTS PASSED")
else:
    print(f"  ❌ {FAIL} FAILURES")
print(f"{'=' * 50}")

sys.exit(0 if FAIL == 0 else 1)
