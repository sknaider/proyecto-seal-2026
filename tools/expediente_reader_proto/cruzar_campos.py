#!/usr/bin/env python3
"""Etapa 2+3 (prototipo, JARVIS 2-sep-2026): extracción por esquema para 3 tipos de documento
y los primeros cruces con semáforo, sobre los OCR de la etapa 1 (hi-NN.txt) y su clasificación
(resultado_*.json). Cada valor lleva su EVIDENCIA: página y línea del OCR donde se leyó."""
import re, json, os, sys, unicodedata

OCR_DIR = sys.argv[1] if len(sys.argv) > 1 else "."
PREFIX  = sys.argv[2] if len(sys.argv) > 2 else "hi"
res = json.load(open(os.path.join(OCR_DIR, f"resultado_{PREFIX}.json")))

def norm(s):
    s = unicodedata.normalize("NFKD", s); s = "".join(c for c in s if not unicodedata.combining(c))
    return s.upper()

def lines(p):
    path = os.path.join(OCR_DIR, f"{PREFIX}-{p:02d}.txt")
    return [norm(l) for l in open(path, encoding="utf-8", errors="replace").read().splitlines()]

def find(p, pattern, group=1):
    """Primer match del patrón en la página p; devuelve (valor, evidencia).
    Los formularios escaneados parten etiqueta y valor en líneas distintas: se prueba la
    línea sola y, si no, la línea + la siguiente no vacía (etiqueta ↵ valor)."""
    ls = lines(p)
    for i, l in enumerate(ls, 1):
        m = re.search(pattern, l)
        if m:
            return m.group(group).strip(), f"pág {p} línea {i}: «{l.strip()[:70]}»"
        nxt = next((x for x in ls[i:i+3] if x.strip()), "")
        m = re.search(pattern, l + " " + nxt)
        if m:
            return m.group(group).strip(), f"pág {p} líneas {i}-{i+1}: «{l.strip()[:40]} ↵ {nxt.strip()[:40]}»"
    return None, f"pág {p}: no encontrado"

def num(s):
    if s is None: return None
    s = s.replace(" ", "").replace(",", "")
    try: return float(s)
    except ValueError: return None

NUM = r"(\d[\d.,]*\d)"   # general: 5549.80, 5,993.30, 772,698.65

# ── esquemas por tipo (qué campo, dónde buscarlo en ESE tipo) ─────────────────
SCHEMAS = {
 "instruccion_embarque": {
   "awb":          r"AWB\W*(0?74\s*-?\s*7269\s*-?\s*0111)",
   "peso":         r"(?:BULTOS|PESO TOTAL).*?(\d[\d.,]*\d)\s*(?:GR|G|KG)",
   "unidad":       r"(?:BULTOS|PESO TOTAL).*?\d\s*(GR|KG|KILO|GRAMO)",
   "consignatario":r"NOMBRES Y APELLIDOS\W*(TIME AHEAD[A-Z ]*|GURUDEV[A-Z ]*)",
   "comprador":    r"(GURUDEV INTERNATIONAL (?:DMCC|FZCO|LLC|LTD))",
   "placa":        r"PLACA\W*([A-Z0-9]{3,4}\s?-?\s?\d{3})",
 },
 "certificado_origen": {
   "peso":    rf"SHIPMENT OF\s*({NUM})\s*(?:KG|G|GR)",
   "unidad":  r"SHIPMENT OF\s*\d[\d.,]*\d\s*(KG|GR|G)\b",
   "awb":     r"MAWB\W*(0?74\s*-?\s*7269\s*-?\s*0111)",
   "factura": r"INVOICE NUMBER\W*(E001\s*-?\s*\d+)",
   "destino": r"SENT TO\s+([A-Z ]+?)\s+VIA",
 },
 "factura_comercial": {
   "factura":   r"\b(E001\s*-?\s*256)\b",
   "cantidad":  rf"^\s*({NUM})\s+GRAMO",
   "unitario":  rf"GRAMO.*?\s({NUM})\s+0[.,]00",
   "total":     rf"IMPORTE TOTAL\W*\$?\s*({NUM})",
   "peso_bruto":rf"PESO BRUTO EN\s*({NUM})",
   "ley":       rf"LEY APROX\.?\s*({NUM})\s*%",
   "comprador": r"(GURUDEV INTERNATIONAL (?:DMCC|FZCO|LLC|LTD))",
 },
}

pages_by_type = {}
for r in res: pages_by_type.setdefault(r["predicho"], []).append(r["pag"])

extracted = {}
for tipo, schema in SCHEMAS.items():
    for p in pages_by_type.get(tipo, []):
        doc = {}
        for campo, pat in schema.items():
            v, ev = find(p, pat)
            doc[campo] = {"valor": v, "evidencia": ev}
        extracted[f"{tipo}@p{p}"] = doc

