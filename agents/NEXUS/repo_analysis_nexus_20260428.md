# Análisis de Repos JEPA — NEXUS (nivel de código)
**Fecha:** 2026-04-28 | Repos analizados: BiJEPA, V-JEPA 2, I-JEPA

---

## 1. BiJEPA — github.com/YongchaoHuang/BiJEPA

### Qué es en realidad
Un **Jupyter notebook exportado a Python** (no una librería). Es investigación independiente de Yongchao Huang (U. Aberdeen), no de Meta FAIR.
Tiene 3 secciones secuenciales que demuestran experimentalmente el problema y la solución.

### Arquitectura del código
```python
class BiJEPA(nn.Module):
    online_encoder: Encoder(input_len, 64, embed_dim)  # procesa x E y
    fwd_predictor: Predictor(embed_dim, 64)  # x → ŷ
    bwd_predictor: Predictor(embed_dim, 64)  # y → x̂
    target_encoder: deepcopy(online_encoder)  # EMA, sin gradientes

def forward(x_raw, y_raw):
    target_x = target_encoder(x)  # stopgrad
    target_y = target_encoder(y)  # stopgrad
    pred_y = fwd_predictor(online_encoder(x))
    pred_x = bwd_predictor(online_encoder(y))
    loss = MSE(pred_y, target_y) + MSE(pred_x, target_x)
```

### El experimento de 3 casos (código lo demuestra)

**Caso 1 (línea 394-590):** BiJEPA SIN LayerNorm, SIN Weight Decay
```python
# encoder sin LayerNorm (línea 402-413):
# nn.LayerNorm(hidden_dim),  <-- REMOVED
optimizer = optim.AdamW(..., weight_decay=0.0)  # <-- sin decay
```
**Resultado:** Representation Explosion. Loss diverge. Inútil.

**Caso 2 (línea 592-850):** BiJEPA CON LayerNorm + Weight Decay (sin L2 norm en output)
```python
# encoder CON LayerNorm (línea 660-673):
nn.LayerNorm(hidden_dim),  # <-- de vuelta
optimizer = optim.AdamW(..., weight_decay=1e-4)  # <-- decay activo
# PERO: sin F.normalize(out, dim=-1)
```
**Resultado:** Estable. La combinación LayerNorm + Weight Decay evita la explosión sin forzar unit sphere.

**Caso 3 (línea 852+):** BiJEPA con L2 normalization en output (unit sphere)
```python
return F.normalize(out, dim=-1)  # output en unit sphere
```
**Resultado:** Más restrictivo. Bueno para retrieval. Peor para expresividad.

### Conclusión código-nivel
El fix de BiJEPA son exactamente 2 elementos:
1. `nn.LayerNorm` en capas ocultas del encoder
2. `weight_decay > 0` en AdamW (el autor usa 1e-4)

---

## 2. I-JEPA — github.com/facebookresearch/ijepa

### Estructura del proyecto
```
ijepa/
├── main.py / main_distributed.py    # entry point (SLURM-aware)
├── src/
│   ├── train.py                      # training loop
│   ├── models/vision_transformer.py  # ViT encoder
│   ├── masks/multiblock.py           # multi-block masking
│   ├── masks/default.py              # random masking
│   └── helper.py                     # init_model, load_checkpoint
└── configs/
    ├── in1k_vith14_ep300.yaml
    └── in22k_vith14_ep66.yaml
```

### Arquitectura del código (train.py)
```python
# Masking config (YAML):
num_enc_masks: 1        # 1 context block
num_pred_masks: 4       # 4 target blocks
enc_mask_scale: (0.85, 1.0)   # context = 85-100% del imagen
pred_mask_scale: (0.15, 0.2)  # cada target = 15-20%

# EMA (momentum scheduler):
ema = [0.996, 1.0]  # aumenta de 0.996 a 1.0 durante entrenamiento
# esto hace que el target encoder sea cada vez más estable
```

