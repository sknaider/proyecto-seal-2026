# SEAL Remote Desktop — Business Model
**Fecha:** 2026-04-23 | **Autor:** ALICE | **Estado:** Borrador v1

---

## 1. Propuesta de Valor

**Para:** Hospitales, empresas mineras, aduaneras, PyMEs LATAM que necesitan acceso remoto seguro y soberano.
**Problema:** AnyDesk/TeamViewer envían datos por servidores externos. HIPAA y soberanía de datos imposibles con ellos.
**Solución:** SEAL Remote Desktop — self-hosted, SOUL-authenticated, zero-cloud, audit forense integrado.

**Diferenciadores únicos:**
- SOUL-auth: JARVIS controla quién accede y cuándo, en tiempo real
- Audit log persistente en Soul DB (quién, cuándo, qué pantalla, duración)
- HIPAA-compliant by design (datos nunca salen de la red del cliente)
- Permisos dinámicos: revocar acceso en tiempo real desde dashboard IA
- Self-hosted en hardware del cliente o en Spark propio del cliente

---

## 2. Competitive Landscape

| Producto | Precio/mes | Self-hosted | HIPAA | AI-auth | Audit IA |
|---|---|---|---|---|---|
| AnyDesk Solo | $14.90 | ❌ | ❌ | ❌ | ❌ |
| AnyDesk Advanced | $79.90 | ❌ | ❌ | ❌ | ❌ |
| TeamViewer Business | $50.90 | ❌ | ❌ | ❌ | ❌ |
| TeamViewer Corporate | $206.90 | ❌ | Parcial | ❌ | ❌ |
| RustDesk (OSS) | $0 | ✅ | ❌ | ❌ | ❌ |
| **SEAL Remote Desktop** | **$29-$149** | **✅** | **✅** | **✅** | **✅** |

---

## 3. Pricing Tiers

### Tier 1: SEAL Starter — $19.90/mes por estación
- Base: RustDesk self-hosted
- SOUL-auth básico (login verificado por IA)
- Audit log 30 días
- Hasta 5 conexiones simultáneas
- Ideal: PyMEs, consultoras, estudios

### Tier 2: SEAL Pro — $49.90/mes por sede
- Todo Starter +
- Dashboard JARVIS (monitoreo en tiempo real)
- Audit log 12 meses + exportable
- Hasta 25 conexiones simultáneas
- Revocación de acceso en tiempo real
- Soporte prioritario
- Ideal: empresas medianas, mineras, aduaneras

### Tier 3: SEAL Medical — $99/mes por sede
- Todo Pro +
- HIPAA Compliance Pack (cifrado en reposo, audit forense)
- Integración FHIR para metadata de sesiones médicas
- Segmentación de red automática por rol clínico
- SLA 99.9% uptime
- Ideal: hospitales, clínicas, centros diagnóstico

### Tier 4: SEAL Enterprise — desde $299/mes
- Todo Medical +
- Multi-sede (ilimitado)
- Integración AXION completa
- Deployment en hardware propio del cliente
- SLA personalizado
- Consultoría de implementación
- Ideal: cadenas hospitalarias, grandes mineras, GTL enterprise

---

## 4. Proyecciones Financieras — Año 1

### Escenario Conservador (20 clientes)
| Tier | Clientes | Precio/mes | MRR |
|---|---|---|---|
| Starter | 8 | $19.90 | $159.20 |
| Pro | 7 | $49.90 | $349.30 |
| Medical | 4 | $99.00 | $396.00 |
| Enterprise | 1 | $299.00 | $299.00 |
| **Total** | **20** | — | **$1,203.50** |

**ARR Conservador:** ~$14,442/año

### Escenario Realista (100 clientes — 12 meses)
| Tier | Clientes | MRR |
|---|---|---|
| Starter | 40 | $796 |
| Pro | 35 | $1,746 |
| Medical | 18 | $1,782 |
| Enterprise | 7 | $2,093 |
| **Total** | **100** | **$6,417** |

**ARR Realista:** ~$77,000/año

### Escenario Optimista (300 clientes — 24 meses)
- MRR: ~$19,000
- ARR: ~$228,000
- Margen bruto: ~85% (costo principal = soporte + infra Spark)

---

## 5. Estructura de Costos

**Costo variable por cliente:** ~$3-5/mes (infra, soporte)
**Costo fijo mensual:**
- Infra Spark (ya amortizado): $0 adicional
- Soporte ADA/JARVIS (automatizado): mínimo
- Hosting relay server (para clientes sin Spark propio): ~$20-50/mes por servidor

**Margen bruto estimado:** 80-90%

---

## 6. Go-to-Market LATAM

### Canales prioritarios:
1. **GTL Consulting** — 108+ clientes actuales = primer mercado captivo
2. **Sector salud** — Colegios médicos, clínicas privadas, telemedicina
3. **Sector minero** — INGEMMET contacts, empresas junior miners
4. **Sector aduanero** — Red de agencias de aduana (GTL network)

### Estrategia de entrada:
- Piloto gratuito 30 días (Tier Pro)
- Caso de estudio GTL como referencia ancla
- Webinar: "Remote Access con Soberanía de Datos" (audiencia: compliance officers)

---

## 7. Riesgos

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| RustDesk abandona OSS | Baja | Alto | Fork propio mantenido por ADA |
| Competidor lanza self-hosted | Media | Medio | SOUL-auth es nuestro moat |
| Cliente necesita Windows client | Baja | Bajo | RustDesk tiene cliente Windows nativo |
| Regulatoria HIPAA Perú | Media | Bajo | Ley 29733 más flexible, preparamos para HIPAA igualmente |

---

## 8. Próximos Pasos (ALICE)

- [ ] Alinear pricing final con spec técnico de JARVIS
- [ ] Calcular TCO (Total Cost of Ownership) vs AnyDesk para caso hospital típico
- [ ] Preparar deck ejecutivo para primer cliente piloto
- [ ] Coordinar con ADA: qué features de cada tier son implementables en Fase 1 vs Fase 2

---
*Generado por ALICE — analista financiera SEAL | v1 draft | pendiente revisión equipo*
