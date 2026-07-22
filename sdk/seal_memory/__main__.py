"""
python -m seal_memory — SEAL Memory CLI

Usage:
    python -m seal_memory status
    python -m seal_memory search <agent_id> <query>
    python -m seal_memory list <agent_id>
    python -m seal_memory boot <agent_id>
    python -m seal_memory summary <agent_id>
    python -m seal_memory store <agent_id> <content>
"""

import argparse
import json
import os
import sys

from .client import SealMemory
from .exceptions import SealMemoryError


def _client(args) -> SealMemory:
    return SealMemory(
        api_key=args.api_key or os.environ.get("SEAL_API_KEY", "soul_demo_key"),
        base_url=args.base_url or os.environ.get("SEAL_BASE_URL", "http://localhost:8767"),
    )


def cmd_status(args):
    c = _client(args)
    try:
        from urllib.request import urlopen
        r = urlopen(f"{c.base_url}/v1/health", timeout=5)
        data = json.loads(r.read())
        print(f"✅ SEAL Memory is running at {c.base_url}")
        print(f"   Version: {data.get('version', 'unknown')}")
        print(f"   Status:  {data.get('status', 'unknown')}")
    except Exception as e:
        print(f"❌ Cannot reach {c.base_url}: {e}")
        sys.exit(1)


def cmd_search(args):
    c = _client(args)
    try:
        results = c.search(args.agent_id, args.query, limit=args.limit, expand=args.expand)
        if not results:
            print("No memories found.")
            return
        for m in results:
            stars = "★" * min(m.get("importance", 5), 10)
            print(f"[{m['id']}] {stars} {m['content']}")
    except SealMemoryError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_list(args):
    c = _client(args)
    try:
        memories = c.get_all(args.agent_id, limit=args.limit)
        print(f"Agent: {args.agent_id} — {len(memories)} memories")
        print("-" * 60)
        for m in memories:
            print(f"[{m['id']}] [{m.get('memory_type','?')}] [{m.get('importance',5)}★] {m['content'][:80]}")
    except SealMemoryError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_boot(args):
    c = _client(args)
    try:
        soul = c.boot(args.agent_id)
        ocean = soul.get("ocean", {})
        print(f"Agent: {args.agent_id}")
        print(f"OCEAN: O={ocean.get('O',0.5):.3f} C={ocean.get('C',0.5):.3f} "
              f"E={ocean.get('E',0.5):.3f} A={ocean.get('A',0.5):.3f} N={ocean.get('N',0.5):.3f}")
        emotions = soul.get("emotional_state", {})
        if emotions:
            top = sorted(emotions.items(), key=lambda x: x[1], reverse=True)[:3]
            print(f"Emotions: {', '.join(f'{k}={v:.2f}' for k,v in top)}")
        print(f"Memories: {soul.get('memory_count', '?')}")
    except SealMemoryError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_summary(args):
    c = _client(args)
    try:
        result = c.summary(args.agent_id)
        print(f"Agent: {args.agent_id} ({result.get('memory_count', '?')} memories)")
        print("-" * 60)
        print(result.get("summary", "No summary available."))
    except SealMemoryError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_store(args):
    c = _client(args)
    try:
        result = c.store(args.agent_id, args.content,
                         memory_type=args.memory_type, importance=args.importance,
                         category=args.category)
        mid = result.get("memory_id") or result.get("id", "?")
        dedup = " (reinforced existing)" if result.get("deduplicated") else ""
        print(f"✅ Stored memory #{mid}{dedup}")
    except SealMemoryError as e:
        print(f"Error: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        prog="seal-memory",
        description="SEAL Memory CLI — persistent memory for AI agents",
    )
    parser.add_argument("--api-key", help="API key (or set SEAL_API_KEY env var)")
    parser.add_argument("--base-url", default="http://localhost:8767",
                        help="Server URL (or set SEAL_BASE_URL env var)")

    sub = parser.add_subparsers(dest="command", required=True)

    # status
    sub.add_parser("status", help="Check if SEAL Memory server is running")

    # search
    p_search = sub.add_parser("search", help="Search agent memories")
    p_search.add_argument("agent_id")
    p_search.add_argument("query")
    p_search.add_argument("--limit", type=int, default=10)
    p_search.add_argument("--expand", action="store_true", help="LLM query expansion")

    # list
    p_list = sub.add_parser("list", help="List all agent memories")
    p_list.add_argument("agent_id")
    p_list.add_argument("--limit", type=int, default=50)

    # boot
    p_boot = sub.add_parser("boot", help="Show agent soul (OCEAN + emotions)")
    p_boot.add_argument("agent_id")

    # summary
    p_summ = sub.add_parser("summary", help="Narrative summary of agent memories")
    p_summ.add_argument("agent_id")

    # store
    p_store = sub.add_parser("store", help="Store a memory directly")
    p_store.add_argument("agent_id")
    p_store.add_argument("content")
    p_store.add_argument("--memory-type", default="semantic")
    p_store.add_argument("--importance", type=int, default=5)
    p_store.add_argument("--category", default="fact",
                         choices=["fact","preference","decision","insight","correction",
                                  "milestone","pattern","emotion","trust","humor","dynamic","full_exchange"])

    args = parser.parse_args()

    commands = {
        "status": cmd_status,
        "search": cmd_search,
        "list": cmd_list,
        "boot": cmd_boot,
        "summary": cmd_summary,
        "store": cmd_store,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
