#!/usr/bin/env python3
"""
sunat_pdf_fiel.py — Representación impresa FIEL al facturador SUNAT (con detracción + crédito).
Layout calcado de la preliminar del facturador (Henry 2026-06-03). Bloques de detracción (SPOT)
y de crédito/cuotas son CONDICIONALES: aparecen solo cuando hay datos.

Se prueba aislado (este módulo, vía __main__) antes de integrarlo a sunat_pdf.py / la emisión real.
"""
from __future__ import annotations

import io
from decimal import Decimal
from pathlib import Path
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

import sys
sys.path.insert(0, str(Path(__file__).parent))
from sunat_ubl import Factura
from sunat_pdf import _unidad_nombre, monto_en_letras  # reutiliza helpers existentes

# Paleta sobria, fiel al facturador (gris azulado para encabezados de tabla)
_NEGRO   = colors.black
_BLANCO  = colors.white
_AZUL    = colors.HexColor("#1F3864")
_GRISH   = colors.HexColor("#44546A")   # header tabla ítems (gris azulado oscuro)
_GRISL   = colors.HexColor("#F2F2F2")
_BORDE   = colors.HexColor("#BFBFBF")
_TOTBG   = colors.HexColor("#F2F2F2")


def _simbolo(moneda: str) -> str:
    return "$" if moneda == "USD" else "S/"


def _moneda_nombre(moneda: str) -> str:
    return "DOLAR AMERICANO" if moneda == "USD" else "SOLES"


def _st():
    return {
        "tit":   ParagraphStyle("tit", fontSize=11, fontName="Helvetica-Bold", alignment=TA_CENTER),
        "emi_b": ParagraphStyle("emi_b", fontSize=10, fontName="Helvetica-Bold", textColor=_NEGRO, leading=12),
        "emi":   ParagraphStyle("emi", fontSize=8.5, fontName="Helvetica", textColor=_NEGRO, leading=11),
        "box_b": ParagraphStyle("box_b", fontSize=11, fontName="Helvetica-Bold", alignment=TA_CENTER, textColor=_NEGRO),
        "box":   ParagraphStyle("box", fontSize=9.5, fontName="Helvetica-Bold", alignment=TA_CENTER, textColor=_NEGRO),
        "lab":   ParagraphStyle("lab", fontSize=8, fontName="Helvetica-Bold", textColor=_NEGRO, leading=10),
        "val":   ParagraphStyle("val", fontSize=8, fontName="Helvetica", textColor=_NEGRO, leading=10),
        "th":    ParagraphStyle("th", fontSize=8, fontName="Helvetica-Bold", textColor=_BLANCO, alignment=TA_CENTER, leading=10),
        "td":    ParagraphStyle("td", fontSize=8, fontName="Helvetica", textColor=_NEGRO, alignment=TA_LEFT, leading=10),
        "tdr":   ParagraphStyle("tdr", fontSize=8, fontName="Helvetica", textColor=_NEGRO, alignment=TA_RIGHT, leading=10),
        "tdc":   ParagraphStyle("tdc", fontSize=8, fontName="Helvetica", textColor=_NEGRO, alignment=TA_CENTER, leading=10),
        "sec":   ParagraphStyle("sec", fontSize=8.5, fontName="Helvetica-Bold", textColor=_NEGRO, leading=11),
        "small": ParagraphStyle("small", fontSize=7.5, fontName="Helvetica", textColor=_NEGRO, leading=9),
        "letras":ParagraphStyle("letras", fontSize=8, fontName="Helvetica-Bold", textColor=_NEGRO, leading=10),
        "foot":  ParagraphStyle("foot", fontSize=7, fontName="Helvetica", textColor=colors.grey, alignment=TA_CENTER, leading=9),
    }


def _kv_rows(pairs, st, w, lab_w=4.2*cm):
    """Tabla de filas 'Etiqueta : valor' (datos del receptor)."""
    data = [[Paragraph(f"{k} :", st["lab"]), Paragraph(v or "", st["val"])] for k, v in pairs]
    t = Table(data, colWidths=[lab_w, w - lab_w])
    t.setStyle(TableStyle([
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("TOPPADDING", (0,0), (-1,-1), 1), ("BOTTOMPADDING", (0,0), (-1,-1), 1),
        ("LEFTPADDING", (0,0), (-1,-1), 2), ("RIGHTPADDING", (0,0), (-1,-1), 2),
    ]))
    return t


