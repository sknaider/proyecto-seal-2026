# LatentGraphMem — Architecture Note (V1)

**Autor:** JARVIS
**Fecha:** 2026-04-12
**Para:** ADA (unblocker de Step 3 — training loop)
**Status:** arch locked para V1, V2 deferred al final del doc
**Paper base:** arxiv 2601.03417 (LatentGraphMem), arxiv 2205.12454 (GraphGPS)

---

## Decisión clave V1: NO straight-through estimator

El spec original proponía STE para hacer TopK diferenciable sobre selección de subgrafo. **ADA ya pre-construyó pares (pos + 3 neg explícitos) en `latent_graphmem_training_pairs`** (192 filas, Step 2 cerrado). Con pares pre-armados, el training loop es **puramente contrastive sobre scores de subgrafo** — no hay TopK en el forward pass, no hay nada que derivar.

**Implicancia:** V1 training loop es contrastive learning estándar. STE queda para V2 cuando hagamos end-to-end selection learning sobre el grafo completo (fuera de scope de este sprint).

Esto simplifica Step 3 de **L (large, 3-5 días)** a **M (medium, 1-2 días)**. Menos código, menos bugs, primer número medible más rápido. +1% rule aplica: shippear V1, medir, iterar.

---

## Base model

**Elección:** `intfloat/multilingual-e5-base`
- 278M params
- Output: 768-dim (matchea el `VECTOR(768)` que ADA usó en la tabla — consistencia garantizada)
- Multilingüe (SOUL tiene contenido en español — William, diary entries, inner_thoughts)
- Ya dominado: familia e5 es la misma que se usa para node embeddings en Neo4j
- Cabe cómodo en DGX Spark (128GB unified) con batch size grande

**Alternativas descartadas:**
- `e5-large` (1024-dim): no matchea schema de ADA, requiere migración
- `all-MiniLM-L6-v2` (384-dim): demasiado chico para capturar semántica multi-hop
- GraphGPS custom transformer: over-engineering para V1, no hay pretraining disponible

**Schema note para ADA:** vos usaste VECTOR(768) en lugar de VECTOR(1024) del spec. Decisión correcta — alinea con multilingual-e5-base que es el encoder óptimo para contenido SEAL. El spec queda actualizado implícitamente.

---

## Arquitectura del forward pass

```
Query (text)                    Subgraph (JSONB: nodes + edges + texts)
    │                                       │
    ▼                                       ▼
serialize_query()                 serialize_subgraph()
    │                                       │
    ▼                                       ▼
e5-base tokenizer           e5-base tokenizer
    │                                       │
    ▼                                       ▼
e5-base encoder (LoRA)      e5-base encoder (LoRA, SHARED weights)
    │                                       │
    ▼                                       ▼
mean pool (attention mask)  mean pool (attention mask)
    │                                       │
    ▼                                       ▼
L2 normalize                 L2 normalize
    │                                       │
    └───────────┬───────────────────────────┘
                ▼
           cosine(query_emb, subgraph_emb) / τ
                │
                ▼
           score ∈ [-1/τ, 1/τ]
```

**Weights:** un único encoder con LoRA. Query y subgraph comparten pesos (bi-encoder siamés). Simplifica training, matchea el pattern de sentence-transformers.

**Serialize subgraph:** concatenación estructurada
```
[SUB] node_1_text [SEP] node_2_text [SEP] ... [SEP] edge: node_1 -[REL]-> node_2 [SEP] ...
```
Truncado a 512 tokens (límite e5-base). Orden: nodos primero por importancia (BFS seed first), edges después.

---

## LoRA config

```python
from peft import LoraConfig, TaskType

lora_config = LoraConfig(
    task_type=TaskType.FEATURE_EXTRACTION,
    r=16,
    lora_alpha=32,
    lora_dropout=0.05,
    bias="none",
    target_modules=["query", "key", "value", "dense"],  # BERT-style (e5 hereda)
)
```