# ── cruces con semáforo ───────────────────────────────────────────────────────
def get(key, campo): return extracted.get(key, {}).get(campo, {}).get("valor")
def evi(key, campo): return extracted.get(key, {}).get(campo, {}).get("evidencia")
IE, CO, FC = "instruccion_embarque@p1", "certificado_origen@p3", "factura_comercial@p12"
checks = []
def add(nombre, estado, detalle, evidencia): checks.append({"cruce": nombre, "estado": estado, "detalle": detalle, "evidencia": evidencia})

# 1. unidad del peso entre instrucción y certificado
u1, u3 = get(IE, "unidad"), get(CO, "unidad")
if u1 and u3:
    same = (u1.startswith("G") and u3.startswith("G")) or (u1.startswith("K") and u3.startswith("K"))
    add("unidad de peso instrucción vs certificado", "OK" if same else "ERROR", f"instrucción={u1} · certificado={u3}", [evi(IE,"unidad"), evi(CO,"unidad")])
else:
    add("unidad de peso instrucción vs certificado", "REVISAR", f"no leído: instrucción={u1} certificado={u3}", [evi(IE,"unidad"), evi(CO,"unidad")])
# 2. peso bruto × ley = fino facturado
pb, ley, cant = num(get(FC,"peso_bruto")), num(get(FC,"ley")), num(get(FC,"cantidad"))
if pb and ley and cant:
    fino = round(pb * ley / 100, 2)
    add("peso bruto × ley = cantidad facturada", "OK" if abs(fino - cant) <= 0.05 else "ERROR", f"{pb} g × {ley} % = {fino} g · facturado {cant} g", [evi(FC,"peso_bruto"), evi(FC,"ley"), evi(FC,"cantidad")])
else:
    add("peso bruto × ley = cantidad facturada", "REVISAR", f"no leído: bruto={pb} ley={ley} cantidad={cant}", [evi(FC,"peso_bruto"), evi(FC,"ley"), evi(FC,"cantidad")])
# 3. cantidad × unitario = total
unit, tot = num(get(FC,"unitario")), num(get(FC,"total"))
if cant and unit and tot:
    calc = round(cant * unit, 2)
    add("cantidad × precio unitario = importe total", "OK" if abs(calc - tot) <= 0.05 else "ERROR", f"{cant} × {unit} = {calc} · total {tot}", [evi(FC,"unitario"), evi(FC,"total")])
else:
    add("cantidad × precio unitario = importe total", "REVISAR", f"no leído: cantidad={cant} unitario={unit} total={tot}", [evi(FC,"unitario"), evi(FC,"total")])
# 4. AWB igual en instrucción y certificado
a1, a3 = get(IE,"awb"), get(CO,"awb")
add("AWB instrucción vs certificado", "OK" if a1 and a3 and re.sub(r"\D","",a1)==re.sub(r"\D","",a3) else ("REVISAR" if not (a1 and a3) else "ERROR"), f"{a1} vs {a3}", [evi(IE,"awb"), evi(CO,"awb")])
# 5. peso bruto igual en instrucción, certificado y factura (valor, aparte de la unidad)
p1, p3 = num(get(IE,"peso")), num(get(CO,"peso"))
vals = [v for v in (p1, p3, pb) if v]
add("peso bruto: mismo número en instrucción / certificado / factura", "OK" if len(vals)==3 and max(vals)-min(vals) <= 0.05 else ("REVISAR" if len(vals)<3 else "ERROR"), f"instrucción={p1} certificado={p3} factura={pb}", [evi(IE,"peso"), evi(CO,"peso"), evi(FC,"peso_bruto")])

# 6. comprador: misma razón social en instrucción y factura
c1, c12 = get(IE,"comprador"), get(FC,"comprador")
if c1 and c12:
    add("comprador: misma razón social en instrucción y factura", "OK" if c1 == c12 else "ERROR", f"instrucción=«{c1}» · factura=«{c12}»" + ("" if c1 == c12 else " (mismo grupo, entidad legal distinta)"), [evi(IE,"comprador"), evi(FC,"comprador")])
else:
    add("comprador: misma razón social en instrucción y factura", "REVISAR", f"no leído: instrucción={c1} factura={c12}", [evi(IE,"comprador"), evi(FC,"comprador")])

print("== CAMPOS ==")
for k, d in extracted.items():
    print(k); [print(f"   {c:13s} = {v['valor']!s:30s}  ← {v['evidencia']}") for c, v in d.items()]
print("\n== CRUCES ==")
for c in checks:
    print(f"{c['estado']:8s} {c['cruce']}: {c['detalle']}")
    for e in c["evidencia"]: print(f"           {e}")
json.dump({"campos": extracted, "cruces": checks}, open(os.path.join(OCR_DIR, f"cruces_{PREFIX}.json"), "w"), ensure_ascii=False, indent=1)