### Checkpoints disponibles AHORA (sin entrenar)
```
ViT-H/14 IN1K:   https://dl.fbaipublicfiles.com/ijepa/IN1K-vit.h.14-300e.pth.tar
ViT-H/16 IN1K:   https://dl.fbaipublicfiles.com/ijepa/IN1K-vit.h.16-448px-300e.pth.tar
ViT-H/14 IN22K:  https://dl.fbaipublicfiles.com/ijepa/IN22K-vit.h.14-900e.pth.tar
```

### Cómo cargar I-JEPA preentrenado (código real)
```python
from src.helper import init_model, load_checkpoint
encoder, predictor = init_model(device, patch_size=14, model_name='vit_huge',
                                 pred_depth=12, pred_emb_dim=384, ...)
load_checkpoint(device, r_file='IN1K-vit.h.14-300e.pth.tar', 
                encoder=encoder, predictor=predictor, opt=None, scaler=None)
encoder.eval()  # freeze encoder, usar como feature extractor
```

---

## 3. V-JEPA 2 — github.com/facebookresearch/vjepa2

### SORPRESA: V-JEPA 2.1 también está en este repo (2026-03-16)
V-JEPA 2.1 añade:
1. **Dense Predictive Loss**: TODOS los tokens contribuyen a la pérdida (no solo enmascarados)
2. **Deep Self-Supervision**: pérdida en múltiples capas intermedias del encoder
3. **Multi-Modal Tokenizers**: imagen + video en el mismo modelo
4. Familia: ViT-B/384, ViT-L/384, ViT-g/384, ViT-G/384 (nuevo gigantic)

### Estructura del código
```
vjepa2/
├── app/
│   ├── main_distributed.py         # training (SLURM)
│   └── vjepa_2_1/models/           # modelos 2.1
├── src/
│   ├── models/
│   │   ├── vision_transformer.py   # ViT encoder (3D-RoPE)
│   │   ├── predictor.py            # VisionTransformerPredictor (sin action)
│   │   ├── ac_predictor.py         # VisionTransformerPredictorAC (con action)
│   │   └── utils/
│   │       ├── pos_embs.py         # 3D sincos embeddings
│   │       └── modules.py          # Block, ACBlock, attention masks
├── evals/                          # evaluation scripts
│   ├── action_anticipation_frozen/ # Epic-Kitchens eval
│   ├── image_classification_frozen/
│   └── video_classification_frozen/
├── notebooks/
│   └── vjepa2_demo.py              # demo de uso
└── hubconf.py                      # torch.hub.load() entry
```

### ac_predictor.py — el corazón del World Model

El `VisionTransformerPredictorAC` (300M params) es donde ocurre la magia:

```python
# Dimensiones clave (línea 44):
action_embed_dim=7  # 7-DOF: [x,y,z, roll,pitch,yaw, gripper]

# Forward pass (línea 136-190):
def forward(self, x, actions, states, extrinsics=None):
    x = self.predictor_embed(x)          # video tokens → 1024-dim
    s = self.state_encoder(states)        # robot state → 1024-dim
    a = self.action_encoder(actions)      # action → 1024-dim
    
    # Interleave: [action_token, state_token, frame_patches...]
    x = torch.cat([a, s, x], dim=2).flatten(1, 2)
    # Shape: [B, T*(H*W+2), 1024]
    
    # Block-causal attention: timestep t SOLO ve t, t-1, t-2, ...
    attn_mask = build_action_block_causal_attention_mask(grid_depth, grid_height, grid_width)
    
    for blk in self.predictor_blocks:
        x = blk(x, attn_mask=attn_mask, ...)
    
    # Strip action tokens, proyectar de vuelta a encoder dim
    x = x[:, :, cond_tokens:, :].flatten(1, 2)  # solo frame tokens
    return self.predictor_proj(self.predictor_norm(x))
```

### Modelos disponibles para descarga (hubconf.py)