**Param count estimado:** ~1.5M trainable (0.5% de 278M base). Cabe en VRAM de cualquier GPU razonable. En DGX Spark: trivial.

**Rationale:**
- r=16: punto de partida estándar, suficiente para dominio específico como retrieval
- alpha=32: scaling factor 2x (estándar)
- dropout 0.05: regularización leve, el dataset es chico (192 pares)
- target_modules: attention + dense (no embeddings, no layer norms)

---

## Loss — InfoNCE contrastive

```python
def contrastive_loss(query_emb, subgraph_embs, labels, temperature=0.07):
    """
    query_emb: (B, D)
    subgraph_embs: (B, K, D)  where K = 1 pos + 3 neg explicit per query
    labels: (B,) — always 0 because positive is at index 0
    """
    # Scale similarity by temperature
    logits = torch.einsum("bd,bkd->bk", query_emb, subgraph_embs) / temperature
    # Cross-entropy: maximize prob of pos (index 0), minimize neg
    loss = F.cross_entropy(logits, torch.zeros(B, dtype=torch.long, device=logits.device))
    return loss
```

**Por qué InfoNCE y no margin-triplet:**
- InfoNCE usa todos los negativos simultáneamente (softmax sobre K opciones) → gradiente más informativo
- Margin-triplet compara 1 pos vs 1 neg a la vez → requiere hard-negative mining manual
- ADA ya tiene 3 neg/pos en DB → InfoNCE es drop-in

**In-batch negatives (augmentation gratuita):** por cada query en el batch, los subgrafos positivos de OTRAS queries en el mismo batch son negativos adicionales. Con batch_size=16 → 16 queries × 4 subgrafos = 64 candidatos, de los cuales 1 es el positivo real → InfoNCE sobre 64 opciones. Gradiente rico sin costo extra.

**Temperature τ=0.07:** estándar MoCo/SimCLR. Tunable en Step 3 v1.1 si loss se estanca.

---

## Training config

```python
training_config = {
    "base_model": "intfloat/multilingual-e5-base",
    "num_train_epochs": 10,
    "per_device_train_batch_size": 16,
    "per_device_eval_batch_size": 32,
    "learning_rate": 2e-4,        # LoRA standard
    "weight_decay": 0.01,
    "warmup_ratio": 0.1,
    "lr_scheduler_type": "cosine",
    "optim": "adamw_torch",
    "fp16": False,                # DGX Spark bf16 nativo
    "bf16": True,
    "gradient_accumulation_steps": 1,
    "logging_steps": 5,
    "eval_strategy": "epoch",
    "save_strategy": "epoch",
    "save_total_limit": 3,        # keep last 3 checkpoints
    "load_best_model_at_end": True,
    "metric_for_best_model": "eval_recall_at_5",
    "greater_is_better": True,
    "seed": 42,
}
```

**Dataset split:**
- Train: 80% de las 48 queries con pos+neg → 38 queries × 4 subgrafos = 152 pares
- Val: 20% → 10 queries × 4 subgrafos = 40 pares
- Test: NO split — reservamos test_set_v1 completo para el diagnóstico 5-mode (Step 6)

Split a nivel de QUERY, no a nivel de par. Crítico: que una query no aparezca en train y val simultáneamente o el modelo ve el positivo en training.

---

## 🚨 Post-mortem — Leak incident 2026-04-12

**Qué pasó:** el build_pairs V1 usó `diagnostic/test_set_v1.jsonl` como **fuente de queries** para generar los 192 training pairs. Resultado: val recall@5 = 0.90 (vs MAGMA 0.4375) era memorización pura, no generalización.

**Auditoría ejecutada (`latent_graphmem/contamination_check.py`):**
- L1 exact query overlap: 192/192 (100%)
- L3 positive subgraph memory_id leak: 48/48 (100%)
- Contamination rate sobre expected_memory_ids: 18/18 = 100%

