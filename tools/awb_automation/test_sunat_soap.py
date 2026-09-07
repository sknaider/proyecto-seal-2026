#!/usr/bin/env python3
"""Tests para sunat_soap.py — cliente SOAP SUNAT facturación electrónica."""
import base64
import io
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent))
from sunat_soap import (
    CredencialesSOL,
    CDR,
    SUNAT_BETA,
    SUNAT_PROD,
    TIPO_DOC,
    _nombre_archivo,
    _empaquetar_zip,
    _build_soap_envelope,
    _parsear_cdr,
    _extraer_application_response,
    enviar_factura,
    guardar_cdr,
    descripcion_cdr,
)


# ── Helpers de fixture ────────────────────────────────────────────────────────

def _cdr_xml(codigo: str = "0", descripcion: str = "Factura aceptada") -> bytes:
    """Genera un XML CDR SUNAT mínimo válido."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ApplicationResponse
  xmlns="urn:oasis:names:specification:ubl:schema:xsd:ApplicationResponse-2"
  xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
  xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2">
  <cac:DocumentResponse>
    <cac:Response>
      <cbc:ResponseCode>{codigo}</cbc:ResponseCode>
      <cbc:Description>{descripcion}</cbc:Description>
    </cac:Response>
  </cac:DocumentResponse>
</ApplicationResponse>""".encode("utf-8")


def _cdr_xml_con_obs(codigo: str = "0", descripcion: str = "Aceptada con obs") -> bytes:
    """CDR con observaciones de línea."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ApplicationResponse
  xmlns="urn:oasis:names:specification:ubl:schema:xsd:ApplicationResponse-2"
  xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
  xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2">
  <cac:DocumentResponse>
    <cac:Response>
      <cbc:ResponseCode>{codigo}</cbc:ResponseCode>
      <cbc:Description>{descripcion}</cbc:Description>
    </cac:Response>
    <cac:LineResponse>
      <cac:Response>
        <cbc:Description>Observacion linea 1</cbc:Description>
      </cac:Response>
    </cac:LineResponse>
    <cac:LineResponse>
      <cac:Response>
        <cbc:Description>Observacion linea 2</cbc:Description>
      </cac:Response>
    </cac:LineResponse>
  </cac:DocumentResponse>
</ApplicationResponse>""".encode("utf-8")


def _hacer_cdr_zip(cdr_xml: bytes, nombre_base: str) -> bytes:
    """Empaqueta CDR XML en ZIP con prefijo R- (formato SUNAT)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"R-{nombre_base}.xml", cdr_xml)
    return buf.getvalue()


def _soap_response_ok(cdr_zip: bytes) -> bytes:
    """Genera un envelope SOAP de respuesta OK de SUNAT."""
    b64 = base64.b64encode(cdr_zip).decode("ascii")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/">
  <S:Body>
    <ns2:sendBillResponse xmlns:ns2="http://service.sunat.gob.pe">
      <applicationResponse>{b64}</applicationResponse>
    </ns2:sendBillResponse>
  </S:Body>
</S:Envelope>""".encode("utf-8")


def _soap_fault(mensaje: str = "Error de autenticación") -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/">
  <S:Body>
    <S:Fault>
      <faultcode>S:Server</faultcode>
      <faultstring>{mensaje}</faultstring>
      <detail>Detalle del error SUNAT</detail>
    </S:Fault>
  </S:Body>
