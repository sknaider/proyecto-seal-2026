#!/usr/bin/env python3
"""
sunat_ubl.py — Generador XML UBL 2.1 para facturas electrónicas SUNAT Perú
Cumple con: RS 097-2012/SUNAT, RS 274-2015/SUNAT, RS 340-2017/SUNAT

Uso:
    from sunat_ubl import Factura, Proveedor, Cliente, Linea, generar_xml

    factura = Factura(
        serie="F001", numero=1,
        emisor=Proveedor(ruc="20123456789", razon_social="MI EMPRESA SAC", ...),
        receptor=Cliente(ruc="20987654321", razon_social="CLIENTE SAC", ...),
        lineas=[Linea(descripcion="Servicio X", cantidad=1, precio_unitario=100.0)],
    )
    xml_bytes = generar_xml(factura)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from lxml import etree

# ── Namespaces UBL 2.1 ───────────────────────────────────────────────────────

NS = {
    "xmlns": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "xmlns:cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "xmlns:cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "xmlns:ccts": "urn:un:unece:uncefact:documentation:2",
    "xmlns:ds": "http://www.w3.org/2000/09/xmldsig#",
    "xmlns:ext": "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2",
    "xmlns:qdt": "urn:oasis:names:specification:ubl:schema:xsd:QualifiedDatatypes-2",
    "xmlns:sac": "urn:sunat:names:specification:ubl:peru:schema:xsd:SunatAggregateComponents-1",
    "xmlns:udt": "urn:un:unece:uncefact:data:specification:UnqualifiedDataTypesSchemaModule:2",
    "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
}

CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
EXT = "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"
DS  = "http://www.w3.org/2000/09/xmldsig#"

# ── Tipos de comprobante ─────────────────────────────────────────────────────

TIPOS = {
    "FACTURA": "01",
    "BOLETA":  "03",
    "NOTA_CREDITO": "07",
    "NOTA_DEBITO":  "08",
}

# ── IGV ──────────────────────────────────────────────────────────────────────

IGV_RATE = Decimal("0.18")

def d(v) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def d6(v) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


# ── Dataclasses ──────────────────────────────────────────────────────────────

@dataclass
class DireccionFiscal:
    ubigeo: str = "150101"          # Lima por defecto
    departamento: str = "LIMA"
    provincia: str = "LIMA"
    distrito: str = "LIMA"
    urbanizacion: str = "-"
    direccion: str = ""
    codigo_pais: str = "PE"

@dataclass
class Proveedor:
    ruc: str
    razon_social: str
    nombre_comercial: str = ""
    direccion: DireccionFiscal = field(default_factory=DireccionFiscal)

@dataclass
class Cliente:
    tipo_doc: str         # "6"=RUC, "1"=DNI, "0"=varios
    numero_doc: str
    razon_social: str
    direccion: str = ""
    email: str = ""

@dataclass
class Linea:
    descripcion: str
    cantidad: Decimal | float | int
    precio_unitario: Decimal | float       # precio sin IGV
    unidad: str = "NIU"                    # NIU=unidad, ZZ=servicio
    codigo_producto: str = ""
    afecto_igv: bool = True                # True=gravado (10), False=inafecto (30)/exonerado (20)
    # catalogo07: "10"=Gravado, "20"=Exonerado, "30"=Inafecto. Cuando se deja vacío se
    # auto-determina: afecto_igv=True→"10", afecto_igv=False→"30" (inafecto por defecto).
    # Para exonerado pasar tipo_afectacion_igv="20" con afecto_igv=False.
    tipo_afectacion_igv: str = ""

    def __post_init__(self):
        self.cantidad = d(self.cantidad)
        self.precio_unitario = d(self.precio_unitario)
        if not self.tipo_afectacion_igv:
            self.tipo_afectacion_igv = "10" if self.afecto_igv else "30"

    @property
    def valor_venta(self) -> Decimal:
        return d(self.cantidad * self.precio_unitario)

    @property
    def igv(self) -> Decimal:
        return d(self.valor_venta * IGV_RATE) if self.afecto_igv else Decimal("0.00")

    @property
    def precio_con_igv(self) -> Decimal:
        return d(self.precio_unitario * (1 + IGV_RATE)) if self.afecto_igv else self.precio_unitario

@dataclass
class Factura:
    serie: str                   # F001 (facturas), B001 (boletas)
    numero: int
    emisor: Proveedor
    receptor: Cliente
    lineas: list[Linea]
    moneda: str = "PEN"          # PEN, USD
    fecha_emision: date = field(default_factory=date.today)
    fecha_vencimiento: Optional[date] = None
    observaciones: str = ""
    tipo: str = "FACTURA"        # FACTURA, BOLETA

    @property
    def id(self) -> str:
        return f"{self.serie}-{self.numero:08d}"

    @property
    def tipo_codigo(self) -> str:
        return TIPOS[self.tipo]

    @property
    def subtotal(self) -> Decimal:
        return d(sum(l.valor_venta for l in self.lineas))

    @property
    def total_igv(self) -> Decimal:
        return d(sum(l.igv for l in self.lineas))

    @property
    def total(self) -> Decimal:
        return d(self.subtotal + self.total_igv)


# ── XML Builder ──────────────────────────────────────────────────────────────

def _el(parent, ns: str, tag: str, text: str = "", **attribs) -> etree._Element:
    e = etree.SubElement(parent, f"{{{ns}}}{tag}", **attribs)
    if text:
        e.text = text
    return e


def generar_xml(factura: Factura) -> bytes:
    """Genera el XML UBL 2.1 sin firmar. Listo para firma digital posterior."""
    if not factura.lineas:
        raise ValueError("La factura debe tener al menos una línea de detalle")

    # Root element
    root = etree.Element(
        "Invoice",
        nsmap={
            None: "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
            "cac": CAC, "cbc": CBC, "ext": EXT, "ds": DS,
            "sac": "urn:sunat:names:specification:ubl:peru:schema:xsd:SunatAggregateComponents-1",
        }
    )

    # UBLExtensions — placeholder para firma digital
    ext_root = _el(root, EXT, "UBLExtensions")
    ext_item = _el(ext_root, EXT, "UBLExtension")
    ext_content = _el(ext_item, EXT, "ExtensionContent")
    # Signature placeholder — signxml llenará esto
    ds_sig = etree.SubElement(ext_content, f"{{{DS}}}Signature", Id="SignatureSP")

    # Versión y customización
    _el(root, CBC, "UBLVersionID", "2.1")
    _el(root, CBC, "CustomizationID", "2.0")

    # Número de factura
    _el(root, CBC, "ID", factura.id)

    # Fechas
    _el(root, CBC, "IssueDate", factura.fecha_emision.strftime("%Y-%m-%d"))
    _el(root, CBC, "IssueTime", "00:00:00")
    if factura.fecha_vencimiento:
        _el(root, CBC, "DueDate", factura.fecha_vencimiento.strftime("%Y-%m-%d"))

    # Tipo de comprobante — solo listID conforme a cpe-engine/SUNAT
    _el(root, CBC, "InvoiceTypeCode", factura.tipo_codigo, listID="0101")

    # Note — monto en letras (requerido por SUNAT)
    from sunat_pdf import monto_en_letras as _monto_letras
    _el(root, CBC, "Note", _monto_letras(factura.total, factura.moneda))

    # Note — observaciones adicionales
    if factura.observaciones:
        _el(root, CBC, "Note", factura.observaciones)

    # Moneda
    _el(root, CBC, "DocumentCurrencyCode", factura.moneda)

    # Referencia a firma
    sig_ref = _el(root, CAC, "Signature")
    _el(sig_ref, CBC, "ID", "SignatureSP")
    sig_party = _el(sig_ref, CAC, "SignatoryParty")
    sig_id = _el(sig_party, CAC, "PartyIdentification")
    _el(sig_id, CBC, "ID", factura.emisor.ruc)
    sig_name = _el(sig_party, CAC, "PartyName")
    _el(sig_name, CBC, "Name", factura.emisor.razon_social)
    sig_attach = _el(sig_ref, CAC, "DigitalSignatureAttachment")
    sig_ext_uri = _el(sig_attach, CAC, "ExternalReference")
    _el(sig_ext_uri, CBC, "URI", "#SignatureSP")

    # ── Proveedor (emisor) ───────────────────────────────────────────────────
    supplier = _el(root, CAC, "AccountingSupplierParty")
    sup_party = _el(supplier, CAC, "Party")

    sup_party_id = _el(sup_party, CAC, "PartyIdentification")
    _el(sup_party_id, CBC, "ID", factura.emisor.ruc, schemeID="6")

    sup_pname = _el(sup_party, CAC, "PartyName")
    _el(sup_pname, CBC, "Name",
        factura.emisor.nombre_comercial or factura.emisor.razon_social)

    sup_tax = _el(sup_party, CAC, "PartyTaxScheme")
    _el(sup_tax, CBC, "RegistrationName", factura.emisor.razon_social)
    _el(sup_tax, CBC, "CompanyID", factura.emisor.ruc, schemeID="6")
    sup_tax_scheme = _el(sup_tax, CAC, "TaxScheme")
    _el(sup_tax_scheme, CBC, "ID", "-")

    sup_legal = _el(sup_party, CAC, "PartyLegalEntity")
    _el(sup_legal, CBC, "RegistrationName", factura.emisor.razon_social)
    reg_addr = _el(sup_legal, CAC, "RegistrationAddress")
    _el(reg_addr, CBC, "ID", factura.emisor.direccion.ubigeo)
    _el(reg_addr, CBC, "AddressTypeCode", "0000")
    _el(reg_addr, CBC, "CitySubdivisionName", factura.emisor.direccion.urbanizacion)
    _el(reg_addr, CBC, "CityName", factura.emisor.direccion.provincia)
    _el(reg_addr, CBC, "CountrySubentity", factura.emisor.direccion.departamento)
    _el(reg_addr, CBC, "District", factura.emisor.direccion.distrito)
    if factura.emisor.direccion.direccion:
        addr_line = _el(reg_addr, CAC, "AddressLine")
        _el(addr_line, CBC, "Line", factura.emisor.direccion.direccion)
    reg_country = _el(reg_addr, CAC, "Country")
    _el(reg_country, CBC, "IdentificationCode", factura.emisor.direccion.codigo_pais)

    # ── Cliente (receptor) ───────────────────────────────────────────────────
    customer = _el(root, CAC, "AccountingCustomerParty")
    cus_party = _el(customer, CAC, "Party")

    cus_party_id = _el(cus_party, CAC, "PartyIdentification")
    _el(cus_party_id, CBC, "ID", factura.receptor.numero_doc,
        schemeID=factura.receptor.tipo_doc)

    cus_legal = _el(cus_party, CAC, "PartyLegalEntity")
    _el(cus_legal, CBC, "RegistrationName", factura.receptor.razon_social)

    # ── Forma de pago (requerido por SUNAT CustomizationID 2.0) ─────────────
    payment_terms = _el(root, CAC, "PaymentTerms")
    _el(payment_terms, CBC, "ID", "FormaPago")
    _el(payment_terms, CBC, "PaymentMeansID", "Contado")

    # ── Totales de impuestos ─────────────────────────────────────────────────
    tax_total = _el(root, CAC, "TaxTotal")
    _el(tax_total, CBC, "TaxAmount", str(factura.total_igv), currencyID=factura.moneda)

    tax_sub = _el(tax_total, CAC, "TaxSubtotal")
    _el(tax_sub, CBC, "TaxableAmount", str(factura.subtotal), currencyID=factura.moneda)
    _el(tax_sub, CBC, "TaxAmount", str(factura.total_igv), currencyID=factura.moneda)
    tax_cat = _el(tax_sub, CAC, "TaxCategory")
    tax_scheme = _el(tax_cat, CAC, "TaxScheme")
    _el(tax_scheme, CBC, "ID", "1000")
    _el(tax_scheme, CBC, "Name", "IGV")
    _el(tax_scheme, CBC, "TaxTypeCode", "VAT")

    # ── Totales monetarios ───────────────────────────────────────────────────
    legal_total = _el(root, CAC, "LegalMonetaryTotal")
    _el(legal_total, CBC, "LineExtensionAmount", str(factura.subtotal), currencyID=factura.moneda)
    _el(legal_total, CBC, "TaxInclusiveAmount", str(factura.total), currencyID=factura.moneda)
    _el(legal_total, CBC, "PayableAmount", str(factura.total), currencyID=factura.moneda)

    # ── Líneas de detalle ────────────────────────────────────────────────────
    for i, linea in enumerate(factura.lineas, start=1):
        line_el = _el(root, CAC, "InvoiceLine")
        _el(line_el, CBC, "ID", str(i))
        _el(line_el, CBC, "InvoicedQuantity", str(linea.cantidad),
            unitCode=linea.unidad)
        _el(line_el, CBC, "LineExtensionAmount", str(linea.valor_venta), currencyID=factura.moneda)

        pricing = _el(line_el, CAC, "PricingReference")
        alt_cond = _el(pricing, CAC, "AlternativeConditionPrice")
        _el(alt_cond, CBC, "PriceAmount", str(linea.precio_con_igv), currencyID=factura.moneda)
        _el(alt_cond, CBC, "PriceTypeCode", "01")

        line_tax = _el(line_el, CAC, "TaxTotal")
        _el(line_tax, CBC, "TaxAmount", str(linea.igv), currencyID=factura.moneda)
        line_tax_sub = _el(line_tax, CAC, "TaxSubtotal")
        _el(line_tax_sub, CBC, "TaxableAmount", str(linea.valor_venta), currencyID=factura.moneda)
        _el(line_tax_sub, CBC, "TaxAmount", str(linea.igv), currencyID=factura.moneda)
        line_tax_cat = _el(line_tax_sub, CAC, "TaxCategory")
        _el(line_tax_cat, CBC, "Percent", str(IGV_RATE * 100))
        _el(line_tax_cat, CBC, "TaxExemptionReasonCode", linea.tipo_afectacion_igv)
        line_tax_scheme = _el(line_tax_cat, CAC, "TaxScheme")
        _el(line_tax_scheme, CBC, "ID", "1000")
        _el(line_tax_scheme, CBC, "Name", "IGV")
        _el(line_tax_scheme, CBC, "TaxTypeCode", "VAT")

        line_item = _el(line_el, CAC, "Item")
        _el(line_item, CBC, "Description", linea.descripcion)
        if linea.codigo_producto:
            item_id = _el(line_item, CAC, "SellersItemIdentification")
            _el(item_id, CBC, "ID", linea.codigo_producto)

        line_price = _el(line_el, CAC, "Price")
        _el(line_price, CBC, "PriceAmount", str(linea.precio_unitario), currencyID=factura.moneda)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)
