#!/usr/bin/env python3
"""
sunat_pdf.py — Representación Impresa de Factura Electrónica SUNAT
Genera el PDF legible (representación impresa) según RS 097-2012/SUNAT Anexo 3.

Uso:
    from sunat_pdf import generar_pdf
    from sunat_ubl import Factura, ...

    pdf_bytes = generar_pdf(factura)
    Path("F001-00000001.pdf").write_bytes(pdf_bytes)

    # Con hash de firma (10 primeros chars del DigestValue del XML firmado)
    pdf_bytes = generar_pdf(factura, hash_firma="Ab3xKl9mZ1")
"""
from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import Flowable

# Import condicional de qrcode
try:
    import qrcode
    from PIL import Image as PILImage
    from reportlab.platypus import Image as RLImage
    _QR_AVAILABLE = True
except ImportError:
    _QR_AVAILABLE = False

# Import del modelo de datos
import sys
sys.path.insert(0, str(Path(__file__).parent))
from sunat_ubl import Factura


# ── Colores corporativos ──────────────────────────────────────────────────────

_AZUL_SUNAT    = colors.HexColor("#003366")  # azul oscuro cabecera
_GRIS_CLARO    = colors.HexColor("#F5F5F5")
_GRIS_HEADER   = colors.HexColor("#D0D8E4")
_NEGRO         = colors.black
_BLANCO        = colors.white


# ── Unidad de medida: codigo SUNAT (cat.03) -> nombre completo para la impresion ──
# El XML SIEMPRE lleva el CODIGO (linea.unidad). En la representacion impresa SUNAT se
# muestra el NOMBRE completo (asi sale en SUNAT — Henry 2026-05-30).
# Fuente: catalogo_03.json oficial de cpe_engine (62 entradas). Fallback: codigo tal cual.
_UNIDAD_NOMBRES = {
    "BJ": "BALDE", "BLL": "BARRILES", "4A": "BOBINAS", "BG": "BOLSA",
    "BO": "BOTELLAS", "BX": "CAJAS", "CT": "CARTONES", "CMK": "CENTIMETRO CUADRADO",
    "CMQ": "CENTIMETRO CUBICO", "CMT": "CENTIMETRO LINEAL", "CEN": "CIENTO DE UNIDADES",
    "CY": "CILINDRO", "CJ": "CONOS", "DZN": "DOCENA", "DZP": "DOCENA POR 10**6",
    "BE": "FARDO", "GLI": "GALON INGLES (4,545956L)", "GRM": "GRAMO", "GRO": "GRUESA",
    "HLT": "HECTOLITRO", "LEF": "HOJA", "SET": "JUEGO", "KGM": "KILOGRAMO",
    "KTM": "KILOMETRO", "KWH": "KILOVATIO HORA", "KT": "KIT", "CA": "LATAS",
    "LBR": "LIBRAS", "LTR": "LITROS", "MWH": "MEGAWATT HORA", "MTR": "METRO",
    "MTK": "METRO CUADRADO", "MTQ": "METRO CUBICO", "MGM": "MILIGRAMOS",
    "MLT": "MILILITRO", "MMT": "MILIMETRO", "MMK": "MILIMETRO CUADRADO",
    "MMQ": "MILIMETRO CUBICO", "MLL": "MILLARES", "MU": "MILLON DE UNIDADES",
    "ONZ": "ONZAS", "PF": "PALETAS", "PK": "PAQUETE", "PR": "PAR",
    "FOT": "PIES", "FTK": "PIES CUADRADOS", "FTQ": "PIES CUBICOS", "C62": "PIEZAS",
    "PG": "PLACAS", "ST": "PLIEGO", "INH": "PULGADAS", "RM": "RESMA",
    "DR": "TAMBOR", "STN": "TONELADA CORTA", "LTN": "TONELADA LARGA", "TNE": "TONELADAS",
    "TU": "TUBOS", "NIU": "UNIDAD", "ZZ": "UNIDAD (SERVICIOS)", "GLL": "US GALON (3,7843 L)",
    "YRD": "YARDA", "YDK": "YARDA CUADRADA",
    # alias compatibilidad backward
    "PIE TABLAR": "PIE TABLAR",
}


def _unidad_nombre(codigo: str) -> str:
    """Nombre completo de la U.M. para la impresion; si el codigo no esta mapeado, lo deja igual."""
    return _UNIDAD_NOMBRES.get((codigo or "").strip().upper(), codigo)


# ── QR Code ───────────────────────────────────────────────────────────────────