def _tabla_items(f: Factura, st):
    headers = ["Cantidad", "Unidad Medida", "Descripción", "Valor Unitario", "ICBPER"]
    cw = [2.0*cm, 2.6*cm, 8.4*cm, 2.6*cm, 2.0*cm]
    data = [[Paragraph(h, st["th"]) for h in headers]]
    for l in f.lineas:
        data.append([
            Paragraph(f"{Decimal(l.cantidad):.2f}", st["tdc"]),
            Paragraph(_unidad_nombre(l.unidad), st["tdc"]),
            Paragraph(l.descripcion, st["td"]),
            Paragraph(f"{Decimal(l.precio_unitario):.2f}", st["tdr"]),
            Paragraph("0.00", st["tdr"]),
        ])
    # filas vacías para dar el aire del facturador (mínimo visual)
    t = Table(data, colWidths=cw, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), _GRISH),
        ("TOPPADDING", (0,0), (-1,0), 5), ("BOTTOMPADDING", (0,0), (-1,0), 5),
        ("TOPPADDING", (0,1), (-1,-1), 3), ("BOTTOMPADDING", (0,1), (-1,-1), 3),
        ("LINEBELOW", (0,0), (-1,0), 0.5, _BORDE),
        ("LINEBELOW", (0,-1), (-1,-1), 0.5, _BORDE),
        ("LINEBEFORE", (0,0), (0,-1), 0.5, _BORDE),
        ("LINEAFTER", (-1,0), (-1,-1), 0.5, _BORDE),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ]))
    return t


def _tabla_totales(f: Factura, st, totales: dict, moneda: str):
    sim = _simbolo(moneda)
    g = lambda k, d="0.00": f"{sim} {totales.get(k, d)}"
    filas = [
        ("Sub Total Ventas", g("sub_total", f"{f.subtotal:.2f}")),
        ("Anticipos", f"{sim} 0.00"),
        ("Descuentos", f"{sim} 0.00"),
        ("Valor Venta", g("valor_venta", f"{f.subtotal:.2f}")),
        ("ISC", f"{sim} 0.00"),
        ("IGV", g("igv", f"{f.total_igv:.2f}")),
        ("ICBPER", f"{sim} 0.00"),
        ("Otros Cargos", f"{sim} 0.00"),
        ("Otros Tributos", f"{sim} 0.00"),
        ("Monto de Redondeo", f"{sim} 0.00"),
        ("Importe Total", g("importe_total", f"{f.total:.2f}")),
    ]
    data = [[Paragraph(f"{k} :", st["lab"]), Paragraph(v, st["tdr"])] for k, v in filas]
    t = Table(data, colWidths=[4.4*cm, 3.0*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), _TOTBG),
        ("BOX", (0,0), (-1,-1), 0.5, _BORDE),
        ("INNERGRID", (0,0), (-1,-1), 0.3, colors.HexColor("#E0E0E0")),
        ("TOPPADDING", (0,0), (-1,-1), 2.5), ("BOTTOMPADDING", (0,0), (-1,-1), 2.5),
        ("ALIGN", (0,0), (0,-1), "RIGHT"), ("ALIGN", (1,0), (1,-1), "RIGHT"),
        ("FONTNAME", (0,-1), (-1,-1), "Helvetica-Bold"),
        ("LINEABOVE", (0,-1), (-1,-1), 0.7, _GRISH),
    ]))
    return t


