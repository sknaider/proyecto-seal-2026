#!/usr/bin/env python3
"""
sunat_soap.py — Cliente SOAP para el webservice de SUNAT (billService)
Envía facturas electrónicas firmadas y parsea el CDR de respuesta.

Flujo completo:
    xml_firmado  = firmar_xml(xml, pfx_path, pfx_password)
    cdr          = enviar_factura(xml_firmado, factura, credenciales)
    print(cdr.aceptado, cdr.descripcion)

Referencia: RS 097-2012/SUNAT + manual del programador SUNAT 2.0
"""
from __future__ import annotations

import base64
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests
from lxml import etree

_SERIE_RE = re.compile(r"^[A-Z][A-Z0-9]{0,3}$")


# ── Endpoints SUNAT ───────────────────────────────────────────────────────────

SUNAT_BETA = (
    "https://e-beta.sunat.gob.pe/ol-ti-itcpfegem-beta/billService"
)
SUNAT_PROD = (
    "https://e-factura.sunat.gob.pe/ol-ti-itcpfegem/billService"
)

# NubeFacT OSE (Operador de Servicios Electrónicos)
OSE_NUBEFACT_BETA = (
    "https://demo-ose.nubefact.com/ol-ti-itcpe/billService"
)
OSE_NUBEFACT_PROD = (
    "https://ose.nubefact.com/ol-ti-itcpe/billService"
)

# Namespace del servicio SUNAT
_NS_SERVICE = "http://service.sunat.gob.pe"
_NS_SOAP    = "http://schemas.xmlsoap.org/soap/envelope/"

# Tipo de documento UBL → código SUNAT
TIPO_DOC = {
    "factura": "01",
    "boleta":  "03",
    "nota_credito": "07",
    "nota_debito":  "08",
}


# ── Estructuras de datos ───────────────────────────────────────────────────────

@dataclass
class CredencialesSOL:
    """Credenciales para autenticación en el webservice SUNAT.

    El username SUNAT es: RUC + código de usuario SOL (sin guión).
    Ejemplo: ruc="20123456789", usuario_sol="USUARIO01"
    → username final = "20123456789USUARIO01"
    """
    ruc: str
    usuario_sol: str
    clave_sol: str = field(repr=False)

    @property
    def username(self) -> str:
        return f"{self.ruc}{self.usuario_sol}"


@dataclass
class CDR:
    """Constancia de Recepción parseada."""
    aceptado: bool
    codigo_respuesta: str
    descripcion: str
    observaciones: list[str] = field(default_factory=list)
    xml_cdr_raw: bytes = field(default=b"", repr=False)


# ── Empaquetado ZIP ────────────────────────────────────────────────────────────

def _nombre_archivo(ruc: str, tipo: str, serie: str, numero: int) -> str:
    """Genera el nombre de archivo SUNAT: {RUC}-{tipo}-{serie}-{numero}."""
    codigo = TIPO_DOC.get(tipo, "01")
    return f"{ruc}-{codigo}-{serie}-{numero:08d}"


def _empaquetar_zip(xml_bytes: bytes, nombre_base: str) -> bytes:
    """Empaqueta el XML firmado en un ZIP con el nombre correcto para SUNAT."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{nombre_base}.xml", xml_bytes)
    return buf.getvalue()


# ── WS-Security namespace ─────────────────────────────────────────────────────

_NS_WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
_NS_PWD_TEXT = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordText"


# ── Envelope SOAP ─────────────────────────────────────────────────────────────

def _build_soap_envelope(
    nombre_zip: str,
    contenido_b64: str,
    wss_username: Optional[str] = None,
    wss_password: Optional[str] = None,
    wss_password_type: bool = False,
) -> bytes:
    """Construye el envelope SOAP para sendBill.

    wss_username/wss_password: incluye WS-Security UsernameToken.
    wss_password_type: True → agrega Type al elemento Password (requerido por OSEs).
    """
    if wss_username and wss_password:
        pwd_type_attr = f' Type="{_NS_PWD_TEXT}"' if wss_password_type else ""
        header = f"""  <soapenv:Header>
    <wsse:Security xmlns:wsse="{_NS_WSSE}">
      <wsse:UsernameToken>
        <wsse:Username>{wss_username}</wsse:Username>
        <wsse:Password{pwd_type_attr}>{wss_password}</wsse:Password>
      </wsse:UsernameToken>
    </wsse:Security>
  </soapenv:Header>"""
    else:
        header = "  <soapenv:Header/>"

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope
  xmlns:soapenv="{_NS_SOAP}"
  xmlns:ser="{_NS_SERVICE}">
{header}
  <soapenv:Body>
    <ser:sendBill>
      <fileName>{nombre_zip}</fileName>
      <contentFile>{contenido_b64}</contentFile>
    </ser:sendBill>
  </soapenv:Body>
</soapenv:Envelope>""".encode("utf-8")


# ── Parser CDR ────────────────────────────────────────────────────────────────

_NS_AR = {
    "ar":  "urn:oasis:names:specification:ubl:schema:xsd:ApplicationResponse-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
}