```python
import torch
# V-JEPA 2 (action-free encoder)
model = torch.hub.load('facebookresearch/vjepa2', 'vjepa2_vit_giant')
model = torch.hub.load('facebookresearch/vjepa2', 'vjepa2_vit_large')

# V-JEPA 2-AC (action-conditioned world model)
model = torch.hub.load('facebookresearch/vjepa2', 'vjepa2_ac_vit_giant')

# V-JEPA 2.1 (más reciente, 2026-03-16)
model = torch.hub.load('facebookresearch/vjepa2', 'vjepa2_1_vit_giant_384')
model = torch.hub.load('facebookresearch/vjepa2', 'vjepa2_1_vit_gigantic_384')
```

### Planning loop (cómo usar V-JEPA 2-AC para planificar)
```python
# Dado: estado actual z_k, meta z_g, estado robot s_k
# Optimizar: secuencia de acciones a* que minimiza ||P(a, s_k, z_k) - z_g||_1

def plan_actions(encoder, ac_predictor, current_frame, goal_frame, horizon=10):
    z_k = encoder(current_frame)
    z_g = encoder(goal_frame)
    
    # Inicializar acciones aleatorias, optimizar por gradiente
    actions = torch.zeros(1, horizon, 7, requires_grad=True)
    optimizer = torch.optim.Adam([actions], lr=0.01)
    
    for _ in range(100):  # iterations
        z_pred = ac_predictor(z_k, actions, states)
        energy = F.l1_loss(z_pred[:, -1], z_g)  # distancia al meta
        optimizer.zero_grad()
        energy.backward()
        optimizer.step()
    
    return actions.detach()[0, 0]  # ejecutar primera acción
```

---

## HALLAZGOS CRÍTICOS DEL CÓDIGO

### 1. BiJEPA es investigación académica (no producción)
Jupyter notebook exportado, no librería. Para SOUL hay que reimplementar el patrón, no importar el repo.
El patrón es simple: añadir `fwd_predictor` + `bwd_predictor` + LayerNorm + weight_decay.

### 2. I-JEPA tiene checkpoints listos para usar HOY
ViT-H/14 en IN1K disponible para descarga. Puede usarse como encoder general de imágenes sin entrenar nada.
Ideal para Medical AI (imágenes DICOM, radiografías).

### 3. V-JEPA 2.1 está en el mismo repo (nuevo, 2026-03-16)
Dense loss + deep supervision + multimodal = mejores representaciones.
ViT-Gigantic (>1B params) disponible.

### 4. V-JEPA 2-AC es el World Model listo para descargar
```python
torch.hub.load('facebookresearch/vjepa2', 'vjepa2_ac_vit_giant')
```
Tiene el planning loop implementable con ~30 líneas de PyTorch.

### 5. Adaptación a SEAL — el pattern V-JEPA 2-AC generaliza
Action-dim = 7 (7-DOF robot). Para SEAL:
- "action" = vector de dimensión K que representa la decisión del agente
- "state" = embedding del estado actual del sistema SEAL
- "video frames" → "conversation context embeddings"
- Planning loop: dado objetivo (goal), encontrar secuencia de acciones que minimiza distancia al objetivo en latente

---

## CÓDIGO PARA SOUL — IMPLEMENTACIÓN INMEDIATA

### Fix LayerNorm para embeddings SOUL (1 línea)
Aplicar en `memory/mcp_server_v2.py` antes de upsert a Qdrant:
```python
import torch.nn.functional as F
# Antes de insertar embedding:
embedding = F.layer_norm(torch.tensor(embedding), [len(embedding)]).tolist()
```

### BiJEPA pattern para memory retrieval mejorado
```python
class SOULMemoryEncoder(nn.Module):
    """Encoder con BiJEPA stability: LayerNorm + no L2 norm en output"""
    def __init__(self, input_dim, embed_dim=768):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, 1024),
            nn.LayerNorm(1024),    # fix representation explosion
            nn.GELU(),
            nn.Linear(1024, 1024),
            nn.LayerNorm(1024),    # fix representation explosion
            nn.GELU(),
            nn.Linear(1024, embed_dim)
        )
        # EMA target encoder para predicción
        self.target = copy.deepcopy(self.net)
    
    def forward(self, x):
        return self.net(x)  # NO F.normalize — permite expresividad
```

---
*Análisis código completado: 2026-04-28 | NEXUS | 3/3 repos analizados a profundidad*