def _bloque_detraccion(det: dict, st, w):
    """Información de la detracción (SPOT). Solo si det no es None."""
    items = [
        ("Leyenda", "Operación sujeta al Sistema de Pago de Obligaciones Tributarias con el Gobierno Central"),
        ("Bien o Servicio", f"{det.get('codigo','')} {det.get('descripcion','')}".strip()),
        ("Medio Pago", det.get("medio_pago", "001 Depósito en cuenta")),
        ("Nro. Cta. Banco de la Nación", det.get("cuenta", "")),
        ("Porcentaje de detracción", f"{det.get('porcentaje','')}"),
        ("Monto detracción", f"S/ {det.get('monto','0.00')}"),
    ]
    cab = Table([[Paragraph("Información de la detracción", st["sec"])]], colWidths=[w])
    cab.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),_GRISL),("BOX",(0,0),(-1,-1),0.5,_BORDE),
                             ("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3),("LEFTPADDING",(0,0),(-1,-1),5)]))
    body = _kv_rows(items, st, w, lab_w=5.2*cm)
    wrap = Table([[cab],[body]], colWidths=[w])
    wrap.setStyle(TableStyle([("BOX",(0,0),(-1,-1),0.5,_BORDE),("TOPPADDING",(0,1),(-1,1),3),("BOTTOMPADDING",(0,1),(-1,1),3),
                              ("LEFTPADDING",(0,1),(-1,1),5)]))
    return wrap


def _bloque_credito(cred: dict, st, w, moneda: str):
    """Información del crédito + tabla de cuotas. Solo si cred no es None."""
    sim = _simbolo(moneda)
    cuotas = cred.get("cuotas", []) or []
    cab = Table([[Paragraph("Información del crédito", st["sec"])]], colWidths=[w])
    cab.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),_GRISL),("BOX",(0,0),(-1,-1),0.5,_BORDE),
                             ("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3),("LEFTPADDING",(0,0),(-1,-1),5)]))
    resumen = _kv_rows([
        ("Monto neto pendiente de pago", f"{sim} {cred.get('neto','0.00')}"),
        ("Total de Cuotas", str(cred.get("total_cuotas", len(cuotas)))),
    ], st, w, lab_w=6.0*cm)
    # tabla de cuotas (encabezado N° Cuota / Fec. Venc. / Monto)
    head = [Paragraph("N° Cuota", st["th"]), Paragraph("Fec. Venc.", st["th"]), Paragraph("Monto", st["th"])]
    cdata = [head]
    for c in cuotas:
        cdata.append([Paragraph(str(c.get("numero","")), st["tdc"]),
                      Paragraph(str(c.get("vencimiento","")), st["tdc"]),
                      Paragraph(f"{c.get('monto','0.00')}", st["tdr"])])
    ctab = Table(cdata, colWidths=[2.5*cm, 3*cm, 3*cm], hAlign="LEFT")
    ctab.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),_GRISH),("GRID",(0,0),(-1,-1),0.4,_BORDE),
                              ("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3)]))
    wrap = Table([[cab],[resumen],[ctab]], colWidths=[w])
    wrap.setStyle(TableStyle([("BOX",(0,0),(-1,-1),0.5,_BORDE),("LEFTPADDING",(0,1),(-1,2),5),
                              ("TOPPADDING",(0,1),(-1,2),3),("BOTTOMPADDING",(0,1),(-1,2),3)]))
    return wrap


def generar_pdf_fiel(
    factura: Factura,
    totales: Optional[dict] = None,
    detraccion: Optional[dict] = None,
    credito: Optional[dict] = None,
    forma_pago: str = "Contado",
    hash_firma: Optional[str] = None,
    logo_path: Optional[Path] = None,
) -> bytes:
    totales = totales or {}
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=1.2*cm, leftMargin=1.2*cm,
                            topMargin=1.2*cm, bottomMargin=1.2*cm)
    st = _st()
    w = A4[0] - 2.4*cm
    story = []
    emisor, receptor = factura.emisor, factura.receptor
    moneda = factura.moneda

    # ── Cabecera: emisor (izq) + caja FACTURA ELECTRONICA (der) ────────────────
    izq = []
    if emisor.nombre_comercial:
        izq.append(Paragraph(emisor.nombre_comercial, st["emi_b"]))
    izq.append(Paragraph(emisor.razon_social, st["emi_b"]))
    d = emisor.direccion
    if d and d.direccion:
        izq.append(Paragraph(d.direccion, st["emi"]))
    linea_geo = " - ".join([x for x in [getattr(d, "distrito", ""), getattr(d, "provincia", ""), getattr(d, "departamento", "")] if x and x != "-"])
    if linea_geo:
        izq.append(Paragraph(linea_geo, st["emi"]))

    caja = Table([[Paragraph("FACTURA ELECTRÓNICA", st["box_b"])],
                  [Paragraph(f"RUC: {emisor.ruc}", st["box"])],
                  [Paragraph(factura.id, st["box"])]], colWidths=[6.4*cm])
    caja.setStyle(TableStyle([("BOX",(0,0),(-1,-1),1.2,_AZUL),("ALIGN",(0,0),(-1,-1),"CENTER"),
                              ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),("VALIGN",(0,0),(-1,-1),"MIDDLE")]))
    cab = Table([[izq, caja]], colWidths=[w - 6.6*cm, 6.6*cm])
    cab.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),("ALIGN",(1,0),(1,0),"RIGHT")]))
    story.append(cab)
    story.append(Spacer(1, 1*mm))
    story.append(Paragraph(f"Forma de pago : {forma_pago}", ParagraphStyle("fp", fontSize=8, fontName="Helvetica-Bold", alignment=TA_RIGHT)))
    story.append(Spacer(1, 2*mm))
    story.append(HRFlowable(width="100%", thickness=0.6, color=_BORDE))
    story.append(Spacer(1, 2*mm))

    # ── Datos del receptor ─────────────────────────────────────────────────────
    tipo_lab = "RUC" if receptor.tipo_doc == "6" else "DNI" if receptor.tipo_doc == "1" else "Doc."
    pares = [
        ("Fecha de Emisión", str(factura.fecha_emision)),
        ("Señor(es)", receptor.razon_social),
        (tipo_lab, receptor.numero_doc),
        ("Dirección del Receptor de la factura", receptor.direccion),
        ("Establecimiento del Emisor", (d.direccion if d else "")),
        ("Tipo de Moneda", _moneda_nombre(moneda)),
    ]
    if factura.observaciones:
        pares.append(("Observación", factura.observaciones))
    story.append(_kv_rows(pares, st, w))
    story.append(Spacer(1, 3*mm))

    # ── Tabla de ítems ─────────────────────────────────────────────────────────
    story.append(_tabla_items(factura, st))
    story.append(Spacer(1, 2*mm))

    # ── Totales (derecha) ──────────────────────────────────────────────────────
    tot = _tabla_totales(factura, st, totales, moneda)
    tot.hAlign = "RIGHT"
    story.append(tot)
    story.append(Spacer(1, 2*mm))

    # ── Monto en letras ────────────────────────────────────────────────────────
    story.append(Paragraph(f"SON: {monto_en_letras(factura.total, moneda)}", st["letras"]))
    story.append(Spacer(1, 3*mm))

    # ── Bloques condicionales ──────────────────────────────────────────────────
    if detraccion:
        story.append(_bloque_detraccion(detraccion, st, w))
        story.append(Spacer(1, 2*mm))
    if credito:
        story.append(_bloque_credito(credito, st, w, moneda))
        story.append(Spacer(1, 2*mm))

    # ── Pie ────────────────────────────────────────────────────────────────────
    if hash_firma:
        story.append(Paragraph(f"Valor Resumen (Hash): {hash_firma[:10].upper()}", st["small"]))
    story.append(Paragraph("Representación impresa de la Factura Electrónica — Autorizado mediante RS 097-2012/SUNAT.", st["foot"]))

    doc.build(story)
    return buf.getvalue()


