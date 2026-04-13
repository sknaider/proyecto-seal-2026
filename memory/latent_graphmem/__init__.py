"""LatentGraphMem V1 — learned subgraph retriever for SOUL.

Package structure:
- model.py : bi-encoder with LoRA on multilingual-e5-base
- data.py  : loader from latent_graphmem_training_pairs, query-level split
- loss.py  : InfoNCE contrastive loss
- eval.py  : recall@k metric over val pool
"""