def _qr_contenido(f: Factura) -> str:
    """Genera el string QR según especificación SUNAT RS 097-2012."""
    return (
        f"{f.emisor.ruc}|01|{f.serie}|{f.numero}|"
        f"{f.total_igv}|{f.total}|{f.fecha_emision}|"
        f"{f.receptor.tipo_doc}|{f.receptor.numero_doc}|"
    )


def _qr_flowable(f: Factura, size_cm: float = 3.5) -> Optional[object]:
    """Retorna un Image flowable del QR o None si qrcode no está disponible."""
    if not _QR_AVAILABLE:
        return None
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(_qr_contenido(f))
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    size = size_cm * cm
    return RLImage(buf, width=size, height=size)


# ── Monto en letras ───────────────────────────────────────────────────────────

_UNIDADES = [
    "", "UNO", "DOS", "TRES", "CUATRO", "CINCO",
    "SEIS", "SIETE", "OCHO", "NUEVE", "DIEZ",
    "ONCE", "DOCE", "TRECE", "CATORCE", "QUINCE",
    "DIECISÉIS", "DIECISIETE", "DIECIOCHO", "DIECINUEVE",
]
_DECENAS = [
    "", "", "VEINTE", "TREINTA", "CUARENTA", "CINCUENTA",
    "SESENTA", "SETENTA", "OCHENTA", "NOVENTA",
]
_CENTENAS = [
    "", "CIEN", "DOSCIENTOS", "TRESCIENTOS", "CUATROCIENTOS",
    "QUINIENTOS", "SEISCIENTOS", "SETECIENTOS", "OCHOCIENTOS", "NOVECIENTOS",
]


def _cientos(n: int) -> str:
    if n == 0:
        return ""
    if n < 20:
        return _UNIDADES[n]
    if n < 100:
        d, u = divmod(n, 10)
        return _DECENAS[d] + (" Y " + _UNIDADES[u] if u else "")
    c, resto = divmod(n, 100)
    sufijo = (" " + _cientos(resto)) if resto else ""
    if c == 1 and resto:
        return "CIENTO" + sufijo
    return _CENTENAS[c] + sufijo


def monto_en_letras(monto: Decimal, moneda: str = "PEN") -> str:
    """Convierte un monto decimal a letras para la representación impresa."""
    entero = int(monto)
    centavos = int(round((monto - entero) * 100))
    simbolo = "SOLES" if moneda == "PEN" else "DÓLARES AMERICANOS"

    if entero == 0:
        texto = "CERO"
    elif entero < 1000:
        texto = _cientos(entero)
    elif entero < 1_000_000:
        miles, resto = divmod(entero, 1000)
        texto = ("MIL" if miles == 1 else _cientos(miles) + " MIL")
        if resto:
            texto += " " + _cientos(resto)
    else:
        millones, resto = divmod(entero, 1_000_000)
        texto = _cientos(millones) + (" MILLÓN" if millones == 1 else " MILLONES")
        if resto >= 1000:
            miles, r2 = divmod(resto, 1000)
            texto += " " + ("MIL" if miles == 1 else _cientos(miles) + " MIL")
            if r2:
                texto += " " + _cientos(r2)
        elif resto:
            texto += " " + _cientos(resto)

    return f"{texto} Y {centavos:02d}/100 {simbolo}"


# ── Estilos ───────────────────────────────────────────────────────────────────