def _parsear_cdr(cdr_zip_bytes: bytes) -> CDR:
    """Extrae y parsea el XML CDR desde el ZIP de respuesta SUNAT."""
    with zipfile.ZipFile(io.BytesIO(cdr_zip_bytes)) as zf:
        nombres = zf.namelist()
        # El CDR tiene prefijo "R-" en el nombre
        cdr_name = next((n for n in nombres if n.startswith("R-")), nombres[0])
        cdr_xml = zf.read(cdr_name)

    root = etree.fromstring(cdr_xml)

    # ResponseCode dentro de DocumentResponse/Response
    doc_resp = root.find(".//cac:DocumentResponse/cac:Response", _NS_AR)
    codigo = ""
    descripcion = ""
    if doc_resp is not None:
        code_el = doc_resp.find("cbc:ResponseCode", _NS_AR)
        desc_el = doc_resp.find("cbc:Description", _NS_AR)
        codigo = (code_el.text or "").strip() if code_el is not None else ""
        descripcion = (desc_el.text or "").strip() if desc_el is not None else ""

    # Observaciones (notas de línea)
    observaciones = [
        (n.text or "").strip()
        for n in root.findall(".//cac:DocumentResponse/cac:LineResponse/cac:Response/cbc:Description", _NS_AR)
        if n.text
    ]

    # Código "0" = aceptado; "2xxx" = aceptado con observaciones; "4xxx" = error
    aceptado = codigo in ("0",) or codigo.startswith("2")

    return CDR(
        aceptado=aceptado,
        codigo_respuesta=codigo,
        descripcion=descripcion,
        observaciones=observaciones,
        xml_cdr_raw=cdr_xml,
    )


# ── Función principal ──────────────────────────────────────────────────────────

def enviar_factura(
    xml_firmado: bytes,
    ruc_emisor: str,
    serie: str,
    numero: int,
    credenciales: CredencialesSOL,
    tipo: str = "factura",
    beta: bool = True,
    timeout: int = 30,
    ose_url: Optional[str] = None,
) -> CDR:
    """Envía una factura electrónica firmada al webservice SUNAT.

    Args:
        xml_firmado:   Bytes del XML UBL 2.1 ya firmado (salida de firmar_xml).
        ruc_emisor:    RUC de la empresa emisora (11 dígitos).
        serie:         Serie de la factura (ej. "F001").
        numero:        Número correlativo de la factura (ej. 1).
        credenciales:  Credenciales SOL del emisor.
        tipo:          Tipo de comprobante: "factura", "boleta", etc.
        beta:          True = endpoint beta SUNAT (para pruebas). False = producción.
        timeout:       Timeout HTTP en segundos.

    Returns:
        CDR con aceptado=True si SUNAT aceptó la factura.

    Raises:
        requests.HTTPError:  Si el servidor SUNAT retorna error HTTP.
        ValueError:          Si la respuesta SOAP no es parseable.
        zipfile.BadZipFile:  Si el CDR recibido no es un ZIP válido.
    """
    if not _SERIE_RE.match(serie):
        raise ValueError(f"serie inválida: {serie!r} — debe ser [A-Z][A-Z0-9]{{0,3}} (ej. F001, B001)")
    endpoint = ose_url if ose_url else (SUNAT_BETA if beta else SUNAT_PROD)
    nombre_base = _nombre_archivo(ruc_emisor, tipo, serie, numero)
    nombre_zip  = f"{nombre_base}.zip"

    zip_bytes  = _empaquetar_zip(xml_firmado, nombre_base)
    contenido_b64 = base64.b64encode(zip_bytes).decode("ascii")

    # WS-Security siempre requerido. OSEs necesitan Type attribute; SUNAT directo no.
    envelope = _build_soap_envelope(
        nombre_zip, contenido_b64,
        wss_username=credenciales.username,
        wss_password=credenciales.clave_sol,
        wss_password_type=bool(ose_url),
    )

    # OSEs también requieren HTTP Basic Auth además de WS-Security
    auth = (credenciales.username, credenciales.clave_sol) if ose_url else None

    resp = requests.post(
        endpoint,
        data=envelope,
        auth=auth,
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": "urn:sendBill",
        },
        timeout=timeout,
        verify=True,
    )
    # SUNAT devuelve HTTP 500 para SOAP faults — intentar parsear antes de raise
    if resp.status_code not in (200, 500):
        resp.raise_for_status()

    cdr_zip_b64 = _extraer_application_response(resp.content)
    cdr_zip_bytes = base64.b64decode(cdr_zip_b64)
    return _parsear_cdr(cdr_zip_bytes)


def _extraer_application_response(soap_response: bytes) -> str:
    """Extrae el base64 del applicationResponse del envelope SOAP de respuesta."""
    try:
        root = etree.fromstring(soap_response)
    except etree.XMLSyntaxError as e:
        raise ValueError(f"Respuesta SOAP no es XML válido: {e}") from e

    # SUNAT puede retornar faults con detalle de error
    fault = root.find(".//{%s}Fault" % _NS_SOAP)
    if fault is not None:
        faultstring = fault.findtext("faultstring") or ""
        detail = fault.findtext("detail") or ""
        raise ValueError(f"SUNAT SOAP Fault: {faultstring} — {detail}")

    # applicationResponse es el campo de respuesta para sendBill
    ns = {"ns": _NS_SERVICE}
    ar = root.find(".//applicationResponse")
    if ar is None:
        # Intentar sin namespace
        ar = root.find(".//{%s}applicationResponse" % _NS_SERVICE)
    if ar is None or not ar.text:
        raise ValueError(
            "Respuesta SOAP de SUNAT no contiene applicationResponse. "
            f"Raw: {soap_response[:500]}"
        )
    return ar.text.strip()


# ── Helpers de diagnóstico ────────────────────────────────────────────────────

def guardar_cdr(cdr: CDR, ruta: Path) -> None:
    """Guarda el XML CDR en disco para auditoría."""
    ruta.write_bytes(cdr.xml_cdr_raw)


def descripcion_cdr(cdr: CDR) -> str:
    """Retorna un string legible del resultado CDR."""
    estado = "ACEPTADA" if cdr.aceptado else "RECHAZADA"
    lineas = [f"[{estado}] Código {cdr.codigo_respuesta}: {cdr.descripcion}"]
    for obs in cdr.observaciones:
        lineas.append(f"  ⚠ {obs}")
    return "\n".join(lineas)
