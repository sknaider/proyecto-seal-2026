# Fase-1 Security — Implementation Checklist

**Status:** Design Complete, Ready for Development  
**Owner:** ALICE (lead), DUM (transport), NEXUS (testing)  
**Target:** Phase 1 completion by 2026-07-25  

---

## Week 1 (2026-07-05 to 2026-07-11)

### Database Foundation (NEXUS)
- [ ] Execute FASE1_SECURITY_DDL_POSTGRESQL.sql against soul_v3
  ```bash
  psql -d soul_v3 -f spec/FASE1_SECURITY_DDL_POSTGRESQL.sql
  ```
- [ ] Verify tables exist: authorized_devices, revoked_tokens, token_audit, pending_device_registrations
- [ ] Verify RLS is enabled on all 4 tables
- [ ] Test role creation: soul_app, soul_admin
- [ ] Verify views: v_active_devices, v_recent_revocations, v_failed_validations
- [ ] **Test:** Query authorized_devices with SET soul.agent='ADA' (should honor RLS)

### Token Generation (ALICE)
- [ ] Implement `compute_device_fingerprint()` (SHA256 of serial + MAC + os_id)
  - [ ] Windows: WMI query for board serial
  - [ ] macOS: system_profiler SPHardwareDataType
  - [ ] Linux: /sys/class/dmi/id/board_serial
  - [ ] Fallback: uuid.getnode()
- [ ] Implement `generate_token(agent, device_id, device_fingerprint)` (Ed25519)
  - [ ] Load private key from SEAL_MASTER_DOC/.seal/private.key
  - [ ] Create payload JSON with all required fields
  - [ ] Sign with Ed25519 (cryptography library)
  - [ ] Return base64-encoded JWT
- [ ] Implement `validate_token(token_str, expected_agent, expected_device_id)` (local, no DB)
  - [ ] Decode base64
  - [ ] Validate signature (Ed25519 public key)
  - [ ] Check expiry
  - [ ] Verify device_fingerprint matches current hardware
  - [ ] Return (valid: bool, error_msg: str)
- [ ] **Test:** Token signed on Device A invalid on Device B (fingerprint mismatch)

### MCP Server Integration (ALICE)
- [ ] Add token validation to MCP server bootstrap
  ```python
  # In mcp_server_v4.py, before first query:
  if not validate_token(request.headers['Authorization'], expected_agent):
      return 401, "Unauthorized"
  
  # Set security context for RLS
  cursor.execute("SELECT soul_v3.set_security_context(%s, false)", (expected_agent,))
  ```
- [ ] Log all token validations to soul_v3.token_audit
- [ ] Return new token in response if TTL < 1 hour (refresh logic)
- [ ] **Test:** MCP query from unauthenticated device → 401

### Device Registration Endpoint (ALICE)
- [ ] Create `POST /agents/register` endpoint
  - [ ] Receive CSR payload (device_id, device_fingerprint, agent, os_info)
  - [ ] Validate payload format
  - [ ] Insert into soul_v3.pending_device_registrations
  - [ ] Return 202 Accepted
  - [ ] Notify operator (stdout: "New device pending approval")
- [ ] **Test:** curl -X POST http://localhost:8771/agents/register -d '{"device_id":"device-test",...}'

### Red-Team Test: Token Forgery (NEXUS)
- [ ] Attempt to forge token without private key → MUST FAIL
- [ ] Attempt to modify nonce in signed payload → signature invalid
- [ ] Attempt to use token on different device (fingerprint mismatch) → MUST FAIL
- [ ] Document findings in docs/audits/

---

## Week 2 (2026-07-12 to 2026-07-18)

### Token Storage in OS Keyring (ALICE)
- [ ] Implement `store_token_secure(agent, device_id, token)`
  - [ ] Validate token before storing (signature, expiry)
  - [ ] Store in OS keyring:
    - [ ] Windows: Credential Manager via `keyring` library
    - [ ] macOS: Keychain via `keyring` library
    - [ ] Linux: Secret Service (DBus) via `keyring` library
  - [ ] Create ~/.seal/config/token_info.json (hash only, not token)
  - [ ] Verify file permissions: 0600 (readable only by owner)
