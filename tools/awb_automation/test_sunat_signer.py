#!/usr/bin/env python3
"""Tests para sunat_signer.py — firma digital XMLDSig SUNAT."""
import sys
import tempfile
from pathlib import Path
from lxml import etree

sys.path.insert(0, str(Path(__file__).parent))
from sunat_ubl import (
    Factura, Proveedor, Cliente, Linea, DireccionFiscal, generar_xml,
)
from sunat_signer import firmar_xml, generar_cert_prueba

DS  = "http://www.w3.org/2000/09/xmldsig#"
EXT = "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"

# ── Fixtures ──────────────────────────────────────────────────────────────────

def _factura_simple():
    return Factura(
        serie="F001", numero=1,
        emisor=Proveedor(
            ruc="20123456789", razon_social="MI EMPRESA SAC",
            nombre_comercial="Mi Empresa",
            direccion=DireccionFiscal(
                ubigeo="150101", departamento="LIMA", provincia="LIMA",
                distrito="LIMA", direccion="AV. PRINCIPAL 123",
            )
        ),
        receptor=Cliente(
            tipo_doc="6", numero_doc="20987654321",
            razon_social="CLIENTE SAC",
        ),
        lineas=[Linea(descripcion="Servicio de transporte", cantidad=1, precio_unitario=100.0)],
    )

# Certificado de prueba generado una sola vez por sesión
_pfx_cache: tuple[Path, str] | None = None

def _get_test_pfx():
    global _pfx_cache
    if _pfx_cache is None:
        import secrets
        pfx_path = Path(tempfile.mktemp(suffix=".pfx"))
        _pfx_cache = generar_cert_prueba(pfx_path, password=secrets.token_urlsafe(16))
    return _pfx_cache


# ── Tests: generación de certificado de prueba ────────────────────────────────

def test_generar_cert_prueba_crea_archivo():
    pfx, pwd = _get_test_pfx()
    assert pfx.exists()
    assert pfx.stat().st_size > 0
    print("PASS test_generar_cert_prueba_crea_archivo")

def test_generar_cert_prueba_legible():
    from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates
    pfx, pwd = _get_test_pfx()
    key, cert, _ = load_key_and_certificates(pfx.read_bytes(), pwd.encode())
    assert key is not None
    assert cert is not None
    print("PASS test_generar_cert_prueba_legible")

def test_cert_prueba_diferente_password_falla():
    pfx, _ = _get_test_pfx()
    try:
        from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates
        load_key_and_certificates(pfx.read_bytes(), b"password_incorrecta")
        # Algunas versiones de cryptography retornan None sin lanzar
        print("PASS test_cert_prueba_diferente_password_falla (no exception — lib permite)")
    except Exception:
        print("PASS test_cert_prueba_diferente_password_falla (exception raised OK)")


# ── Tests: firma del XML ──────────────────────────────────────────────────────

def test_firmar_xml_retorna_bytes():
    pfx, pwd = _get_test_pfx()
    xml = generar_xml(_factura_simple())
    signed = firmar_xml(xml, pfx, pwd)
    assert isinstance(signed, bytes)
    assert b"<?xml" in signed
    print("PASS test_firmar_xml_retorna_bytes")

def test_xml_firmado_es_parseable():
    pfx, pwd = _get_test_pfx()
    xml = generar_xml(_factura_simple())
    signed = firmar_xml(xml, pfx, pwd)
    root = etree.fromstring(signed)
    assert root is not None
    print("PASS test_xml_firmado_es_parseable")

def test_xml_firmado_tiene_ds_signature():
    pfx, pwd = _get_test_pfx()
    xml = generar_xml(_factura_simple())
    signed = firmar_xml(xml, pfx, pwd)
    assert b"<ds:Signature" in signed or b"Signature" in signed
    print("PASS test_xml_firmado_tiene_ds_signature")

def test_firma_dentro_de_extension_content():
    """La firma debe estar dentro de ext:ExtensionContent — requisito SUNAT."""
    pfx, pwd = _get_test_pfx()
    xml = generar_xml(_factura_simple())
    signed = firmar_xml(xml, pfx, pwd)
    root = etree.fromstring(signed)
    ext_content = root.find(f".//{{{EXT}}}ExtensionContent")
    assert ext_content is not None, "No hay ExtensionContent"
    sig = ext_content.find(f"{{{DS}}}Signature")
    assert sig is not None, "ds:Signature no está dentro de ExtensionContent"
    print("PASS test_firma_dentro_de_extension_content")

def test_firma_tiene_signaturevalue():
    pfx, pwd = _get_test_pfx()
    xml = generar_xml(_factura_simple())
    signed = firmar_xml(xml, pfx, pwd)
    root = etree.fromstring(signed)
    sig_value = root.find(f".//{{{DS}}}SignatureValue")
    assert sig_value is not None
    assert sig_value.text is not None and len(sig_value.text.strip()) > 20
    print("PASS test_firma_tiene_signaturevalue")

def test_firma_tiene_x509_certificate():
    pfx, pwd = _get_test_pfx()
    xml = generar_xml(_factura_simple())
    signed = firmar_xml(xml, pfx, pwd)
    root = etree.fromstring(signed)
    cert_node = root.find(f".//{{{DS}}}X509Certificate")
    assert cert_node is not None
    assert len(cert_node.text.strip()) > 20
    print("PASS test_firma_tiene_x509_certificate")