def _estilos():
    s = getSampleStyleSheet()
    return {
        "titulo": ParagraphStyle(
            "titulo", fontSize=14, fontName="Helvetica-Bold",
            textColor=_AZUL_SUNAT, alignment=TA_CENTER, spaceAfter=2,
        ),
        "subtitulo": ParagraphStyle(
            "subtitulo", fontSize=10, fontName="Helvetica-Bold",
            textColor=_AZUL_SUNAT, alignment=TA_CENTER, spaceAfter=2,
        ),
        "serie_num": ParagraphStyle(
            "serie_num", fontSize=11, fontName="Helvetica-Bold",
            textColor=_NEGRO, alignment=TA_CENTER, spaceAfter=2,
        ),
        "label": ParagraphStyle(
            "label", fontSize=8, fontName="Helvetica-Bold",
            textColor=_AZUL_SUNAT, spaceAfter=1,
        ),
        "valor": ParagraphStyle(
            "valor", fontSize=8, fontName="Helvetica",
            textColor=_NEGRO, spaceAfter=1,
        ),
        "tabla_header": ParagraphStyle(
            "tabla_header", fontSize=8, fontName="Helvetica-Bold",
            textColor=_BLANCO, alignment=TA_CENTER,
        ),
        "tabla_cel": ParagraphStyle(
            "tabla_cel", fontSize=8, fontName="Helvetica",
            textColor=_NEGRO, alignment=TA_LEFT,
        ),
        "tabla_num": ParagraphStyle(
            "tabla_num", fontSize=8, fontName="Helvetica",
            textColor=_NEGRO, alignment=TA_RIGHT,
        ),
        "total_label": ParagraphStyle(
            "total_label", fontSize=9, fontName="Helvetica-Bold",
            textColor=_NEGRO, alignment=TA_RIGHT,
        ),
        "total_val": ParagraphStyle(
            "total_val", fontSize=9, fontName="Helvetica-Bold",
            textColor=_NEGRO, alignment=TA_RIGHT,
        ),
        "footer": ParagraphStyle(
            "footer", fontSize=7, fontName="Helvetica",
            textColor=colors.grey, alignment=TA_CENTER, spaceAfter=2,
        ),
        "leyenda": ParagraphStyle(
            "leyenda", fontSize=8, fontName="Helvetica",
            textColor=_NEGRO, spaceAfter=2,
        ),
        "letras": ParagraphStyle(
            "letras", fontSize=8, fontName="Helvetica-BoldOblique",
            textColor=_NEGRO, spaceAfter=4,
        ),
    }


# ── Tabla de líneas ────────────────────────────────────────────────────────────