- [ ] Implement `load_token_secure(agent, device_id)`
  - [ ] Retrieve from OS keyring
  - [ ] Validate signature locally
  - [ ] Return token or raise exception
- [ ] **Test:** Store token, restart process, load token → OK
- [ ] **Test:** token_info.json does NOT contain plaintext token

### CSR Generation & Manual Approval (ALICE)
- [ ] Create `create_csr(agent_name, soul_endpoint)` in installer
  - [ ] Generate device_id (format: device-{12-hex})
  - [ ] Generate device_fingerprint via compute_device_fingerprint()
  - [ ] Create payload with os_info
  - [ ] Save to ~/.seal/csr/device_info.json
  - [ ] Generate QR-code (device_id + fingerprint) → ~/.seal/csr/device_qr.png
  - [ ] Print to stdout: device_id, fingerprint, QR path
- [ ] Create `POST /agents/register_approve` endpoint
  - [ ] Receive approval_data (device_id, device_fingerprint, agent, 2fa_token)
  - [ ] Validate 2FA token (integration point for real 2FA)
  - [ ] Look up pending_device_registrations
  - [ ] Verify fingerprint matches
  - [ ] Call generate_token()
  - [ ] Insert into authorized_devices (status='active')
  - [ ] Log to token_audit (action='issued', reason='manual_approval')
  - [ ] Return token + expires_in_seconds
- [ ] Create UI/CLI for William approval
  - [ ] Display pending devices: device_id, fingerprint, timestamp
  - [ ] Prompt for 2FA
  - [ ] Call /agents/register_approve
  - [ ] Return token to device
- [ ] **Test:** Full CSR → approval flow, device receives token

### Revocation Blacklist (NEXUS)
- [ ] Implement `is_token_revoked(token_jti)` on device
  - [ ] Check local cache (memory dict)
  - [ ] Return (revoked: bool, reason: str)
- [ ] Implement `RevocationManager` daemon
  - [ ] Background thread: sync every 60s
  - [ ] GET /api/revoked_tokens?agent=X
  - [ ] Parse response: [{"token_jti": "...", "reason": "..."}]
  - [ ] Update cache (TTL: 15 min)
  - [ ] If sync fails, expire cache immediately (paranoia mode)
- [ ] Implement `GET /api/revoked_tokens` endpoint (SOUL)
  - [ ] Require Authorization header (Bearer token)
  - [ ] Validate solicitant's token
  - [ ] Query soul_v3.revoked_tokens WHERE agent=X
  - [ ] Return last 90 days
  - [ ] Endpoint should be fast (<100ms)
- [ ] **Test:** Revoke token via DB, device syncs within 60s, validation fails

### Revocation CLI (ALICE)
- [ ] Implement `seal-revoke-token` script
  - [ ] Usage: seal-revoke-token <AGENT> <DEVICE_ID> [REASON]
  - [ ] Require SOUL_ADMIN_TOKEN in env
  - [ ] POST to /api/revoke_token
  - [ ] Update authorized_devices (status='revoked', revoked_at=now)
  - [ ] Insert into revoked_tokens
  - [ ] Print confirmation
- [ ] Make executable: chmod +x ~/SEAL_MASTER_DOC/seal-revoke-token
- [ ] **Test:** seal-revoke-token ADA device-test "test_revocation"

### Red-Team Test: Revocation Latency (NEXUS)
- [ ] Steal token (simulate compromise)
- [ ] Make 5 requests with stolen token
- [ ] Revoke device via CLI
- [ ] Measure time until revocation detected (should be <60s)
- [ ] Verify all subsequent requests fail
- [ ] Document in audit

---

## Week 3 (2026-07-19 to 2026-07-25)

### mTLS & Cert Pinning (DUM)
- [ ] Implement `generate_self_signed_cert()`
  - [ ] Generate RSA 2048 private key
  - [ ] Create self-signed X.509 cert (365-day expiry)
  - [ ] Save to ~/.seal/certs/mcp.{crt,key,pub}
  - [ ] Calculate SHA256 fingerprint for pinning
  - [ ] Run once during SOUL bootstrap
