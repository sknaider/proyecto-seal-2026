#!/usr/bin/env python3
"""
prepare_eval_dataset.py — Prepara dataset de evaluación para ronda 3.
Lee medical_eval.json (125 items) y genera eval_ronda3.json con:
- id, pregunta, respuesta_esperada, categoría, idioma, dificultad, keywords
"""
import json
import re
import os

INPUT_PATH = "data/medical_eval.json"
OUTPUT_PATH = "data/eval_ronda3.json"


CATEGORY_MAP = {
    "PEDIATR": "pediatría",
    "CARDIOL": "cardiología",
    "NEUMOL": "neumología",
    "NEUROL": "neurología",
    "ENDOCRIN": "endocrinología",
    "GASTRO": "gastroenterología",
    "FARMACOL": "farmacología",
    "INFECT": "infectología",
    "URGENCI": "emergencias",
    "EMERG": "emergencias",
    "CIRUGÍ": "cirugía",
    "CIRUGI": "cirugía",
    "GINECOL": "ginecología",
    "OBSTET": "obstetricia",
    "HEMATOL": "hematología",
    "ONCOL": "oncología",
    "NEFROL": "nefrología",
    "REUMATOL": "reumatología",
    "DERMATOL": "dermatología",
    "OFTALMOL": "oftalmología",
    "TRAUMATOL": "traumatología",
    "PSIQUIATR": "psiquiatría",
    "MIR": "medicina_interna",
    "MEDICINA INTERNA": "medicina_interna",
    "DIAGNÓSTICO": "diagnóstico",
    "DIAGNOSTICO": "diagnóstico",
    "TRATAMIENTO": "tratamiento",
    "FARMACO": "farmacología",
    "GENERAL": "medicina_general",
}

SPANISH_INDICATORS = [
    "¿", "qué", "cuál", "cuáles", "cómo", "por qué", "diagnóstico", "tratamiento",
    "paciente", "síntoma", "dolor", "fiebre", "niño", "adulto", "edad", "años",
    "presenta", "refiere", "consulta", "manejo", "primera línea", "mecanismo",
]

ENGLISH_INDICATORS = [
    "what", "which", "how", "why", "patient", "treatment", "diagnosis",
    "symptoms", "management", "first line", "mechanism", "describe", "explain",
]


def detect_language(text: str) -> str:
    text_lower = text.lower()
    es_score = sum(1 for w in SPANISH_INDICATORS if w in text_lower)
    en_score = sum(1 for w in ENGLISH_INDICATORS if w in text_lower)
    return "es" if es_score >= en_score else "en"


def detect_category(title: str) -> str:
    title_upper = title.upper()
    for key, cat in CATEGORY_MAP.items():
        if key in title_upper:
            return cat
    return "medicina_general"


def estimate_difficulty(question: str, answer: str) -> int:
    q_len = len(question)
    a_len = len(answer)
    # Clinical case (detailed scenario) → hard
    clinical_markers = ["presenta", "refiere", "antecedentes", "años de edad",
                        "años con", "paciente de", "history of", "year-old"]
    is_clinical = any(m in question.lower() for m in clinical_markers)
    if is_clinical and q_len > 200:
        return 3  # hard
    elif q_len > 100 or a_len > 300:
        return 2  # medium
    else:
        return 1  # easy


def extract_keywords(answer: str, n: int = 6) -> list[str]:
    # Extract capitalized medical terms and key nouns
    words = re.findall(r'\b[A-Za-záéíóúÁÉÍÓÚñÑ]{4,}\b', answer)
    # Frequency count, skip very common words
    stopwords = {"para", "como", "este", "esta", "tiene", "puede", "debe",
                 "también", "entre", "desde", "hasta", "sobre", "durante",
                 "cuando", "siendo", "siendo", "with", "that", "this", "from",
                 "have", "which", "been", "more", "than", "their", "also"}
    freq: dict[str, int] = {}
    for w in words:
        wl = w.lower()
        if wl not in stopwords and len(wl) > 4:
            freq[wl] = freq.get(wl, 0) + 1
    sorted_words = sorted(freq, key=lambda x: -freq[x])
    return sorted_words[:n]


def main():
    with open(INPUT_PATH, encoding="utf-8") as f:
        raw = json.load(f)

    print(f"Loaded {len(raw)} items from {INPUT_PATH}")

    output = []
    for i, item in enumerate(raw, start=1):
        question = item.get("question", "")
        answer = item.get("answer", "")
        title = item.get("title", "")
        source = item.get("source", "")

        lang = detect_language(question)
        category = detect_category(title)
        difficulty = estimate_difficulty(question, answer)
        keywords = extract_keywords(answer)

        output.append({
            "id": i,
            "title": title,
            "pregunta": question,
            "respuesta_esperada": answer,
            "categoria": category,
            "idioma": lang,
            "dificultad": difficulty,
            "keywords": keywords,
            "source": source,
        })

    # Stats
    lang_counts = {}
    cat_counts = {}
    diff_counts = {}
    for item in output:
        lang_counts[item["idioma"]] = lang_counts.get(item["idioma"], 0) + 1
        cat_counts[item["categoria"]] = cat_counts.get(item["categoria"], 0) + 1
        diff_counts[item["dificultad"]] = diff_counts.get(item["dificultad"], 0) + 1

    print(f"\nLanguage distribution: {lang_counts}")
    print(f"Difficulty distribution: {diff_counts}")
    print("Top categories:", sorted(cat_counts.items(), key=lambda x: -x[1])[:8])

    os.makedirs(os.path.dirname(OUTPUT_PATH) if os.path.dirname(OUTPUT_PATH) else ".", exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\nSaved {len(output)} items to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