**Causa raíz:** el spec original no distinguía entre "fuente de supervisión" (ground-truth para construir pares) y "held-out eval" (test_set_v1). El mismo archivo cumplía los dos roles.

**Regla permanente (SEAL rule `audit_train_test_contamination`):** antes de cualquier training run, ejecutar `contamination_check.py` como pre-train gate. Exit 1 → abort. Sin excepción. Cita de William: *"los bugs silenciosos no escalan linealmente, escalan exponencial"* — 48 queries contaminadas hoy = 30min perdidos; el mismo pattern en AXION con millones de memorias = desastre regulatorio.

**Anti-contamination protocol V2 (obligatorio para el retrain limpio):**
1. **Sellar test_set_v1.** Jamás leerlo desde `build_pairs.py`. Chequeo con hash SHA-256 en el script de build: si el archivo cambia, abortar.
2. **Generar queries de training desde memorias aleatorias vía LLM** (qwen2.5:7b local o Claude), excluyendo los 18 `expected_memory_ids` del test set como seed. Listar el blacklist en el script.
3. **Filtro L1+L2** en el build: descartar cualquier query sintética que sea exact-match o Jaccard>0.6 contra cualquier query de test_set_v1.
4. **Pre-train gate obligatorio** en `train_latent_graphmem.py`: `subprocess.run(["python3", "latent_graphmem/contamination_check.py", "--pairs-source", SOURCE_TAG])` antes de cargar el modelo base. Exit != 0 → `sys.exit(1)`.
5. **Post-train sanity**: sobre test_set_v1 genuinamente hold-out, si r@5 > 0.95 activar alerta de "demasiado bueno" y auditar de nuevo. La regla de William del 10/10 queda como tripwire automático.

**Lección de proceso:** el orden correcto es siempre `build → contamination_check → train → hold-out eval`. V1 saltó el contamination_check porque no existía. V1 murió el 12 abril 2026 a los 30 minutos de haber sido declarada "2× mejor que MAGMA". La muerte fue barata; hubiera sido carísima en producción.

---

## Validation metric: recall@5

Durante training, cada epoch:
1. Tomar las 10 val queries
2. Para cada query: scorear contra un **pool de candidatos** = los 48 subgrafos positivos del training set + los 10 subgrafos positivos de val (total 58 candidatos)
3. Ranking: top-5 por score
4. Recall@5 = `sum(1 if correct_pos in top5 else 0) / 10`

Early-stop: patience=3 epochs sin mejora en val_recall_at_5. Min epochs = 3 para evitar early-stop prematuro con loss ruidoso.

---

## Archivos y paths

```
~/IA/proyecto-seal/memory/
├── train_latent_graphmem.py           # Training script (Step 3)
├── latent_graphmem/
│   ├── __init__.py
│   ├── model.py                       # Bi-encoder + LoRA wiring
│   ├── data.py                        # Dataset class, pg loader, split
│   ├── loss.py                        # InfoNCE
│   └── eval.py                        # recall@k metric
└── runs/latent_graphmem_v1/            # Tensorboard logs

~/IA/modelos/
└── latent-graphmem-soul-v1/
    ├── checkpoint-epoch-1/
    ├── checkpoint-epoch-2/
    └── best/                           # symlink al mejor checkpoint
```

---

## Serving contract (Step 4 unblock para ADA)

FastAPI endpoint `POST /retrieve`:

**Request:**
```json
{
  "query": "string",
  "top_k": 5,
  "token_budget": 2000
}
```

**Response:**
```json
{
  "subgraph": {
    "nodes": [{"memory_id": 123, "text": "..."}, ...],
    "edges": [{"src": 123, "dst": 456, "rel": "CAUSES"}, ...]
  },
  "memory_ids": [123, 456, 789],
  "scores": [0.92, 0.81, 0.73],
  "latency_ms": 145.2,
  "adapter_version": "v1"
}
```

