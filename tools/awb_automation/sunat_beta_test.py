#!/usr/bin/env python3
"""
sunat_beta_test.py — Prueba de integración end-to-end contra SUNAT beta.

Uso:
    # Configura variables de entorno primero:
    export PFX_PATH="/ruta/a/cert.p12"
    export PFX_PWD="contraseña_del_p12"
    export RUC_EMISOR="20XXXXXXXXX"
    export SOL_USUARIO="USUARIO_SECUNDARIO"
    export SOL_CLAVE="clave_sol"

    # Parámetros opcionales de la empresa:
    export RAZON_SOCIAL="MI EMPRESA SAC"
    export DIRECCION="AV. PRINCIPAL 123"
    export DISTRITO="CHICLAYO"
    export PROVINCIA="CHICLAYO"

    python3 sunat_beta_test.py
"""
import os
import sys
from pathlib import Path
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).parent))

from sunat_ubl import (
    Factura, Proveedor, Cliente, Linea, DireccionFiscal, generar_xml,
)
from sunat_signer import firmar_xml
from sunat_soap import enviar_factura, CredencialesSOL, descripcion_cdr, guardar_cdr
from sunat_pdf import generar_pdf


def _requerir_env(nombre: str) -> str:
    val = os.environ.get(nombre, "").strip()
    if not val:
        print(f"ERROR: variable de entorno requerida: {nombre}")
        sys.exit(1)
    return val


def main():
    print("=" * 60)
    print("SUNAT BETA — Test de integración end-to-end")
    print("=" * 60)

    # ── Leer configuración desde entorno ──────────────────────────
    pfx_path  = Path(_requerir_env("PFX_PATH"))
    pfx_pwd   = _requerir_env("PFX_PWD")
    ruc       = _requerir_env("RUC_EMISOR")
    sol_user  = _requerir_env("SOL_USUARIO")
    sol_clave = _requerir_env("SOL_CLAVE")

    razon_social    = os.environ.get("RAZON_SOCIAL", "EMPRESA DE PRUEBA SAC")
    nombre_comercial = os.environ.get("NOMBRE_COMERCIAL", "")
    direccion_str   = os.environ.get("DIRECCION", "AV. PRINCIPAL 123")
    distrito        = os.environ.get("DISTRITO", "CHICLAYO")
    provincia       = os.environ.get("PROVINCIA", "CHICLAYO")
    departamento    = os.environ.get("DEPARTAMENTO", "LAMBAYEQUE")
    ubigeo          = os.environ.get("UBIGEO", "140101")

    # ── Construir factura de prueba ────────────────────────────────
    # RUC de cliente ficticio aceptado por SUNAT beta
    factura = Factura(
        serie="F001",
        numero=int(os.environ.get("NUMERO", "1")),
        emisor=Proveedor(
            ruc=ruc,
            razon_social=razon_social,
            nombre_comercial=nombre_comercial or None,
            direccion=DireccionFiscal(
                ubigeo=ubigeo,
                departamento=departamento,
                provincia=provincia,
                distrito=distrito,
                direccion=direccion_str,
            ),
        ),
        receptor=Cliente(
            tipo_doc="6",
            numero_doc=os.environ.get("RUC_RECEPTOR", "20000000001"),
            razon_social=os.environ.get("RECEPTOR_NOMBRE", "CLIENTE DE PRUEBA SAC"),
        ),
        lineas=[
            Linea(
                descripcion=os.environ.get("DESCRIPCION", "Servicio de prueba de integración SUNAT"),
                cantidad=1,
                precio_unitario=float(os.environ.get("PRECIO", "100.0")),
            )
        ],
    )

    print(f"\n📄 Factura: {factura.id}")
    print(f"   Emisor : {ruc} — {razon_social}")
    print(f"   Total  : S/ {factura.total}")
    print(f"   IGV    : S/ {factura.total_igv}")

    # ── Paso 1: Generar XML ────────────────────────────────────────
    print("\n[1/4] Generando XML UBL 2.1...")
    xml = generar_xml(factura)
    out_xml_unsigned = Path(f"/tmp/{factura.id}_sin_firma.xml")
    out_xml_unsigned.write_bytes(xml)
    print(f"      → {out_xml_unsigned} ({len(xml):,} bytes)")

    # ── Paso 2: Firmar ────────────────────────────────────────────
    print(f"\n[2/4] Firmando con certificado: {pfx_path.name}...")
    if not pfx_path.exists():
        print(f"ERROR: No se encuentra el certificado: {pfx_path}")
        sys.exit(1)

    xml_firmado = firmar_xml(xml, pfx_path, pfx_pwd)
    out_xml_signed = Path(f"/tmp/{factura.id}_firmado.xml")
    out_xml_signed.write_bytes(xml_firmado)
    print(f"      → {out_xml_signed} ({len(xml_firmado):,} bytes)")

    # ── Paso 3: Enviar a SUNAT beta ───────────────────────────────
    print(f"\n[3/4] Enviando a SUNAT beta...")
    creds = CredencialesSOL(
        ruc=ruc,
        usuario_sol=sol_user,
        clave_sol=sol_clave,
    )

    try:
        cdr = enviar_factura(
            xml_firmado=xml_firmado,
            ruc_emisor=ruc,
            serie=factura.serie,
            numero=factura.numero,
            credenciales=creds,
            tipo="factura",
            beta=True,
        )
    except Exception as e:
        print(f"\n❌ Error al enviar a SUNAT: {type(e).__name__}: {e}")
        print("\n   Si el error es 'HTTPError 401' → credenciales SOL incorrectas")
        print("   Si el error es 'HTTPError 500' → error en el XML (revisar formato)")
        print("   Si el error es 'ValueError: SOAP Fault' → SUNAT rechazó la petición")
        sys.exit(1)

    # ── Paso 4: Mostrar resultado CDR ─────────────────────────────
    print(f"\n[4/4] CDR recibido:")
    print(f"\n{'='*60}")
    print(f"  Estado    : {'✅ ACEPTADA' if cdr.aceptado else '❌ RECHAZADA'}")
    print(f"  Código    : {cdr.codigo_respuesta}")
    print(f"  Descripción: {cdr.descripcion}")
    if cdr.observaciones:
        print("  Observaciones:")
        for obs in cdr.observaciones:
            print(f"    ⚠ {obs}")
    print(f"{'='*60}")

    # Guardar CDR
    out_cdr = Path(f"/tmp/CDR-{factura.id}.xml")
    guardar_cdr(cdr, out_cdr)
    print(f"\n  CDR guardado: {out_cdr}")

    # Generar PDF si fue aceptada
    if cdr.aceptado:
        print("\n[PDF] Generando representación impresa...")
        pdf = generar_pdf(factura)
        out_pdf = Path(f"/tmp/{factura.id}.pdf")
        out_pdf.write_bytes(pdf)
        print(f"      → {out_pdf} ({len(pdf):,} bytes)")
        print(f"\n✅ Prueba beta completada exitosamente.")
        print(f"   La factura {factura.id} fue aceptada por SUNAT.")
    else:
        print(f"\n❌ SUNAT rechazó la factura. Código: {cdr.codigo_respuesta}")
        print(f"   Motivo: {cdr.descripcion}")
        print("\n   Códigos comunes:")
        print("   2108 → Error en firma digital (certificado no reconocido en beta)")
        print("   2075 → RUC del emisor no está habilitado para emisión electrónica")
        print("   4000 → Error de estructura en el XML")
        sys.exit(1)


if __name__ == "__main__":
    main()
