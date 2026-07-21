#!/usr/bin/env python3
"""gtl_fiscal.py — Lógica fiscal del editor de facturas GTL (ALICE 2026-05-29).

Mi parte del split (Henry aprobó 4 ideas): VALIDADOR "Cumple SUNAT" + RECÁLCULO fiscal.
JARVIS hace la UI (semáforo, binding, multi-tenant). Esta lógica es agnóstica al
frontend/backend: si es backend Python se importa directo; si es frontend JS, sirve
de spec exacta para portar (las fórmulas y reglas son las mismas).

Fuentes reales (no inventado):
- IGV 18% (tasa general Perú).
- Detracción: catálogo agents/ALICE/detraccion_codigos_porcentajes.json (códigos SUNAT).
- monto_en_letras: reusa tools/awb_automation/sunat_pdf.py (no se reimplementa).
"""
from __future__ import annotations
import json
import os
from decimal import Decimal, ROUND_HALF_UP

IGV_RATE = Decimal("0.18")  # IGV general Perú

_CAT_PATH = os.path.join(os.path.dirname(__file__), "detraccion_codigos_porcentajes.json")


def _load_detraccion() -> dict[str, dict]:
    """Carga el catálogo de detracción {codigo: {porcentaje, descripcion}} desde el JSON real."""
    with open(_CAT_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    items = data if isinstance(data, list) else data.get("codigos", data.get("items", []))
    return {str(it["codigo"]): it for it in items if isinstance(it, dict) and "codigo" in it}


DETRACCION = _load_detraccion()


def _money(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calcular_totales(base, moneda: str = "PEN", codigo_detraccion: str | None = None) -> dict:
    """Recalcula los totales fiscales a partir de la base imponible.

    Devuelve base, IGV 18%, total, y (si aplica) detracción + neto a pagar.
    El monto en letras se delega a sunat_pdf.monto_en_letras (fuente única).
    """
    base = _money(base)
    igv = _money(base * IGV_RATE)
    total = _money(base + igv)
    out = {
        "moneda": moneda,
        "base_imponible": float(base),
        "igv_18pct": float(igv),
        "total": float(total),
    }
    if codigo_detraccion:
        cod = str(codigo_detraccion)
        if cod not in DETRACCION:
            out["detraccion_error"] = f"Código de detracción '{cod}' no existe en el catálogo SUNAT"
        else:
            pct = Decimal(str(DETRACCION[cod]["porcentaje"])) / Decimal("100")
            monto_detr = _money(total * pct)
            out["detraccion"] = {
                "codigo": cod,
                "descripcion": DETRACCION[cod].get("descripcion", ""),
                "porcentaje": float(DETRACCION[cod]["porcentaje"]),
                "monto_detraccion": float(monto_detr),
                "neto_a_pagar": float(_money(total - monto_detr)),
            }
    try:
        from sys import path as _p
        _p.insert(0, "/home/dadito/IA/proyecto-seal/tools/awb_automation")
        from sunat_pdf import monto_en_letras  # fuente única, no se reimplementa
        out["monto_en_letras"] = monto_en_letras(total, "USD" if moneda == "USD" else "PEN")
    except Exception:
        out["monto_en_letras"] = None  # disponible solo si el módulo SUNAT está accesible
    return out


def calcular_desde_lineas(lineas: list[dict], moneda: str = "PEN", codigo_detraccion: str | None = None) -> dict:
    """Recálculo para TABLA DE LÍNEAS DINÁMICA (Henry 2026-05-29).

    Suma los importes de todas las líneas (importe = cantidad × valor_unitario si no viene)
    y aplica la fiscalidad sobre la base total. Al agregar/quitar líneas en el editor,
    se llama esto y los totales (base/IGV/total/detracción/monto en letras) se actualizan solos.
    """
    base = Decimal("0")
    detalle = []
    for ln in lineas:
        cant = Decimal(str(ln.get("cantidad", 1)))
        vu = Decimal(str(ln.get("valor_unitario", ln.get("precio_unitario", 0))))
        importe = _money(ln["importe"]) if ln.get("importe") not in (None, "") else _money(cant * vu)
        base += importe
        detalle.append({
            "descripcion": ln.get("descripcion", ""),
            "cantidad": float(cant),
            "valor_unitario": float(_money(vu)),
            "importe": float(importe),
        })
    out = calcular_totales(base, moneda, codigo_detraccion)
    out["lineas"] = detalle
    out["num_lineas"] = len(detalle)
    return out


# Campos obligatorios SUNAT (de los guardrails fiscales + sunat_ubl.py)
_OBLIGATORIOS = {
    "emisor_ruc": "RUC del emisor",
    "emisor_razon_social": "Razón social del emisor",
    "cliente_numero_doc": "Documento del cliente",
    "cliente_razon_social": "Razón social del cliente",
    "serie": "Serie (F001/B001)",
    "numero": "Número correlativo",
    "fecha_emision": "Fecha de emisión",
    "moneda": "Moneda",
}


def validar_sunat(factura: dict) -> list[str]:
    """Valida que la factura cumpla los mínimos SUNAT. Devuelve lista de errores (vacía = OK)."""
    errores = []
    for campo, label in _OBLIGATORIOS.items():
        v = factura.get(campo)
        if v is None or (isinstance(v, str) and not v.strip()):
            errores.append(f"Falta campo obligatorio: {label} ({campo})")
    ruc = str(factura.get("emisor_ruc", "")).strip()
    if ruc and (not ruc.isdigit() or len(ruc) != 11):
        errores.append(f"RUC emisor inválido (debe ser 11 dígitos): '{ruc}'")
    lineas = factura.get("lineas") or factura.get("items") or []
    if not lineas:
        errores.append("La factura debe tener al menos una línea/ítem")
    for i, ln in enumerate(lineas, 1):
        if not (ln.get("descripcion") or "").strip():
            errores.append(f"Línea {i}: falta descripción")
        if ln.get("cantidad") in (None, 0):
            errores.append(f"Línea {i}: cantidad inválida")
    cod = factura.get("detraccion_codigo")
    if cod and str(cod) not in DETRACCION:
        errores.append(f"Código de detracción '{cod}' no existe en catálogo SUNAT")
    return errores


def cumple_sunat(factura: dict) -> bool:
    """Semáforo verde/rojo para el editor: True = lista para emitir."""
    return len(validar_sunat(factura)) == 0


if __name__ == "__main__":
    # Demo / autotest
    print("=== calcular_totales(672.74, USD, detraccion 027) ===")
    print(json.dumps(calcular_totales(672.74, "USD", "027"), ensure_ascii=False, indent=2))
    print("\n=== validar_sunat (factura incompleta) ===")
    print(validar_sunat({"emisor_ruc": "123", "serie": "F001"}))
    print("\n=== validar_sunat (factura completa) ===")
    ok = {
        "emisor_ruc": "20610565451", "emisor_razon_social": "GTL CONSULTING S.A.C.S.",
        "cliente_numero_doc": "20612831565", "cliente_razon_social": "CLIENTE EJEMPLO S.A.C.",
        "serie": "F001", "numero": 123, "fecha_emision": "2026-05-29", "moneda": "USD",
        "items": [{"descripcion": "Servicio de transporte de carga aerea", "cantidad": 1}],
    }
    print("errores:", validar_sunat(ok), "| cumple_sunat:", cumple_sunat(ok))
