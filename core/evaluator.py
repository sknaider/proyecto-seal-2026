"""
Evaluador local para Proyecto SEAL.
Evalúa accuracy del modelo sin depender de APIs externas.
"""
import re
import torch


def evaluate_qa(model, processor, items: list[dict],
                max_eval: int = 20, max_tokens: int = 128) -> dict:
    """Evaluate model on QA items. Returns answer_rate and avg_length."""
    model.eval()
    results = {"correct": 0, "total": 0, "avg_length": 0, "responses": []}
    total_len = 0

    for item in items[:max_eval]:
        question = item["question"][:500]
        prompt = (
            f"Responde la siguiente pregunta médica de forma concisa y precisa.\n\n"
            f"Pregunta: {question}\n"
            f"Respuesta:"
        )

        messages = [
            {"role": "system", "content": [{"type": "text", "text":
                "Eres un asistente médico experto. Responde en español."}]},
            {"role": "user", "content": [{"type": "text", "text": prompt}]},
        ]

        inputs = processor.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=True,
            return_dict=True, return_tensors="pt",
        ).to(model.device, dtype=torch.bfloat16)

        input_len = inputs["input_ids"].shape[-1]

        with torch.inference_mode():
            out = model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                temperature=0.3,
                do_sample=True,
            )

        response = processor.decode(out[0][input_len:], skip_special_tokens=True)
        response = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()

        total_len += len(response)
        results["total"] += 1

        refusal_markers = ["no puedo", "lo siento", "i cannot", "i can't", "disclaimer"]
        is_refusal = any(m in response.lower() for m in refusal_markers)
        if len(response) > 30 and not is_refusal:
            results["correct"] += 1

        results["responses"].append({
            "question": question[:100],
            "response": response[:200],
            "accepted": not is_refusal and len(response) > 30,
        })

    results["avg_length"] = total_len / max(results["total"], 1)
    results["answer_rate"] = results["correct"] / max(results["total"], 1)
    model.train()
    return results


def compute_f1(prediction: str, reference: str) -> float:
    """Compute token-level F1 between prediction and reference."""
    pred_tokens = set(prediction.lower().split())
    ref_tokens = set(reference.lower().split())

    if not pred_tokens or not ref_tokens:
        return 0.0

    common = pred_tokens & ref_tokens
    if not common:
        return 0.0

    precision = len(common) / len(pred_tokens)
    recall = len(common) / len(ref_tokens)
    return 2 * precision * recall / (precision + recall)