if __name__ == "__main__":
    from sunat_ubl import Proveedor, Cliente, Linea, DireccionFiscal
    f = Factura(
        serie="F001", numero=15,
        emisor=Proveedor(ruc="20610565451", razon_social="GTL CONSULTING S.A.C.S.", nombre_comercial="GLOBAL GTL",
                         direccion=DireccionFiscal(direccion="OTR. PARCELA 102 COO. LOS LAURELES", distrito="CHANCAY", provincia="HUARAL", departamento="LIMA")),
        receptor=Cliente(tipo_doc="6", numero_doc="20612437727", razon_social="APU SAN LORENZO E.I.R.L.",
                         direccion="JR. LORETO URB. VILLAMERCEDES MZA. D LOTE. 12 POR JR BRACESCO PUNO SAN ROMAN JULIACA"),
        lineas=[Linea(descripcion="123", cantidad=1, precio_unitario=123.0, unidad="ZZ")],
        moneda="USD", observaciones="123",
    )
    tot = {"sub_total":"123.00","valor_venta":"123.00","igv":"22.14","importe_total":"145.14"}
    det = {"codigo":"022","descripcion":"Otros servicios empresariales","medio_pago":"001 Depósito en cuenta",
           "cuenta":"01010100101","porcentaje":"12.00","monto":"456.00"}
    cred = {"neto":"123.00","total_cuotas":1,"cuotas":[{"numero":1,"vencimiento":"04/06/2026","monto":"123.00"}]}
    pdf = generar_pdf_fiel(f, totales=tot, detraccion=det, credito=cred, forma_pago="Crédito", hash_firma="Ab3xKl9mZ1")
    out = Path("/tmp/factura_fiel_demo.pdf"); out.write_bytes(pdf)
    print(f"OK {out} ({len(pdf)} bytes)")
    # Variante SIN detracción ni crédito (deben desaparecer)
    pdf2 = generar_pdf_fiel(f, totales=tot, forma_pago="Contado", hash_firma="Ab3xKl9mZ1")
    Path("/tmp/factura_fiel_contado.pdf").write_bytes(pdf2)
    print("OK contado (sin spot/credito)")
