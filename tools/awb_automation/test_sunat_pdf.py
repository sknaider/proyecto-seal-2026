#!/usr/bin/env python3
"""Tests para sunat_pdf.py — generador de representación impresa SUNAT."""
import io
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sunat_ubl import Factura, Proveedor, Cliente, Linea, DireccionFiscal
from sunat_pdf import generar_pdf, monto_en_letras, _qr_contenido


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _factura_simple():
    return Factura(
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
            razon_social="CLIENTE SAC", email="cliente@test.com",
        ),
        lineas=[Linea(descripcion="Servicio de transporte", cantidad=1, precio_unitario=100.0)],
    )

def _factura_multi():
    return Factura(
        serie="F001", numero=99,
        emisor=Proveedor(ruc="20000000001", razon_social="EMPRESA PERÚ SAC"),
        receptor=Cliente(tipo_doc="6", numero_doc="20000000002", razon_social="CLIENTE SRL"),
        lineas=[
            Linea(descripcion="Producto A con descripción larga para ver el ajuste", cantidad=3, precio_unitario=200.0),
            Linea(descripcion="Producto B inafecto", cantidad=1, precio_unitario=500.0, afecto_igv=False),
        ],
    )


# ── Tests: monto_en_letras ────────────────────────────────────────────────────

def test_monto_letras_entero_simple():
    resultado = monto_en_letras(Decimal("118.00"))
    assert "CIENTO" in resultado
    assert "DIECIOCHO" in resultado
    assert "00/100" in resultado
    assert "SOLES" in resultado
    print("PASS test_monto_letras_entero_simple")

def test_monto_letras_cero():
    resultado = monto_en_letras(Decimal("0.00"))
    assert "CERO" in resultado
    print("PASS test_monto_letras_cero")

def test_monto_letras_centavos():
    resultado = monto_en_letras(Decimal("100.50"))
    assert "50/100" in resultado
    print("PASS test_monto_letras_centavos")

def test_monto_letras_miles():
    resultado = monto_en_letras(Decimal("1000.00"))
    assert "MIL" in resultado
    print("PASS test_monto_letras_miles")

def test_monto_letras_usd():
    resultado = monto_en_letras(Decimal("200.00"), moneda="USD")
    assert "DÓLARES AMERICANOS" in resultado
    print("PASS test_monto_letras_usd")

def test_monto_letras_354():
    resultado = monto_en_letras(Decimal("354.00"))
    assert "TRESCIENTOS" in resultado
    assert "CINCUENTA" in resultado
    assert "CUATRO" in resultado
    print("PASS test_monto_letras_354")

def test_monto_letras_millon():
    resultado = monto_en_letras(Decimal("1000000.00"))
    assert "MILLÓN" in resultado
    print("PASS test_monto_letras_millon")

def test_monto_letras_21():
    resultado = monto_en_letras(Decimal("21.00"))
    assert "VEINTIUNO" in resultado or ("VEINTE" in resultado and "UNO" in resultado) or "VEINTIÚN" in resultado or "VEINTI" in resultado
    print("PASS test_monto_letras_21")


# ── Tests: _qr_contenido ─────────────────────────────────────────────────────

def test_qr_contenido_formato():
    f = _factura_simple()
    qr = _qr_contenido(f)
    partes = qr.split("|")
    assert partes[0] == "20123456789"   # RUC emisor
    assert partes[1] == "01"            # tipo factura
    assert partes[2] == "F001"          # serie
    assert partes[3] == "1"             # numero
    assert partes[4] == "18.00"         # IGV
    assert partes[5] == "118.00"        # total
    assert partes[7] == "6"             # tipo doc receptor
    assert partes[8] == "20987654321"   # nro doc receptor
    print("PASS test_qr_contenido_formato")

def test_qr_contenido_termina_con_pipe():
    f = _factura_simple()
    qr = _qr_contenido(f)
    assert qr.endswith("|")
    print("PASS test_qr_contenido_termina_con_pipe")


# ── Tests: generar_pdf ────────────────────────────────────────────────────────

def test_generar_pdf_retorna_bytes():
    pdf = generar_pdf(_factura_simple())
    assert isinstance(pdf, bytes)
    assert len(pdf) > 0
    print("PASS test_generar_pdf_retorna_bytes")

def test_generar_pdf_es_pdf_valido():
    pdf = generar_pdf(_factura_simple())
    # Los PDFs comienzan con %PDF
    assert pdf[:4] == b"%PDF"
    print("PASS test_generar_pdf_es_pdf_valido")

def test_generar_pdf_tamano_razonable():
    pdf = generar_pdf(_factura_simple())
    # Un PDF de factura debe ser > 5KB y < 2MB
    assert 5_000 < len(pdf) < 2_000_000
    print("PASS test_generar_pdf_tamano_razonable")

def test_generar_pdf_multi_lineas():
    pdf = generar_pdf(_factura_multi())
    assert isinstance(pdf, bytes)
    assert pdf[:4] == b"%PDF"
    print("PASS test_generar_pdf_multi_lineas")