- [ ] Update MCP server to use TLS
  - [ ] Load cert + key in FastMCP init
  - [ ] Enable ssl_keyfile + ssl_certfile
  - [ ] Listen on 0.0.0.0:8771 (TLS enabled)
- [ ] Implement device-side cert pinning
  - [ ] Download SOUL public key during installer (out-of-band)
  - [ ] Store in ~/.seal/certs/soul.pub
  - [ ] Create SSL context with ca_cert
  - [ ] Enable hostname verification
  - [ ] On every HTTPS request: validate cert fingerprint
- [ ] **Test:** Device connects to MCP via HTTPS, validates cert
- [ ] **Test:** MITM proxy cannot intercept (cert pinning prevents)

### Comprehensive Testing (NEXUS + ALICE)

#### Scenario 1: Token Lifecycle
- [ ] Device A boots → loads token from keyring
- [ ] Validates signature locally → OK
- [ ] Connects to SOUL with token
- [ ] SOUL validates → OK, returns 200
- [ ] MCP call succeeds
- [ ] Audit log shows access

#### Scenario 2: Token Theft & Revocation
- [ ] Simulate token theft: extract from keyring
- [ ] Use stolen token on different device → fingerprint mismatch → FAIL
- [ ] Use stolen token on same device → succeeds (expected)
- [ ] Revoke via CLI
- [ ] Next request → revocation list hit → FAIL
- [ ] Verify audit log: who, when, why

#### Scenario 3: Hardware Change
- [ ] Compute fingerprint on Device A
- [ ] Move disk to Device B (different MB)
- [ ] Boot Device B with token from A
- [ ] Compute_device_fingerprint() on Device B → different hash
- [ ] Token validation fails (fingerprint mismatch)
- [ ] Expected: device-level revocation

#### Scenario 4: Cross-Agent Read Block
- [ ] Device running ADA attempts to read JARVIS private memory
- [ ] RLS policy: agent != 'JARVIS' AND scope='private' → DENY
- [ ] Query returns 0 rows
- [ ] Audit log: action='access_denied', reason='scope_mismatch'

#### Scenario 5: Revocation Cache Sync
- [ ] Device starts, revocation cache TTL=15min
- [ ] Revoke token at minute 5
- [ ] Device syncs at minute 6 (within 60s daemon) → cache updated
- [ ] Token rejected at minute 7 (cache hit)
- [ ] Verify sync latency < 60s

### Documentation (ALICE)
- [ ] Create RUNBOOK_FASE1_SECURITY.md
  - [ ] Operator workflow: approve new device, revoke compromised device
  - [ ] Troubleshooting: token validation fails, cache sync issues, cert pinning errors
  - [ ] Monitoring: check v_active_devices, v_recent_revocations
  - [ ] Emergency procedures
- [ ] Update INSTALL_GUIDE.md
  - [ ] CSR generation step
  - [ ] Token storage in keyring (OS-specific)
  - [ ] Keyring fallback (env var for testing)
- [ ] Create SECURITY_MODEL_FASE1.md
  - [ ] Threat model (token theft, device compromise, MITM, cross-agent read)
  - [ ] Mitigations (device fingerprint, revocation, TLS, RLS)
  - [ ] Audit trail (what to monitor, what alerts to set)

### Operational Readiness (NEXUS)
- [ ] Create monitoring queries
  ```sql
  -- Failed validations (last 24h)
  SELECT * FROM soul_v3.v_failed_validations;
  
  -- Recent revocations
  SELECT * FROM soul_v3.v_recent_revocations LIMIT 10;
  
  -- Active devices by agent
  SELECT * FROM soul_v3.v_active_devices;
  ```
- [ ] Set up daily maintenance (cron)
  ```bash
  # Daily cleanup (revocations >90d, audit >180d, pending >24h)
  0 3 * * * psql -d soul_v3 -c "SELECT soul_v3.cleanup_revoked_tokens(); SELECT soul_v3.cleanup_token_audit(); SELECT soul_v3.cleanup_pending_registrations();"
  ```