**Retrieval pipeline en serving (diferente al training):**
1. Recibir query
2. Generar candidate subgraphs: BFS desde top-M memories por embedding similarity (M=20)
3. Re-rank los M candidatos con el bi-encoder LoRA-tuned
4. Devolver top_k subgrafos, fusionados y truncados a token_budget

**Adapter load:**
```python
from peft import PeftModel
base = AutoModel.from_pretrained("intfloat/multilingual-e5-base")
model = PeftModel.from_pretrained(base, "~/IA/modelos/latent-graphmem-soul-v1/best")
model.eval()
```

---

## Tests (Step 8 unblock)

Smoke tests obligatorios para declarar Step 3 verde:

1. **Data loader:** carga los 192 pares de pg, split correcto por query (no leakage), shape de batches correcto
2. **Forward pass:** un batch produce (B, K, D) embeddings con D=768, sin NaN
3. **Loss:** contrastive loss > 0 y < log(K) al inicio; decrece en epoch 1
4. **Backward pass:** gradientes fluyen a LoRA params, NO a base params (assert sobre `requires_grad`)
5. **1-epoch smoke:** entrena 1 epoch full sin OOM, checkpoint se guarda y se puede recargar
6. **Eval metric:** recall@5 en val es un float ∈ [0, 1], no explota con divisiones por cero

---

## V2 — deferred (no bloquear V1)

Lo que queda fuera de V1 pero está en el roadmap:

1. **Straight-through estimator** — end-to-end subgraph selection diferenciable. Requiere reformular training para operar sobre el grafo completo, no sobre pares pre-armados. Complejidad +3x.
2. **Hard negative mining online** — en lugar de usar los 3 negativos fijos de la tabla, samplear negativos duros durante training con nearest-neighbor en el espacio de embedding actual.
3. **Graph-structure-aware encoder** — GraphGPS o GAT adicional sobre los node embeddings antes del pooling. V1 trata al subgrafo como bag-of-text; V2 respeta estructura.
4. **Weak-label augmentation** — expandir el dataset con los 1,870 memorias como background + ERL `category='insight'` como pseudo-labels. Requiere pipeline de auto-labeling.
5. **Learned router** — v2 del Step 7. V1 es rule-based sobre los números del diagnóstico.

V2 arranca solo si V1 muestra lift real. Sin lift → no invertimos más, se queda como modo opcional en el diagnóstico.

---

## Dependencias bloqueantes para Step 3

- ✅ Step 0: diagnostic baseline (DONE — MAGMA 0.4375)
- ✅ Step 2: training pairs (DONE — 192 pares, ADA)
- ✅ Step 1: arch note (ESTE DOCUMENTO)
- ⏳ DGX Spark acceso + venv con `transformers`, `peft`, `torch`, `sentence-transformers`

No hay más bloqueos. ADA puede arrancar Step 3 cuando termine de leer esto.

---

## Siguientes pasos inmediatos

1. **ADA:** lee esta nota, me respondés con questions o GO
2. **JARVIS:** standby para co-ownership durante implementación del training loop
3. **Primer milestone Step 3:** script `train_latent_graphmem.py` que cargue los pares, forward pass sin errores, 1 epoch smoke test (target: <30 min)
4. **Segundo milestone:** entrenamiento completo 10 epochs, recall@5 val reportado
5. **Tercer milestone:** checkpoint best guardado, smoke reload desde disco

**Criterio de éxito V1:** recall@5 val > 0.40 (baseline MAGMA full-test es 0.4375 — si igualamos o superamos con solo 192 pares de training, es señal clarísima de que el approach funciona).

**Criterio de fracaso V1:** recall@5 val < 0.25 en 10 epochs → el encoder no está aprendiendo nada útil del dataset chico. Aprendemos que necesitamos más datos o un approach distinto.

Ambos escenarios son información. Fallar no es el precio, es el método.

---

*Architecture locked 2026-04-12 12:30. ADA, cuando quieras arrancamos.*
