#!/usr/bin/env python3
"""
sunat_signer.py — Firma digital XMLDSig para facturas electrónicas SUNAT Perú
Aplica firma RSA-SHA256 enveloped sobre XML UBL 2.1

El certificado va dentro de: UBLExtensions/UBLExtension/ExtensionContent/ds:Signature
conforme al Anexo 2 de la RS 097-2012/SUNAT.

Uso:
    from sunat_signer import firmar_xml, generar_cert_prueba

    # Con certificado real (Camerfirma, DigitSign, etc.)
    import os
    xml_firmado = firmar_xml(xml_bytes, pfx_path=Path("cert.pfx"), pfx_password=os.environ["PFX_PASSWORD"])

    # Para pruebas locales (genera cert autofirmado — NO válido para SUNAT producción)
    import secrets
    pfx, pwd = generar_cert_prueba(Path("/tmp/test_cert.pfx"), password=secrets.token_urlsafe(16))
    xml_firmado = firmar_xml(xml_bytes, pfx_path=pfx, pfx_password=pwd)
"""
from __future__ import annotations

import datetime
from pathlib import Path
from typing import Optional

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization.pkcs12 import (
    load_key_and_certificates,
    serialize_key_and_certificates,
)
from cryptography.x509.oid import NameOID
from lxml import etree
from signxml import XMLSigner, methods

# ── Namespaces ────────────────────────────────────────────────────────────────

EXT = "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"
DS  = "http://www.w3.org/2000/09/xmldsig#"


# ── Firma principal ───────────────────────────────────────────────────────────

def firmar_xml(
    xml_bytes: bytes,
    pfx_path: Path,
    pfx_password: str,
) -> bytes:
    """Firma el XML UBL 2.1 con el certificado .pfx y retorna el XML firmado.

    La firma XMLDSig queda dentro de ext:UBLExtensions/ext:UBLExtension/
    ext:ExtensionContent — posición requerida por SUNAT.

    Args:
        xml_bytes:    XML sin firmar (salida de sunat_ubl.generar_xml).
        pfx_path:     Ruta al archivo .pfx (PKCS#12).
        pfx_password: Contraseña del .pfx.

    Returns:
        XML firmado como bytes UTF-8.
    """
    # 1. Cargar clave y certificado del .pfx
    pfx_data = pfx_path.read_bytes()
    private_key, certificate, _ = load_key_and_certificates(
        pfx_data, pfx_password.encode("utf-8")
    )

    # Serializar a PEM para signxml
    key_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    cert_pem = certificate.public_bytes(serialization.Encoding.PEM)

    # 2. Parsear el XML
    root = etree.fromstring(xml_bytes)

    # 3. Quitar el placeholder ds:Signature dentro de ExtensionContent
    ext_content = root.find(f".//{{{EXT}}}ExtensionContent")
    if ext_content is None:
        raise ValueError("XML no tiene ext:ExtensionContent — no proviene de sunat_ubl.generar_xml")

    placeholder = ext_content.find(f"{{{DS}}}Signature")
    if placeholder is not None:
        ext_content.remove(placeholder)

    # 4. Firmar con signxml (enveloped — la firma se inserta como hijo del root)
    signer = XMLSigner(
        method=methods.enveloped,
        signature_algorithm="rsa-sha256",
        digest_algorithm="sha256",
        c14n_algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315",
    )
    signed_root = signer.sign(root, key=key_pem, cert=cert_pem)

    # 5. Mover la firma al interior de ExtensionContent (requisito SUNAT)
    sig_el = signed_root.find(f"{{{DS}}}Signature")
    if sig_el is not None:
        signed_root.remove(sig_el)
        # El Id debe coincidir con cac:Signature/cac:ExternalReference/cbc:URI="#SignatureSP"
        sig_el.set("Id", "SignatureSP")
        ext_content_signed = signed_root.find(f".//{{{EXT}}}ExtensionContent")
        if ext_content_signed is not None:
            ext_content_signed.append(sig_el)
        else:
            signed_root.append(sig_el)

    return etree.tostring(
        signed_root, xml_declaration=True, encoding="UTF-8", pretty_print=True
    )


# ── Generador de certificado de prueba ───────────────────────────────────────

def generar_cert_prueba(
    pfx_path: Path,
    password: str,
    razon_social: str = "EMPRESA DE PRUEBA SAC",
    ruc: str = "20000000001",
) -> tuple[Path, str]:
    """Genera un certificado RSA autofirmado para pruebas locales.

    No válido para SUNAT producción — solo para tests y desarrollo.

    Returns:
        (pfx_path, password) para pasar directamente a firmar_xml().
    """
    # Generar clave RSA 2048-bit
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # Construir certificado X.509
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "PE"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "LIMA"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "LIMA"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, razon_social),
        x509.NameAttribute(NameOID.COMMON_NAME, ruc),
    ])

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    # Serializar a .pfx (PKCS#12)
    pfx_bytes = serialize_key_and_certificates(
        name=razon_social.encode(),
        key=key,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(password.encode()),
    )

    pfx_path.write_bytes(pfx_bytes)
    return pfx_path, password
