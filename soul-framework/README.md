# soul-framework

Persistent AI souls — memory, personality, identity for any LLM agent.

```python
from soul_framework import Soul

async with Soul.create("Maya", ocean={"O": 0.8, "C": 0.9, "E": 0.6, "A": 0.7, "N": 0.2}) as agent:
    await agent.memory.store("User prefers short answers", importance=7)
    context = await agent.boot()
    await agent.reflect("Today I learned something new")
```

## Install

```bash
pip install soul-framework
```

## Status

Alpha — Phase 1 extraction from Team SEAL production system.