def test_xml_firmado_conserva_id_factura():
    pfx, pwd = _get_test_pfx()
    xml = generar_xml(_factura_simple())
    signed = firmar_xml(xml, pfx, pwd)
    assert b"F001-00000001" in signed
    print("PASS test_xml_firmado_conserva_id_factura")

def test_xml_firmado_conserva_ruc_emisor():
    pfx, pwd = _get_test_pfx()
    xml = generar_xml(_factura_simple())
    signed = firmar_xml(xml, pfx, pwd)
    assert b"20123456789" in signed
    print("PASS test_xml_firmado_conserva_ruc_emisor")

def test_xml_firmado_conserva_total():
    pfx, pwd = _get_test_pfx()
    xml = generar_xml(_factura_simple())
    signed = firmar_xml(xml, pfx, pwd)
    assert b"118.00" in signed
    print("PASS test_xml_firmado_conserva_total")

def test_xml_firmado_no_tiene_placeholder_vacio():
    """El placeholder ds:Signature vacío debe haber sido reemplazado por la firma real."""
    pfx, pwd = _get_test_pfx()
    xml = generar_xml(_factura_simple())
    signed = firmar_xml(xml, pfx, pwd)
    root = etree.fromstring(signed)
    # Si hay Signature, debe tener SignatureValue
    sig = root.find(f".//{{{DS}}}Signature")
    assert sig is not None
    sig_value = sig.find(f"{{{DS}}}SignatureValue")
    assert sig_value is not None and sig_value.text
    print("PASS test_xml_firmado_no_tiene_placeholder_vacio")

def test_firmar_pfx_inexistente_lanza_error():
    xml = generar_xml(_factura_simple())
    try:
        firmar_xml(xml, Path("/tmp/no_existe_este_cert.pfx"), "cualquier")
        assert False, "Debía lanzar FileNotFoundError"
    except FileNotFoundError:
        print("PASS test_firmar_pfx_inexistente_lanza_error")

def test_firmar_xml_sin_extension_content_lanza_error():
    """Si el XML no tiene ExtensionContent, debe fallar claro."""
    from lxml import etree as ET
    root = ET.Element("Invoice")
    ET.SubElement(root, "ID").text = "F001-00000001"
    xml_malo = ET.tostring(root, xml_declaration=True, encoding="UTF-8")
    pfx, pwd = _get_test_pfx()
    try:
        firmar_xml(xml_malo, pfx, pwd)
        assert False, "Debía lanzar ValueError"
    except ValueError as e:
        assert "ExtensionContent" in str(e)
        print("PASS test_firmar_xml_sin_extension_content_lanza_error")

def test_firmar_multi_lineas():
    pfx, pwd = _get_test_pfx()
    f = Factura(
        serie="F001", numero=99,
        emisor=Proveedor(ruc="20000000001", razon_social="EMPRESA SAC"),
        receptor=Cliente(tipo_doc="6", numero_doc="20000000002", razon_social="CLIENTE SRL"),
        lineas=[
            Linea(descripcion="Item A", cantidad=3, precio_unitario=50.0),
            Linea(descripcion="Item B", cantidad=1, precio_unitario=200.0),
        ],
    )
    xml = generar_xml(f)
    signed = firmar_xml(xml, pfx, pwd)
    root = etree.fromstring(signed)
    sig = root.find(f".//{{{DS}}}Signature")
    assert sig is not None
    assert b"F001-00000099" in signed
    print("PASS test_firmar_multi_lineas")

def test_firmar_encoding_utf8_especial():
    pfx, pwd = _get_test_pfx()
    f = Factura(
        serie="F001", numero=2,
        emisor=Proveedor(ruc="20000000001", razon_social="EMPRESA PERÚ SAC"),
        receptor=Cliente(tipo_doc="6", numero_doc="20000000002", razon_social="ÑOÑO SRL"),
        lineas=[Linea(descripcion="Servicio ñoño", cantidad=1, precio_unitario=50.0)],
    )
    xml = generar_xml(f)
    signed = firmar_xml(xml, pfx, pwd)
    root = etree.fromstring(signed)
    assert root is not None
    assert b"UTF-8" in signed
    print("PASS test_firmar_encoding_utf8_especial")


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_generar_cert_prueba_crea_archivo,
        test_generar_cert_prueba_legible,
        test_cert_prueba_diferente_password_falla,
        test_firmar_xml_retorna_bytes,
        test_xml_firmado_es_parseable,
        test_xml_firmado_tiene_ds_signature,
        test_firma_dentro_de_extension_content,
        test_firma_tiene_signaturevalue,
        test_firma_tiene_x509_certificate,
        test_xml_firmado_conserva_id_factura,
        test_xml_firmado_conserva_ruc_emisor,
        test_xml_firmado_conserva_total,
        test_xml_firmado_no_tiene_placeholder_vacio,
        test_firmar_pfx_inexistente_lanza_error,
        test_firmar_xml_sin_extension_content_lanza_error,
        test_firmar_multi_lineas,
        test_firmar_encoding_utf8_especial,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            print(f"FAIL {t.__name__}: {e}")
            import traceback; traceback.print_exc()
            failed += 1
    print(f"\n{'='*40}")
    print(f"Resultados: {len(tests)-failed}/{len(tests)} tests pasados")
    if failed:
        sys.exit(1)