</S:Envelope>""".encode("utf-8")


def _credenciales():
    return CredencialesSOL(
        ruc="20123456789",
        usuario_sol="MODDATOS",
        clave_sol="moddatos",
    )


# ── Tests: CredencialesSOL ────────────────────────────────────────────────────

def test_credenciales_username():
    c = CredencialesSOL(ruc="20123456789", usuario_sol="MODDATOS", clave_sol="moddatos")
    assert c.username == "20123456789MODDATOS"
    print("PASS test_credenciales_username")

def test_credenciales_campos():
    c = CredencialesSOL(ruc="20000000001", usuario_sol="USR01", clave_sol="clave")
    assert c.ruc == "20000000001"
    assert c.usuario_sol == "USR01"
    assert c.clave_sol == "clave"
    print("PASS test_credenciales_campos")


# ── Tests: endpoints ──────────────────────────────────────────────────────────

def test_endpoints_definidos():
    assert "e-beta.sunat.gob.pe" in SUNAT_BETA
    assert "e-factura.sunat.gob.pe" in SUNAT_PROD
    print("PASS test_endpoints_definidos")

def test_tipos_documento():
    assert TIPO_DOC["factura"] == "01"
    assert TIPO_DOC["boleta"] == "03"
    assert TIPO_DOC["nota_credito"] == "07"
    assert TIPO_DOC["nota_debito"] == "08"
    print("PASS test_tipos_documento")


# ── Tests: _nombre_archivo ────────────────────────────────────────────────────

def test_nombre_archivo_factura():
    nombre = _nombre_archivo("20123456789", "factura", "F001", 1)
    assert nombre == "20123456789-01-F001-00000001"
    print("PASS test_nombre_archivo_factura")

def test_nombre_archivo_boleta():
    nombre = _nombre_archivo("20000000001", "boleta", "B001", 999)
    assert nombre == "20000000001-03-B001-00000999"
    print("PASS test_nombre_archivo_boleta")

def test_nombre_archivo_correlativo_grande():
    nombre = _nombre_archivo("20123456789", "factura", "F001", 99999999)
    assert nombre == "20123456789-01-F001-99999999"
    print("PASS test_nombre_archivo_correlativo_grande")

def test_nombre_archivo_tipo_desconocido():
    # Tipo desconocido debe caer en "01" (default)
    nombre = _nombre_archivo("20000000001", "otro", "X001", 1)
    assert "-01-" in nombre
    print("PASS test_nombre_archivo_tipo_desconocido")


# ── Tests: _empaquetar_zip ────────────────────────────────────────────────────

def test_empaquetar_zip_es_zip_valido():
    xml = b"<Invoice>test</Invoice>"
    zip_bytes = _empaquetar_zip(xml, "20123456789-01-F001-00000001")
    buf = io.BytesIO(zip_bytes)
    assert zipfile.is_zipfile(buf)
    print("PASS test_empaquetar_zip_es_zip_valido")

def test_empaquetar_zip_contiene_xml():
    xml = b"<Invoice>test content</Invoice>"
    nombre = "20123456789-01-F001-00000001"
    zip_bytes = _empaquetar_zip(xml, nombre)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        assert f"{nombre}.xml" in zf.namelist()
        assert zf.read(f"{nombre}.xml") == xml
    print("PASS test_empaquetar_zip_contiene_xml")

def test_empaquetar_zip_solo_un_archivo():
    zip_bytes = _empaquetar_zip(b"test", "nombre")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        assert len(zf.namelist()) == 1
    print("PASS test_empaquetar_zip_solo_un_archivo")


# ── Tests: _build_soap_envelope ───────────────────────────────────────────────

def test_build_soap_envelope_es_xml():
    from lxml import etree
    env = _build_soap_envelope("test.zip", "BASE64CONTENT")
    root = etree.fromstring(env)
    assert root is not None
    print("PASS test_build_soap_envelope_es_xml")

def test_build_soap_envelope_contiene_filename():
    env = _build_soap_envelope("20123456789-01-F001-00000001.zip", "BASE64")
    assert b"20123456789-01-F001-00000001.zip" in env
    print("PASS test_build_soap_envelope_contiene_filename")

def test_build_soap_envelope_contiene_contenido():
    env = _build_soap_envelope("test.zip", "MIBASE64CONTENT")
    assert b"MIBASE64CONTENT" in env
    print("PASS test_build_soap_envelope_contiene_contenido")

def test_build_soap_envelope_namespace_sunat():
    env = _build_soap_envelope("t.zip", "b")
    assert b"service.sunat.gob.pe" in env
    print("PASS test_build_soap_envelope_namespace_sunat")

def test_build_soap_envelope_send_bill():
    env = _build_soap_envelope("t.zip", "b")
    assert b"sendBill" in env
    print("PASS test_build_soap_envelope_send_bill")


# ── Tests: _parsear_cdr ───────────────────────────────────────────────────────

def test_parsear_cdr_aceptado():
    cdr_xml = _cdr_xml("0", "La Factura F001-1 ha sido aceptada")
    cdr_zip = _hacer_cdr_zip(cdr_xml, "20123456789-01-F001-00000001")
    cdr = _parsear_cdr(cdr_zip)
    assert cdr.aceptado is True
    assert cdr.codigo_respuesta == "0"
    assert "aceptada" in cdr.descripcion.lower()
    print("PASS test_parsear_cdr_aceptado")

def test_parsear_cdr_rechazado():
    cdr_xml = _cdr_xml("4000", "El valor del IGV es incorrecto")
    cdr_zip = _hacer_cdr_zip(cdr_xml, "20123456789-01-F001-00000001")
    cdr = _parsear_cdr(cdr_zip)
    assert cdr.aceptado is False
    assert cdr.codigo_respuesta == "4000"
    print("PASS test_parsear_cdr_rechazado")

def test_parsear_cdr_con_observaciones():
    cdr_xml = _cdr_xml_con_obs("0", "Aceptada con observaciones")
    cdr_zip = _hacer_cdr_zip(cdr_xml, "20123456789-01-F001-00000001")
    cdr = _parsear_cdr(cdr_zip)
    assert cdr.aceptado is True
    assert len(cdr.observaciones) == 2
    assert "Observacion linea 1" in cdr.observaciones
    print("PASS test_parsear_cdr_con_observaciones")

def test_parsear_cdr_raw_bytes():
    cdr_xml = _cdr_xml("0", "OK")
    cdr_zip = _hacer_cdr_zip(cdr_xml, "20123456789-01-F001-00000001")
    cdr = _parsear_cdr(cdr_zip)
    assert len(cdr.xml_cdr_raw) > 0
    assert b"ApplicationResponse" in cdr.xml_cdr_raw
    print("PASS test_parsear_cdr_raw_bytes")

def test_parsear_cdr_codigo_2xxx_es_aceptado():
    # Códigos 2xxx = aceptado con observaciones
    cdr_xml = _cdr_xml("2001", "Aceptada con advertencia")
    cdr_zip = _hacer_cdr_zip(cdr_xml, "20123456789-01-F001-00000001")
    cdr = _parsear_cdr(cdr_zip)
    assert cdr.aceptado is True
    print("PASS test_parsear_cdr_codigo_2xxx_es_aceptado")


# ── Tests: _extraer_application_response ─────────────────────────────────────

def test_extraer_application_response_ok():
    cdr_xml = _cdr_xml("0", "OK")
    cdr_zip = _hacer_cdr_zip(cdr_xml, "nombre")
    b64_esperado = base64.b64encode(cdr_zip).decode("ascii")
    soap_resp = _soap_response_ok(cdr_zip)
    b64_extraido = _extraer_application_response(soap_resp)
    assert b64_extraido == b64_esperado
    print("PASS test_extraer_application_response_ok")

def test_extraer_application_response_fault_lanza_error():
    soap_resp = _soap_fault("Credenciales incorrectas")
    try:
        _extraer_application_response(soap_resp)
        assert False, "Debía lanzar ValueError"
    except ValueError as e:
        assert "SOAP Fault" in str(e) or "Fault" in str(e)
        print("PASS test_extraer_application_response_fault_lanza_error")

def test_extraer_application_response_xml_malo():
    try:
        _extraer_application_response(b"no es xml")
        assert False, "Debía lanzar ValueError"
    except ValueError:
        print("PASS test_extraer_application_response_xml_malo")

def test_extraer_application_response_sin_ar_lanza_error():
    soap_sin_ar = b"""<?xml version="1.0"?>
