# Proyecto SEAL — Instrucciones del Agente Autónomo

## Objetivo
Entrenar modelos médicos de forma autónoma usando el framework SEAL
(Self-Adapting Language Models) con protecciones de continual learning.

## Cómo ejecutar

```bash
cd ~/IA/proyecto-seal

# Entrenar MedGemma 27B con SEAL
python -m core.seal_engine --config models/medgemma_27b.yaml

# Con datos custom
python -m core.seal_engine --config models/medgemma_27b.yaml \
  --train_data data/medical_train.json \
  --eval_data data/medical_eval.json
```

## Monitoreo
- Logs en tiempo real: `tail -f results/*/seal_engine.log`
- Dashboard: `streamlit run dashboard.py`

## Estructura
- `core/seal_engine.py` — Motor principal SEAL
- `core/continual_utils.py` — ReplayBuffer, FisherEMA, ContinualLoRATrainer
- `core/self_edit_gen.py` — Generador de self-edits en 4 formatos
- `core/evaluator.py` — Evaluador local sin APIs externas
- `models/*.yaml` — Configuraciones por modelo
