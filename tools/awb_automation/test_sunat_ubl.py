#!/usr/bin/env python3
"""Tests para sunat_ubl.py — validación del XML UBL 2.1 SUNAT."""
import sys
from decimal import Decimal
from pathlib import Path
from lxml import etree

sys.path.insert(0, str(Path(__file__).parent))
from sunat_ubl import (
    Factura, Proveedor, Cliente, Linea, DireccionFiscal,
    generar_xml, d, IGV_RATE, CAC, CBC, EXT,
)

# Fixtures base
def _emisor():
    return Proveedor(
        ruc="20123456789",
        razon_social="MI EMPRESA SAC",
        nombre_comercial="Mi Empresa",
        direccion=DireccionFiscal(
            ubigeo="150101", departamento="LIMA", provincia="LIMA",
            distrito="LIMA", direccion="AV. PRINCIPAL 123",
        )
    )

def _receptor():
    return Cliente(
        tipo_doc="6", numero_doc="20987654321",
        razon_social="CLIENTE SAC", email="cliente@test.com"
    )

def _linea_simple():
    return Linea(descripcion="Servicio de transporte", cantidad=1, precio_unitario=100.0)

def _factura_simple():
    return Factura(
        serie="F001", numero=1,
        emisor=_emisor(), receptor=_receptor(),
        lineas=[_linea_simple()],
    )


# ── Test 1: Cálculos decimales ────────────────────────────────────────────────

def test_linea_calculos():
    l = Linea(descripcion="X", cantidad=2, precio_unitario=100.0)
    assert l.valor_venta == Decimal("200.00")
    assert l.igv == Decimal("36.00")          # 200 × 18%
    assert l.precio_con_igv == Decimal("118.00")
    print("PASS test_linea_calculos")

def test_factura_totales():
    f = _factura_simple()
    assert f.subtotal == Decimal("100.00")
    assert f.total_igv == Decimal("18.00")
    assert f.total == Decimal("118.00")
    print("PASS test_factura_totales")

def test_factura_id():
    f = _factura_simple()
    assert f.id == "F001-00000001"
    print("PASS test_factura_id")

def test_factura_multi_lineas():
    f = Factura(
        serie="F001", numero=5,
        emisor=_emisor(), receptor=_receptor(),
        lineas=[
            Linea(descripcion="Item A", cantidad=2, precio_unitario=50.0),
            Linea(descripcion="Item B", cantidad=1, precio_unitario=200.0),
        ]
    )
    assert f.subtotal == Decimal("300.00")   # 100 + 200
    assert f.total_igv == Decimal("54.00")   # 300 × 18%
    assert f.total == Decimal("354.00")
    print("PASS test_factura_multi_lineas")

def test_linea_inafecta():
    l = Linea(descripcion="Servicio exonerado", cantidad=1, precio_unitario=500.0, afecto_igv=False)
    assert l.igv == Decimal("0.00")
    assert l.precio_con_igv == Decimal("500.00")
    print("PASS test_linea_inafecta")


# ── Test 2: Generación XML ────────────────────────────────────────────────────

def test_generar_xml_retorna_bytes():
    xml = generar_xml(_factura_simple())
    assert isinstance(xml, bytes)
    assert b"<?xml" in xml
    print("PASS test_generar_xml_retorna_bytes")

def test_xml_es_valido():
    xml = generar_xml(_factura_simple())
    root = etree.fromstring(xml)
    assert root is not None
    print("PASS test_xml_es_valido")

def test_xml_contiene_ruc_emisor():
    xml = generar_xml(_factura_simple())
    assert b"20123456789" in xml
    print("PASS test_xml_contiene_ruc_emisor")

def test_xml_contiene_ruc_receptor():
    xml = generar_xml(_factura_simple())
    assert b"20987654321" in xml
    print("PASS test_xml_contiene_ruc_receptor")

def test_xml_contiene_id_factura():
    xml = generar_xml(_factura_simple())
    assert b"F001-00000001" in xml
    print("PASS test_xml_contiene_id_factura")

def test_xml_ubl_version():
    xml = generar_xml(_factura_simple())
    root = etree.fromstring(xml)
    ns = {"cbc": CBC}
    ubl_ver = root.find("cbc:UBLVersionID", ns)
    assert ubl_ver is not None and ubl_ver.text == "2.1"
    print("PASS test_xml_ubl_version")

def test_xml_tipo_documento_factura():
    xml = generar_xml(_factura_simple())
    assert b">01<" in xml  # código de factura electrónica
    print("PASS test_xml_tipo_documento_factura")

def test_xml_total_igv():
    xml = generar_xml(_factura_simple())
    assert b"18.00" in xml
    print("PASS test_xml_total_igv")

def test_xml_total_pagar():
    xml = generar_xml(_factura_simple())
    assert b"118.00" in xml
    print("PASS test_xml_total_pagar")

def test_xml_tiene_ubl_extensions():
    xml = generar_xml(_factura_simple())
    assert b"UBLExtensions" in xml
    assert b"Signature" in xml   # placeholder para firma
    print("PASS test_xml_tiene_ubl_extensions")

def test_xml_linea_descripcion():
    xml = generar_xml(_factura_simple())
    assert b"Servicio de transporte" in xml
    print("PASS test_xml_linea_descripcion")

def test_xml_moneda_pen():
    xml = generar_xml(_factura_simple())
    assert b"PEN" in xml
    print("PASS test_xml_moneda_pen")

def test_xml_moneda_usd():
    f = Factura(
        serie="F001", numero=2,
        emisor=_emisor(), receptor=_receptor(),
        lineas=[_linea_simple()],
        moneda="USD",
    )
    xml = generar_xml(f)
    assert b"USD" in xml
    print("PASS test_xml_moneda_usd")

def test_xml_nombre_comercial():
    xml = generar_xml(_factura_simple())
    assert b"Mi Empresa" in xml
    print("PASS test_xml_nombre_comercial")

def test_xml_encoding_utf8():
    f = Factura(
        serie="F001", numero=3,
        emisor=Proveedor(ruc="20000000001", razon_social="EMPRESA PERÚ SAC"),
        receptor=Cliente(tipo_doc="6", numero_doc="20000000002",
                         razon_social="CLIENTE ÑOÑO SRL"),
        lineas=[Linea(descripcion="Servicio ñoño con tilde", cantidad=1, precio_unitario=50.0)],
    )
    xml = generar_xml(f)
    assert b"UTF-8" in xml
    # Verificar que el XML con caracteres especiales es parseable
    root = etree.fromstring(xml)
    assert root is not None
    print("PASS test_xml_encoding_utf8")


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_linea_calculos,
        test_factura_totales,
        test_factura_id,
        test_factura_multi_lineas,
        test_linea_inafecta,
        test_generar_xml_retorna_bytes,
        test_xml_es_valido,
        test_xml_contiene_ruc_emisor,
        test_xml_contiene_ruc_receptor,
        test_xml_contiene_id_factura,
        test_xml_ubl_version,
        test_xml_tipo_documento_factura,
        test_xml_total_igv,
        test_xml_total_pagar,
        test_xml_tiene_ubl_extensions,
        test_xml_linea_descripcion,
        test_xml_moneda_pen,
        test_xml_moneda_usd,
        test_xml_nombre_comercial,
        test_xml_encoding_utf8,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            print(f"FAIL {t.__name__}: {e}")
            failed += 1
    print(f"\n{'='*40}")
    print(f"Resultados: {len(tests)-failed}/{len(tests)} tests pasados")
    if failed:
        sys.exit(1)
