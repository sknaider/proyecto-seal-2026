"""
Self-Edit Generator — Genera datos de entrenamiento automáticos.
El modelo lee un contexto y genera self-edits en diferentes formatos.
"""
import random

FORMAT_PROMPTS = {
    "implications": (
        "Dado el siguiente texto médico, lista todas las implicaciones clínicas "
        "que se pueden derivar. Incluye diagnósticos diferenciales, posibles "
        "complicaciones y consideraciones terapéuticas.\n\n"
        "Texto: {context}\n\n"
        "Implicaciones clínicas:"
    ),
    "rewrite": (
        "Reescribe el siguiente contenido médico de forma que sea más fácil de "
        "asimilar y recordar. Reorganiza la información, añade conexiones causales "
        "y destaca los puntos clave.\n\n"
        "Texto original: {context}\n\n"
        "Reescritura:"
    ),
    "self-qa": (
        "Genera 5 preguntas de estudio y sus respuestas detalladas basándote en "
        "el siguiente texto médico. Las preguntas deben evaluar comprensión profunda, "
        "no solo memorización.\n\n"
        "Texto: {context}\n\n"
        "Preguntas y respuestas:"
    ),
    "clinical-summary": (
        "Genera un resumen clínico estructurado del siguiente texto médico. Incluye: "
        "1) Hallazgos principales, 2) Diagnóstico probable, 3) Plan de manejo, "
        "4) Puntos clave a recordar.\n\n"
        "Texto: {context}\n\n"
        "Resumen clínico:"
    ),
}


def build_context_from_qa(item: dict) -> str:
    """Build a context string from a QA dataset item."""
    title = item.get("title", "")
    question = item.get("question", "")
    answer = item.get("answer", "")
    options = item.get("options", "")

    parts = []
    if title:
        parts.append(f"Tema: {title}")
    if question:
        parts.append(f"Pregunta: {question}")
    if options:
        parts.append(f"Opciones:\n{options}")
    if answer:
        parts.append(f"Respuesta correcta: {answer}")

    return "\n".join(parts)


def generate_self_edit(model, processor, item: dict, fmt: str,
                       max_new_tokens: int = 512, temperature: float = 0.7) -> str:
    """Generate a self-edit for the given item using the specified format."""
    import torch

    context = build_context_from_qa(item)
    prompt_template = FORMAT_PROMPTS.get(fmt, FORMAT_PROMPTS["implications"])
    prompt = prompt_template.format(context=context)

    messages = [
        {"role": "system", "content": [{"type": "text", "text":
            "Eres un asistente médico experto que genera material de estudio. "
            "Responde siempre en español."}]},
        {"role": "user", "content": [{"type": "text", "text": prompt}]},
    ]

    inputs = processor.apply_chat_template(
        messages, add_generation_prompt=True, tokenize=True,
        return_dict=True, return_tensors="pt",
    ).to(model.device, dtype=torch.bfloat16)

    input_len = inputs["input_ids"].shape[-1]

    with torch.inference_mode():
        outputs = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            do_sample=True,
            top_p=0.9,
        )

    new_tokens = outputs[0][input_len:]
    return processor.decode(new_tokens, skip_special_tokens=True)


def generate_multi_format_self_edits(model, processor, item: dict,
                                      formats: list[str] = None,
                                      max_new_tokens: int = 512) -> list[dict]:
    """Generate self-edits in multiple formats for a single item."""
    if formats is None:
        formats = list(FORMAT_PROMPTS.keys())

    results = []
    for fmt in formats:
        text = generate_self_edit(model, processor, item, fmt, max_new_tokens)
        results.append({"format": fmt, "text": text, "item": item})

    return results