- [ ] Create alert thresholds
  - [ ] Failed validations > 10/hour → alert
  - [ ] Revocations > 5/day → alert
  - [ ] Cache sync > 2min latency → alert

---

## Acceptance Criteria (ALL Must Pass)

### Security
- [ ] ✅ Token cannot be forged (Ed25519 signature validates or fails)
- [ ] ✅ Token from Device A invalid on Device B (fingerprint mismatch)
- [ ] ✅ Stolen token revoked in < 1 min (CLI) and ineffective within cache TTL
- [ ] ✅ Cross-agent read denied (RLS enforced, audit logged)
- [ ] ✅ Hardware change detected (fingerprint mismatch)

### Functionality
- [ ] ✅ New device: CSR → approval → token → keyring storage ✓
- [ ] ✅ Token refresh 1h before expiry (returned in response)
- [ ] ✅ Revocation cache synced within 60s
- [ ] ✅ Revocation blacklist query < 100ms

### Operability
- [ ] ✅ Operator can revoke device in < 30 sec (CLI one-liner)
- [ ] ✅ Monitoring queries return results < 1 sec
- [ ] ✅ Audit trail complete (all access logged)
- [ ] ✅ Troubleshooting guide resolves 90% of issues

### Testing
- [ ] ✅ Red-team testing: token theft, HW change, MITM (all mitigated)
- [ ] ✅ Smoke tests pass (5+ scenarios)
- [ ] ✅ Coverage: tokengen, tokenval, csr, revocation, rls

---

## Deployment Checklist

### Pre-Deployment
- [ ] DDL executed, tables verified in prod soul_v3
- [ ] SOUL private key (Ed25519) backed up + secured
- [ ] Public key distribution prepared (for devices)
- [ ] MCP server updated + tested locally
- [ ] All CLI tools executable + paths documented

### Rollout
- [ ] **Canary:** SOUL central only (no devices yet)
- [ ] **Alpha:** Single device (ADA on laptop) registers + gets token
- [ ] **Beta:** JARVIS + NEXUS devices join
- [ ] **GA:** All agents, all devices

### Post-Deployment Monitoring
- [ ] Check v_active_devices (should show registered devices)
- [ ] Check token_audit (should show validations)
- [ ] Test revocation: seal-revoke-token test-device → works?
- [ ] Verify RLS: query authorized_devices with different agents

---

## Known Risks & Mitigations

| Risk | Probability | Mitigation |
|------|-------------|-----------|
| OS keyring unavailable | Low | Fallback to env var SEAL_SESSION_TOKEN (dev only) |
| Private key leak | Very Low | Key file in SEAL_MASTER_DOC, restricted perms, backup encrypted |
| Revocation sync network failure | Low | Paranoia mode: expire cache immediately, reject all tokens |
| Token TTL too long (24h) | N/A | Design choice; mitigated by fingerprint + revocation |
| RLS policy bypass | Very Low | Red-team testing before GA |

---

## Links & References

**Design Docs:**
- FASE1_SECURITY_AUTH_GATE_NEXUS.md (format, flows, threat model)
- FASE1_SECURITY_DDL_POSTGRESQL.sql (tables, functions, RLS)
- FASE1_SECURITY_EXECUTIVE_SUMMARY.md (overview, timeline)

**Implementation Paths:**
- Token: `/home/dadito/IA/proyecto-seal/memory/mcp_server_v4.py`
- CSR UI: `/home/dadito/IA/proyecto-seal/seal-agent/installer.py`
- Revocation: `/home/dadito/IA/proyecto-seal/memory/revocation_client.py`
- mTLS: `/home/dadito/IA/proyecto-seal/seal-agent/mcp_client_setup.py`

**Testing:**
- Red-team scenarios: `docs/audits/security/FASE1_redteam_results.md`
- Smoke tests: `tests/test_fase1_security.py`

---

## Sign-Off

- [ ] ALICE (implementation owner): _______________
- [ ] NEXUS (testing owner): _______________
- [ ] DUM (transport owner): _______________
- [ ] JARVIS (architecture approval): _______________

---

**Generated:** 2026-07-02  
**Last Updated:** 2026-07-02  
**Next Review:** After Week 1 completion (2026-07-11)