def test_generar_pdf_con_hash_firma():
    pdf = generar_pdf(_factura_simple(), hash_firma="Ab3xKl9mZ1xyz")
    assert isinstance(pdf, bytes)
    assert pdf[:4] == b"%PDF"
    print("PASS test_generar_pdf_con_hash_firma")

def test_generar_pdf_sin_hash_firma():
    pdf = generar_pdf(_factura_simple(), hash_firma=None)
    assert pdf[:4] == b"%PDF"
    print("PASS test_generar_pdf_sin_hash_firma")

def test_generar_pdf_logo_inexistente_no_falla():
    # Si logo_path no existe, no debe fallar — simplemente lo omite
    pdf = generar_pdf(_factura_simple(), logo_path=Path("/tmp/logo_no_existe.png"))
    assert pdf[:4] == b"%PDF"
    print("PASS test_generar_pdf_logo_inexistente_no_falla")

def test_generar_pdf_guardable():
    pdf = generar_pdf(_factura_simple())
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        ruta = Path(f.name)
    ruta.write_bytes(pdf)
    assert ruta.exists()
    assert ruta.stat().st_size > 5_000
    ruta.unlink()
    print("PASS test_generar_pdf_guardable")

def test_generar_pdf_utf8_especial():
    f = Factura(
        serie="F001", numero=2,
        emisor=Proveedor(ruc="20000000001", razon_social="EMPRESA PERÚ SAC"),
        receptor=Cliente(tipo_doc="6", numero_doc="20000000002", razon_social="ÑOÑO SRL"),
        lineas=[Linea(descripcion="Servicio ñoño con acentos", cantidad=1, precio_unitario=50.0)],
    )
    pdf = generar_pdf(f)
    assert pdf[:4] == b"%PDF"
    print("PASS test_generar_pdf_utf8_especial")

def test_generar_pdf_sin_nombre_comercial():
    f = Factura(
        serie="F001", numero=3,
        emisor=Proveedor(ruc="20000000001", razon_social="EMPRESA SIN COMERCIAL SAC"),
        receptor=Cliente(tipo_doc="6", numero_doc="20000000002", razon_social="CLIENTE"),
        lineas=[Linea(descripcion="Item", cantidad=1, precio_unitario=100.0)],
    )
    pdf = generar_pdf(f)
    assert pdf[:4] == b"%PDF"
    print("PASS test_generar_pdf_sin_nombre_comercial")

def test_generar_pdf_receptor_dni():
    f = Factura(
        serie="B001", numero=1,
        emisor=Proveedor(ruc="20000000001", razon_social="EMPRESA SAC"),
        receptor=Cliente(tipo_doc="1", numero_doc="12345678", razon_social="JUAN PÉREZ"),
        lineas=[Linea(descripcion="Servicio", cantidad=1, precio_unitario=50.0)],
    )
    pdf = generar_pdf(f)
    assert pdf[:4] == b"%PDF"
    print("PASS test_generar_pdf_receptor_dni")

def test_generar_pdf_moneda_usd():
    f = Factura(
        serie="F001", numero=5,
        emisor=Proveedor(ruc="20000000001", razon_social="EMPRESA SAC"),
        receptor=Cliente(tipo_doc="6", numero_doc="20000000002", razon_social="CLIENTE"),
        lineas=[Linea(descripcion="Servicio en dólares", cantidad=1, precio_unitario=1000.0)],
        moneda="USD",
    )
    pdf = generar_pdf(f)
    assert pdf[:4] == b"%PDF"
    print("PASS test_generar_pdf_moneda_usd")

def test_generar_pdf_linea_inafecta():
    f = Factura(
        serie="F001", numero=6,
        emisor=Proveedor(ruc="20000000001", razon_social="EMPRESA SAC"),
        receptor=Cliente(tipo_doc="6", numero_doc="20000000002", razon_social="CLIENTE"),
        lineas=[
            Linea(descripcion="Servicio afecto", cantidad=1, precio_unitario=100.0),
            Linea(descripcion="Servicio inafecto", cantidad=1, precio_unitario=200.0, afecto_igv=False),
        ],
    )
    pdf = generar_pdf(f)
    assert pdf[:4] == b"%PDF"
    print("PASS test_generar_pdf_linea_inafecta")


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_monto_letras_entero_simple,
        test_monto_letras_cero,
        test_monto_letras_centavos,
        test_monto_letras_miles,
        test_monto_letras_usd,
        test_monto_letras_354,
        test_monto_letras_millon,
        test_monto_letras_21,
        test_qr_contenido_formato,
        test_qr_contenido_termina_con_pipe,
        test_generar_pdf_retorna_bytes,
        test_generar_pdf_es_pdf_valido,
        test_generar_pdf_tamano_razonable,
        test_generar_pdf_multi_lineas,
        test_generar_pdf_con_hash_firma,
        test_generar_pdf_sin_hash_firma,
        test_generar_pdf_logo_inexistente_no_falla,
        test_generar_pdf_guardable,
        test_generar_pdf_utf8_especial,
        test_generar_pdf_sin_nombre_comercial,
        test_generar_pdf_receptor_dni,
        test_generar_pdf_moneda_usd,
        test_generar_pdf_linea_inafecta,
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