<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/">
  <S:Body><respuesta>sin campo</respuesta></S:Body>
</S:Envelope>"""
    try:
        _extraer_application_response(soap_sin_ar)
        assert False, "Debía lanzar ValueError"
    except ValueError as e:
        assert "applicationResponse" in str(e)
        print("PASS test_extraer_application_response_sin_ar_lanza_error")


# ── Tests: enviar_factura (con mock) ──────────────────────────────────────────

def _xml_firmado_mock() -> bytes:
    return b"<?xml version='1.0' encoding='UTF-8'?><Invoice><ID>F001-00000001</ID></Invoice>"


def test_enviar_factura_aceptada():
    cdr_xml = _cdr_xml("0", "La Factura F001-1 ha sido aceptada")
    cdr_zip = _hacer_cdr_zip(cdr_xml, "20123456789-01-F001-00000001")
    soap_resp = _soap_response_ok(cdr_zip)

    mock_resp = MagicMock()
    mock_resp.content = soap_resp
    mock_resp.raise_for_status = MagicMock()

    with patch("sunat_soap.requests.post", return_value=mock_resp) as mock_post:
        cdr = enviar_factura(
            _xml_firmado_mock(), "20123456789", "F001", 1, _credenciales(), beta=True
        )

    assert cdr.aceptado is True
    assert cdr.codigo_respuesta == "0"
    mock_post.assert_called_once()
    print("PASS test_enviar_factura_aceptada")

def test_enviar_factura_usa_endpoint_beta():
    cdr_xml = _cdr_xml("0", "OK")
    cdr_zip = _hacer_cdr_zip(cdr_xml, "20123456789-01-F001-00000001")
    soap_resp = _soap_response_ok(cdr_zip)
    mock_resp = MagicMock()
    mock_resp.content = soap_resp
    mock_resp.raise_for_status = MagicMock()

    with patch("sunat_soap.requests.post", return_value=mock_resp) as mock_post:
        enviar_factura(_xml_firmado_mock(), "20123456789", "F001", 1, _credenciales(), beta=True)

    args, kwargs = mock_post.call_args
    assert "e-beta.sunat.gob.pe" in args[0]
    print("PASS test_enviar_factura_usa_endpoint_beta")

def test_enviar_factura_usa_endpoint_produccion():
    cdr_xml = _cdr_xml("0", "OK")
    cdr_zip = _hacer_cdr_zip(cdr_xml, "20123456789-01-F001-00000001")
    soap_resp = _soap_response_ok(cdr_zip)
    mock_resp = MagicMock()
    mock_resp.content = soap_resp
    mock_resp.raise_for_status = MagicMock()

    with patch("sunat_soap.requests.post", return_value=mock_resp) as mock_post:
        enviar_factura(_xml_firmado_mock(), "20123456789", "F001", 1, _credenciales(), beta=False)

    args, kwargs = mock_post.call_args
    assert "e-factura.sunat.gob.pe" in args[0]
    print("PASS test_enviar_factura_usa_endpoint_produccion")

def test_enviar_factura_auth_correcta():
    cdr_xml = _cdr_xml("0", "OK")
    cdr_zip = _hacer_cdr_zip(cdr_xml, "20123456789-01-F001-00000001")
    soap_resp = _soap_response_ok(cdr_zip)
    mock_resp = MagicMock()
    mock_resp.content = soap_resp
    mock_resp.raise_for_status = MagicMock()

    creds = CredencialesSOL("20123456789", "MODDATOS", "mi_clave_sol")

    with patch("sunat_soap.requests.post", return_value=mock_resp) as mock_post:
        enviar_factura(_xml_firmado_mock(), "20123456789", "F001", 1, creds)

    _, kwargs = mock_post.call_args
    # SUNAT directo usa WS-Security; no HTTP Basic Auth (auth=None)
    assert kwargs["auth"] is None
    print("PASS test_enviar_factura_auth_correcta")

def test_enviar_factura_content_type_soap():
    cdr_xml = _cdr_xml("0", "OK")
    cdr_zip = _hacer_cdr_zip(cdr_xml, "20123456789-01-F001-00000001")
    soap_resp = _soap_response_ok(cdr_zip)
    mock_resp = MagicMock()
    mock_resp.content = soap_resp
    mock_resp.raise_for_status = MagicMock()

    with patch("sunat_soap.requests.post", return_value=mock_resp) as mock_post:
        enviar_factura(_xml_firmado_mock(), "20123456789", "F001", 1, _credenciales())

    _, kwargs = mock_post.call_args
    assert "text/xml" in kwargs["headers"]["Content-Type"]
    print("PASS test_enviar_factura_content_type_soap")

def test_enviar_factura_nombre_zip_correcto():
    cdr_xml = _cdr_xml("0", "OK")
    cdr_zip = _hacer_cdr_zip(cdr_xml, "20123456789-01-F001-00000042")
    soap_resp = _soap_response_ok(cdr_zip)
    mock_resp = MagicMock()
    mock_resp.content = soap_resp
    mock_resp.raise_for_status = MagicMock()

    with patch("sunat_soap.requests.post", return_value=mock_resp) as mock_post:
        enviar_factura(_xml_firmado_mock(), "20123456789", "F001", 42, _credenciales())

    _, kwargs = mock_post.call_args
    body = kwargs["data"]
    assert b"20123456789-01-F001-00000042.zip" in body
    print("PASS test_enviar_factura_nombre_zip_correcto")

def test_enviar_factura_rechazada():
    cdr_xml = _cdr_xml("4000", "RUC del emisor no encontrado")
    cdr_zip = _hacer_cdr_zip(cdr_xml, "20123456789-01-F001-00000001")
    soap_resp = _soap_response_ok(cdr_zip)
    mock_resp = MagicMock()
    mock_resp.content = soap_resp
    mock_resp.raise_for_status = MagicMock()

    with patch("sunat_soap.requests.post", return_value=mock_resp):
        cdr = enviar_factura(_xml_firmado_mock(), "20123456789", "F001", 1, _credenciales())

    assert cdr.aceptado is False
    assert cdr.codigo_respuesta == "4000"
    print("PASS test_enviar_factura_rechazada")

def test_enviar_factura_fault_soap_lanza_error():
    mock_resp = MagicMock()
    mock_resp.content = _soap_fault("Credenciales inválidas")
    mock_resp.raise_for_status = MagicMock()

    with patch("sunat_soap.requests.post", return_value=mock_resp):
        try:
            enviar_factura(_xml_firmado_mock(), "20123456789", "F001", 1, _credenciales())
            assert False, "Debía lanzar ValueError"
        except ValueError as e:
            assert "Fault" in str(e)
            print("PASS test_enviar_factura_fault_soap_lanza_error")

def test_enviar_factura_http_error_propagado():
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = Exception("HTTP 500")

    with patch("sunat_soap.requests.post", return_value=mock_resp):
        try:
            enviar_factura(_xml_firmado_mock(), "20123456789", "F001", 1, _credenciales())
            assert False, "Debía propagar excepción HTTP"
        except Exception as e:
            assert "500" in str(e)
            print("PASS test_enviar_factura_http_error_propagado")

def test_enviar_factura_serie_invalida_lanza_error():
    for serie_mala in ["<inj>", "f001", "F001<x>", "", "TOOLONG1"]:
        try:
            enviar_factura(_xml_firmado_mock(), "20123456789", serie_mala, 1, _credenciales())
            assert False, f"Debía lanzar ValueError para serie={serie_mala!r}"
        except ValueError as e:
            assert "serie" in str(e).lower()
    print("PASS test_enviar_factura_serie_invalida_lanza_error")

def test_credenciales_clave_sol_no_en_repr():
    creds = _credenciales()
    assert "clave_sol_test" not in repr(creds)
    print("PASS test_credenciales_clave_sol_no_en_repr")


# ── Tests: helpers ────────────────────────────────────────────────────────────

def test_descripcion_cdr_aceptada():
    cdr = CDR(aceptado=True, codigo_respuesta="0", descripcion="La Factura fue aceptada")
    desc = descripcion_cdr(cdr)
    assert "ACEPTADA" in desc
    assert "0" in desc
    print("PASS test_descripcion_cdr_aceptada")

def test_descripcion_cdr_rechazada():
    cdr = CDR(aceptado=False, codigo_respuesta="4000", descripcion="IGV incorrecto")
    desc = descripcion_cdr(cdr)
    assert "RECHAZADA" in desc
    assert "4000" in desc
    print("PASS test_descripcion_cdr_rechazada")

def test_descripcion_cdr_con_observaciones():
    cdr = CDR(
        aceptado=True, codigo_respuesta="0", descripcion="OK",
        observaciones=["Obs1", "Obs2"]
    )
    desc = descripcion_cdr(cdr)
    assert "Obs1" in desc
    assert "Obs2" in desc
    print("PASS test_descripcion_cdr_con_observaciones")

def test_guardar_cdr():
    cdr = CDR(aceptado=True, codigo_respuesta="0", descripcion="OK",
              xml_cdr_raw=b"<CDR>contenido</CDR>")
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as f:
        ruta = Path(f.name)
    guardar_cdr(cdr, ruta)
    assert ruta.exists()
    assert ruta.read_bytes() == b"<CDR>contenido</CDR>"
    ruta.unlink()
    print("PASS test_guardar_cdr")


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        # CredencialesSOL
        test_credenciales_username,
        test_credenciales_campos,
        # Endpoints
        test_endpoints_definidos,
        test_tipos_documento,
        # _nombre_archivo
        test_nombre_archivo_factura,
        test_nombre_archivo_boleta,
        test_nombre_archivo_correlativo_grande,
        test_nombre_archivo_tipo_desconocido,
        # _empaquetar_zip
        test_empaquetar_zip_es_zip_valido,
        test_empaquetar_zip_contiene_xml,
        test_empaquetar_zip_solo_un_archivo,
        # _build_soap_envelope
        test_build_soap_envelope_es_xml,
        test_build_soap_envelope_contiene_filename,
        test_build_soap_envelope_contiene_contenido,
        test_build_soap_envelope_namespace_sunat,
        test_build_soap_envelope_send_bill,
        # _parsear_cdr
        test_parsear_cdr_aceptado,
        test_parsear_cdr_rechazado,
        test_parsear_cdr_con_observaciones,
        test_parsear_cdr_raw_bytes,
        test_parsear_cdr_codigo_2xxx_es_aceptado,
        # _extraer_application_response
        test_extraer_application_response_ok,
        test_extraer_application_response_fault_lanza_error,
        test_extraer_application_response_xml_malo,
        test_extraer_application_response_sin_ar_lanza_error,
        # enviar_factura
        test_enviar_factura_aceptada,
        test_enviar_factura_usa_endpoint_beta,
        test_enviar_factura_usa_endpoint_produccion,
        test_enviar_factura_auth_correcta,
        test_enviar_factura_content_type_soap,
        test_enviar_factura_nombre_zip_correcto,
        test_enviar_factura_rechazada,
        test_enviar_factura_fault_soap_lanza_error,
        test_enviar_factura_http_error_propagado,
        test_enviar_factura_serie_invalida_lanza_error,
        test_credenciales_clave_sol_no_en_repr,
        # helpers
        test_descripcion_cdr_aceptada,
        test_descripcion_cdr_rechazada,
        test_descripcion_cdr_con_observaciones,
        test_guardar_cdr,
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
