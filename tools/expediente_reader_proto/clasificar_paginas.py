#!/usr/bin/env python3
"""Etapa 1 del lector de expedientes (prototipo, JARVIS 2-sep-2026):
clasificar cada página OCR por TIPO de documento con reglas por palabras clave,
extraer campos clave (RUC, AWB, factura, guía, peso, placa, fecha) y medir contra
las 24 etiquetas manuales que hice leyendo el PDF de Henry."""
import re, glob, os, sys, json, unicodedata

D = os.path.dirname(os.path.abspath(__file__))
PREFIX = sys.argv[1] if len(sys.argv) > 1 else "hi"

# Verdad manual (leída página por página, 2-sep 14:35)
LABELS = {
 1: "instruccion_embarque", 2: "acta_compromiso", 3: "certificado_origen",
 4: "ddjj_procedencia", 5: "ddjj_origen", 6: "ddjj_guia_aerea", 7: "carta_responsabilidad",
 8: "ddjj_aduana_productor", 9: "ddjj_transporte", 10: "anexo5_uif", 11: "ddjj_aduana_lavado",
 12: "factura_comercial", 13: "factura_agenciamiento", 14: "factura_servicio", 15: "factura_servicio",
 16: "informe_ensayo", 17: "guia_remision_manual", 18: "orden_servicio", 19: "guia_recepcion",
 20: "guia_remision_electronica", 21: "guia_transportista", 22: "guia_recepcion",
 23: "guia_remision_electronica", 24: "guia_transportista",
}

def norm(s):
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.upper())

# Reglas: (tipo, [patrones que deben aparecer], peso)  — se elige el mayor puntaje
RULES = [
 ("instruccion_embarque",      [r"INSTRUCCI.N DE EMBARQUE"], 10),
 ("acta_compromiso",           [r"ACTA DE COMPROMISO"], 10),
 ("certificado_origen",        [r"CERTIFICATE OF ORIGIN"], 10),
 ("anexo5_uif",                [r"ANEXO N.? ?5", r"CONOCIMIENTO DEL CLIENTE"], 10),
 ("informe_ensayo",            [r"INFORME DE ENSAYO"], 10),
 ("orden_servicio",            [r"ORDEN DE SERVICIO"], 10),
 ("guia_recepcion",            [r"GUIA DE RECEPCION"], 10),
 ("guia_transportista",        [r"REMISI.N ELECTR.NICA", r"TRANSPORTISTA"], 9),
 ("guia_remision_electronica", [r"REMISI.N ELECTR.NICA", r"REMITENTE"], 8),
 ("guia_remision_manual",      [r"GUIA DE REMISION", r"REMITENTE"], 6),
 ("factura_comercial",         [r"FACTURA ELECTRONICA", r"BARRA DE ORO"], 9),
 ("factura_agenciamiento",     [r"FACTURA ELECTRONICA", r"AGENCIAMIENTO"], 9),
 ("factura_servicio",          [r"FACTURA ELECTR.NICA", r"SERVICIO"], 7),
 ("carta_responsabilidad",     [r"CARTA DE RESPONSABILIDAD"], 10),
 ("ddjj_transporte",           [r"JURADA DE TRANSPORTE"], 10),
 ("ddjj_procedencia",          [r"PROCEDENCIA DE MINERAL"], 10),
 ("ddjj_origen",               [r"ORIGEN DEL MINERAL"], 10),
 ("ddjj_aduana_productor",     [r"INTENDENCIA DE ADUANA", r"PRODUCTOR"], 8),
 ("ddjj_aduana_lavado",        [r"INTENDENCIA DE ADUANA", r"FACTURA"], 6),
 ("ddjj_guia_aerea",           [r"DECLARO BAJO JURAMENTO", r"GU.A A.REA"], 8),
]

FIELDS = {
 "ruc":     r"\b(1\d{10}|2\d{10})\b",
 "awb":     r"0?74\s*-?\s*7269\s*-?\s*0111",
 "factura": r"\b(E001|FF02)\s*-?\s*0*(\d{1,4})\b",
 "guia":    r"\b(EG0[37]|004|002|001)\s*-\s*0*(\d{1,8})\b",
 "peso":    r"\b5[.,]?993[.,]3\d?\b",
 "placa":   r"\b([A-Z]\d[A-Z]\s?-?\s?\d{3}|[A-Z]{3}\s?-?\s?\d{3}|Z8[IU]\s?-?\s?\d{3})\b",
 "fecha":   r"\b(\d{1,2})[/ ]?(de )?(0?8|AGOSTO|August)[/ ,]?(de )?2026\b",
 "kg_o_gr": r"\b(kg|KG|GR\.?|GRAMOS?|KILOGRAMOS?|gramos)\b",
}

def classify(text):
    t = norm(text)
    best, score = "desconocido", 0
    for tipo, pats, w in RULES:
        hits = sum(1 for p in pats if re.search(p, t))
        if hits == len(pats) and w > score:
            best, score = tipo, w
    return best, score

rows, ok = [], 0
for i in range(1, 25):
    path = f"{D}/{PREFIX}-{i:02d}.txt"
    text = open(path, encoding="utf-8", errors="replace").read() if os.path.exists(path) else ""
    tipo, score = classify(text)
    t = norm(text)
    f = {k: sorted(set(m if isinstance(m, str) else "".join(m) for m in re.findall(p, t)))[:4] for k, p in FIELDS.items()}
    hit = tipo == LABELS[i]; ok += hit
    rows.append({"pag": i, "esperado": LABELS[i], "predicho": tipo, "ok": hit, "campos": f})
    print(f"{i:02d} {'OK ' if hit else 'XX '} {LABELS[i]:26s} -> {tipo:26s} ruc={f['ruc']} awb={bool(f['awb'])} fac={f['factura']} guia={f['guia']} peso={bool(f['peso'])} unidad={f['kg_o_gr']}")
print(f"\nclasificación: {ok}/24 correctas")
json.dump(rows, open(f"{D}/resultado_{PREFIX}.json", "w"), ensure_ascii=False, indent=1)
