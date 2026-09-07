#!/usr/bin/env python3
"""
test_sunat_beta.py — Test de integración contra el ambiente beta de SUNAT.

Uso:
    export PFX_PATH=/ruta/a/tu_cert.p12
    export PFX_PASSWORD=tu_contraseña_p12
    export RUC_EMISOR=20XXXXXXXXX
    export USUARIO_SOL=USUARIO01          # usuario SOL secundario
    export CLAVE_SOL=tu_clave_sol
    export RAZON_SOCIAL="MI EMPRESA SAC"  # opcional, default usa env

    python3 test_sunat_beta.py
"""
import os
import sys
from pathlib import Path

# ── Validar env vars requeridas ───────────────────────────────────────────────

REQUIRED = ["PFX_PATH", "PFX_PASSWORD", "RUC_EMISOR", "USUARIO_SOL", "CLAVE_SOL"]
missing = [v for v in REQUIRED if not os.environ.get(v)]
if missing:
    print(f"ERROR: faltan variables de entorno: {', '.join(missing)}")
    print(__doc__)
    sys.exit(1)

pfx_path     = Path(os.environ["PFX_PATH"])
pfx_password = os.environ["PFX_PASSWORD"]
ruc_emisor   = os.environ["RUC_EMISOR"]
usuario_sol  = os.environ["USUARIO_SOL"]
clave_sol    = os.environ["CLAVE_SOL"]
razon_social = os.environ.get("RAZON_SOCIAL", "EMPRESA DE PRUEBA SAC")

if not pfx_path.exists():
    print(f"ERROR: no se encontró el archivo .p12 en: {pfx_path}")
    sys.exit(1)

if len(ruc_emisor) != 11 or not ruc_emisor.isdigit():
    print(f"ERROR: RUC_EMISOR debe tener exactamente 11 dígitos: {ruc_emisor!r}")
    sys.exit(1)

# ── Importar módulos SUNAT ────────────────────────────────────────────────────

from sunat_ubl import (
    Factura, Proveedor, Cliente, Linea,
    DireccionFiscal, generar_xml,
)
from sunat_signer import firmar_xml
from sunat_soap import CredencialesSOL, enviar_factura, descripcion_cdr

# ── Armar factura de prueba ───────────────────────────────────────────────────

print(f"\n{'='*55}")
print(f"  TEST BETA SUNAT — {ruc_emisor}")
print(f"{'='*55}\n")

factura = Factura(
    serie="F001",
    numero=1,
    tipo="FACTURA",
    moneda="PEN",
    emisor=Proveedor(
        ruc=ruc_emisor,
        razon_social=razon_social,
        nombre_comercial=razon_social,
        direccion=DireccionFiscal(
            ubigeo="140101",
            departamento="LAMBAYEQUE",
            provincia="CHICLAYO",
            distrito="CHICLAYO",
            direccion="AV. PRUEBA 123",
        ),
    ),
    receptor=Cliente(
        tipo_doc="6",
        numero_doc="20100039207",  # RUC SUNAT (receptor de prueba)
        razon_social="EMPRESA RECEPTORA SAC",
    ),
    lineas=[
        Linea(
            descripcion="Servicio de prueba SUNAT beta",
            cantidad=1,
            precio_unitario=100.00,
            unidad="ZZ",
        )
    ],
    observaciones="Factura de prueba — ambiente beta SUNAT",
)

if not factura.lineas:
    print("ERROR: la factura no tiene líneas — SUNAT rechazaría con totales 0.00")
    sys.exit(1)

print(f"Factura:    {factura.id}")
print(f"Emisor:     {ruc_emisor} — {razon_social}")
print(f"Total:      S/. {factura.total}")
print(f"Certificado: {pfx_path.name}\n")

# ── Paso 1: Generar XML ───────────────────────────────────────────────────────

print("Paso 1/3 — Generando XML UBL 2.1...", end=" ", flush=True)
xml_bytes = generar_xml(factura)
print(f"OK ({len(xml_bytes):,} bytes)")

# ── Paso 2: Firmar con .p12 ───────────────────────────────────────────────────

print("Paso 2/3 — Firmando con certificado .p12...", end=" ", flush=True)
try:
    xml_firmado = firmar_xml(xml_bytes, pfx_path=pfx_path, pfx_password=pfx_password)
    print(f"OK ({len(xml_firmado):,} bytes)")
except Exception as e:
    print(f"ERROR\n  {e}")
    sys.exit(1)

# ── Paso 3: Enviar a SUNAT beta ───────────────────────────────────────────────

print("Paso 3/3 — Enviando a SUNAT beta...", end=" ", flush=True)
creds = CredencialesSOL(
    ruc=ruc_emisor,
    usuario_sol=usuario_sol,
    clave_sol=clave_sol,
)
try:
    cdr = enviar_factura(
        xml_firmado,
        ruc_emisor=ruc_emisor,
        serie="F001",
        numero=1,
        credenciales=creds,
        tipo="factura",
        beta=True,
        timeout=30,
    )
    print("OK\n")
    print(f"{'='*55}")
    print(f"  RESULTADO CDR")
    print(f"{'='*55}")
    print(descripcion_cdr(cdr))
    if cdr.aceptado:
        print("\n✅ ACEPTADA — firma y conexión SUNAT OK. Listo para producción.")
    else:
        print(f"\n❌ RECHAZADA — revisar código {cdr.codigo_respuesta}")
    print(f"{'='*55}\n")
except Exception as e:
    print(f"ERROR\n")
    print(f"{'='*55}")
    print(f"  ERROR AL ENVIAR")
    print(f"{'='*55}")
    print(f"  {type(e).__name__}: {e}")
    print(f"{'='*55}\n")
    sys.exit(1)