def _tabla_lineas(f: Factura, st: dict) -> Table:
    headers = ["Cant.", "U.M.", "Descripción", "V. Unit. (sin IGV)", "Dscto.", "Val. Venta (S/)"]
    col_widths = [1.5*cm, 1.2*cm, 8*cm, 2.5*cm, 1.5*cm, 2.8*cm]

    data = [[Paragraph(h, st["tabla_header"]) for h in headers]]

    for linea in f.lineas:
        # precio_unitario ya es SIN IGV por definicion (sunat_ubl.Linea) y asi va en el XML
        # (PriceAmount). NO dividir entre 1.18: hacerlo lo deflacta 2 veces y descuadra el PDF
        # contra el XML. La columna "P. Unit (sin IGV)" muestra la base unitaria tal cual.
        precio_sin_igv = linea.precio_unitario
        data.append([
            Paragraph(f"{linea.cantidad:.2f}", st["tabla_num"]),
            # U.M.: muestra el NOMBRE completo (asi sale en SUNAT) pero el codigo real
            # (linea.unidad) es el que va al XML como unitCode. NO hardcodear.
            Paragraph(_unidad_nombre(linea.unidad), st["tabla_cel"]),
            Paragraph(linea.descripcion, st["tabla_cel"]),
            Paragraph(f"{precio_sin_igv:.2f}", st["tabla_num"]),
            Paragraph("0.00", st["tabla_num"]),
            Paragraph(f"{linea.valor_venta:.2f}", st["tabla_num"]),
        ])

    tabla = Table(data, colWidths=col_widths, repeatRows=1)
    tabla.setStyle(TableStyle([
        # Header
        ("BACKGROUND",  (0, 0), (-1, 0), _AZUL_SUNAT),
        ("TEXTCOLOR",   (0, 0), (-1, 0), _BLANCO),
        ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, 0), 8),
        ("ALIGN",       (0, 0), (-1, 0), "CENTER"),
        ("TOPPADDING",  (0, 0), (-1, 0), 4),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
        # Filas de datos
        ("FONTNAME",    (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE",    (0, 1), (-1, -1), 8),
        ("TOPPADDING",  (0, 1), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 3),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [_BLANCO, _GRIS_CLARO]),
        # Grid
        ("GRID",        (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("LINEBELOW",   (0, 0), (-1, 0), 1, _AZUL_SUNAT),
    ]))
    return tabla


# ── Tabla de totales ──────────────────────────────────────────────────────────

def _tabla_totales(f: Factura, st: dict) -> Table:
    base_imponible = f.subtotal
    total_inafecto = sum(
        l.valor_venta for l in f.lineas if not l.afecto_igv
    )

    filas = []
    if base_imponible > 0:
        filas.append(["Op. Gravadas:", f"S/ {base_imponible:.2f}"])
    if total_inafecto > 0:
        filas.append(["Op. Inafectas:", f"S/ {total_inafecto:.2f}"])
    filas.append(["IGV (18%):", f"S/ {f.total_igv:.2f}"])
    filas.append(["IMPORTE TOTAL:", f"S/ {f.total:.2f}"])

    data = [
        [Paragraph(label, st["total_label"]), Paragraph(val, st["total_val"])]
        for label, val in filas
    ]

    tabla = Table(data, colWidths=[5*cm, 3*cm])
    tabla.setStyle(TableStyle([
        ("FONTNAME",    (0, 0), (-1, -2), "Helvetica"),
        ("FONTNAME",    (0, -1), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, -1), 9),
        ("ALIGN",       (0, 0), (-1, -1), "RIGHT"),
        ("TOPPADDING",  (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LINEABOVE",   (0, -1), (-1, -1), 1, _AZUL_SUNAT),
        ("BACKGROUND",  (0, -1), (-1, -1), _GRIS_HEADER),
    ]))
    return tabla


# ── Función principal ──────────────────────────────────────────────────────────

def generar_pdf(
    factura: Factura,
    hash_firma: Optional[str] = None,
    logo_path: Optional[Path] = None,
) -> bytes:
    """Genera la representación impresa de la factura electrónica en PDF.

    Args:
        factura:    Objeto Factura con todos los datos.
        hash_firma: Primeros 10 caracteres del DigestValue del XML firmado.
                    Si None, omite el campo en el PDF.
        logo_path:  Ruta a imagen del logo de la empresa (PNG/JPG, opcional).

    Returns:
        Bytes del PDF generado.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=1.5*cm,
        leftMargin=1.5*cm,
        topMargin=1.5*cm,
        bottomMargin=1.5*cm,
    )

    st = _estilos()
    story = []
    w = A4[0] - 3*cm  # ancho útil

    # ── Cabecera: logo + datos empresa + caja número ───────────────────────────
    emisor = factura.emisor

    # Columna izquierda: logo + razón social
    col_empresa = []
    if logo_path and logo_path.exists():
        col_empresa.append(RLImage(str(logo_path), width=4*cm, height=2*cm))
        col_empresa.append(Spacer(1, 2*mm))

    col_empresa.append(Paragraph(emisor.razon_social, st["titulo"]))
    if emisor.nombre_comercial:
        col_empresa.append(Paragraph(emisor.nombre_comercial, st["subtitulo"]))
    col_empresa.append(Paragraph(f"RUC: {emisor.ruc}", st["valor"]))
    if emisor.direccion:
        d = emisor.direccion
        dir_text = d.direccion or ""
        if d.distrito:
            dir_text += f", {d.distrito}"
        if d.provincia:
            dir_text += f" - {d.provincia}"
        col_empresa.append(Paragraph(dir_text, st["valor"]))

    # Columna derecha: caja azul con número de comprobante
    caja_datos = [
        [Paragraph("FACTURA ELECTRÓNICA", st["subtitulo"])],
        [Paragraph(f"Nro. {factura.id}", st["serie_num"])],
        [Spacer(1, 2*mm)],
        [Paragraph(f"Fecha de Emisión: {factura.fecha_emision}", st["valor"])],
        [Paragraph(f"Moneda: {'Soles (PEN)' if factura.moneda == 'PEN' else 'Dólares (USD)'}", st["valor"])],
    ]
    tabla_caja = Table(caja_datos, colWidths=[7*cm])
    tabla_caja.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, 1), _AZUL_SUNAT),
        ("TEXTCOLOR",   (0, 0), (-1, 1), _BLANCO),
        ("BACKGROUND",  (0, 2), (-1, -1), _GRIS_HEADER),
        ("ALIGN",       (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING",  (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("BOX",         (0, 0), (-1, -1), 1, _AZUL_SUNAT),
    ]))

    # Tabla principal de cabecera (2 columnas)
    ancho_empresa = w - 7.5*cm
    cabecera = Table(
        [[col_empresa, tabla_caja]],
        colWidths=[ancho_empresa, 7.5*cm],
    )
    cabecera.setStyle(TableStyle([
        ("VALIGN",  (0, 0), (-1, -1), "TOP"),
        ("ALIGN",   (1, 0), (1, 0), "RIGHT"),
    ]))
    story.append(cabecera)
    story.append(Spacer(1, 4*mm))
    story.append(HRFlowable(width="100%", thickness=1, color=_AZUL_SUNAT))
    story.append(Spacer(1, 3*mm))

    # ── Datos del receptor ────────────────────────────────────────────────────
    receptor = factura.receptor
    tipo_label = "RUC" if receptor.tipo_doc == "6" else "DNI" if receptor.tipo_doc == "1" else f"Doc.{receptor.tipo_doc}"
    receptor_data = [
        [
            Paragraph("DATOS DEL CLIENTE", st["label"]),
            Paragraph(receptor.razon_social, st["valor"]),
        ],
        [
            Paragraph(tipo_label, st["label"]),
            Paragraph(receptor.numero_doc, st["valor"]),
        ],
    ]
    if receptor.email:
        receptor_data.append([
            Paragraph("Email", st["label"]),
            Paragraph(receptor.email, st["valor"]),
        ])

    tabla_receptor = Table(receptor_data, colWidths=[3*cm, w - 3*cm])
    tabla_receptor.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, -1), _GRIS_CLARO),
        ("BOX",         (0, 0), (-1, -1), 0.5, colors.HexColor("#AAAAAA")),
        ("TOPPADDING",  (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(tabla_receptor)
    story.append(Spacer(1, 4*mm))

    # ── Tabla de líneas ───────────────────────────────────────────────────────
    story.append(_tabla_lineas(factura, st))
    story.append(Spacer(1, 3*mm))

    # ── Monto en letras ───────────────────────────────────────────────────────
    letras = monto_en_letras(factura.total, factura.moneda)
    story.append(Paragraph(f"Son: {letras}", st["letras"]))

    # ── Totales + QR ─────────────────────────────────────────────────────────
    qr_img = _qr_flowable(factura)
    totales_tabla = _tabla_totales(factura, st)

    if qr_img:
        # QR a la izquierda, totales a la derecha
        qr_col = [
            Paragraph("Código QR SUNAT", ParagraphStyle("qrlabel", fontSize=7,
                fontName="Helvetica", textColor=colors.grey, alignment=TA_CENTER)),
            qr_img,
        ]
        pie_tabla = Table(
            [[qr_col, totales_tabla]],
            colWidths=[4.5*cm, w - 4.5*cm],
        )
        pie_tabla.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
            ("ALIGN",  (1, 0), (1, 0), "RIGHT"),
        ]))
        story.append(pie_tabla)
    else:
        # Sin QR: solo totales alineados a la derecha
        pie_tabla = Table([[Spacer(1, 1), totales_tabla]], colWidths=[w - 8*cm, 8*cm])
        pie_tabla.setStyle(TableStyle([("ALIGN", (1, 0), (1, 0), "RIGHT")]))
        story.append(pie_tabla)

    story.append(Spacer(1, 4*mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
    story.append(Spacer(1, 2*mm))

    # ── Leyendas SUNAT ────────────────────────────────────────────────────────
    if hash_firma:
        story.append(Paragraph(
            f"Hash de firma digital: {hash_firma[:10].upper()}",
            st["leyenda"]
        ))

    story.append(Paragraph(
        "Representación Impresa de Factura Electrónica — "
        "Autorizado mediante RS 097-2012/SUNAT y modificatorias.",
        st["footer"]
    ))
    story.append(Paragraph(
        "Este documento es la representación impresa de un comprobante electrónico. "
        "Consulte su validez en: https://ww1.sunat.gob.pe/ol-ti-itconsultaunificadalibre/",
        st["footer"]
    ))

    doc.build(story)
    return buf.getvalue()


# ── CLI rápida ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import secrets
    from sunat_ubl import Proveedor, Cliente, Linea, DireccionFiscal

    factura_demo = Factura(
        serie="F001", numero=1,
        emisor=Proveedor(
            ruc="20123456789", razon_social="MI EMPRESA SAC",
            nombre_comercial="Mi Empresa",
            direccion=DireccionFiscal(
                ubigeo="150101", departamento="LIMA", provincia="LIMA",
                distrito="MIRAFLORES", direccion="AV. PRINCIPAL 123",
            )
        ),
        receptor=Cliente(
            tipo_doc="6", numero_doc="20987654321",
            razon_social="CLIENTE IMPORTACIONES SAC",
            email="cliente@empresa.com",
        ),
        lineas=[
            Linea(descripcion="Servicio de transporte internacional", cantidad=2, precio_unitario=500.0),
            Linea(descripcion="Gestión aduanera", cantidad=1, precio_unitario=300.0),
        ],
    )

    pdf = generar_pdf(factura_demo, hash_firma="Ab3xKl9mZ1")
    out = Path("/tmp/F001-00000001_demo.pdf")
    out.write_bytes(pdf)
    print(f"PDF generado: {out} ({len(pdf):,} bytes)")
