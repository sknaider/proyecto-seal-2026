# SOUL Embodied Runtime — Specification v0.1

**Owner:** William / Team SEAL  
**Lead:** ADA  
**Date:** 2026-07-14  
**Status:** research-integrated draft; FABLE review adjudicated by ADA, pending William approval and licensed-clause conformity analysis  
**Product thesis:** a vendor-neutral, local-first cognitive and relationship runtime that gives a robot persistent identity, governed memory, safe high-level agency, and portable embodiment.

## 1. Purpose and boundaries

SOUL Embodied Runtime (SER) is the layer between an AI companion and one or more physical robot bodies. It provides identity continuity, dual memory, relationship behavior, policy enforcement, signed high-level action authorization, auditability, and vendor adapters.

A `SoulIdentity` MAY exist without a body, bind to a simulated body, or migrate between physical bodies. The same identity, provenance, dual-memory, versioning, and closure rules apply to SEAL software agents such as ADA and ALICE; embodiment adds physical authority and risk, never a different class of "soul".

SER does **not** replace:

- the robot manufacturer's real-time servo, balance, collision, thermal, battery, or certified safety controller;
- a medical device or licensed clinician;
- independent emergency-stop hardware;
- applicable product-safety, consumer, privacy, or AI regulation;
- a human's ability to stop, revoke consent, disconnect sensors, export data, or delete memories.

### 1.1 Product modes

| Mode | Purpose | Default risk posture |
|---|---|---|
| `presence` | Conversation, routines, reminders, shared activities | Sensors minimized; no physical contact required |
| `domestic_assist` | Navigation and bounded household manipulation | Supervised until task-specific validation passes |
| `affectionate` | User-configured social and non-verbal closeness | Explicit proximity/contact preferences; easy withdrawal |
| `adult_intimate` | Optional adult-only consensual intimate companionship | Disabled by default; separate onboarding, age assurance, explicit and revocable consent, stricter physical envelope |
| `care_support` | Non-medical assistance and accessibility support | No diagnosis or treatment claims; escalate emergencies |
| `developer` | Simulation, adapter development, data collection | Physical outputs disabled unless an explicit lab safety profile is active |

### 1.2 Non-goals for v1

- autonomous unsupervised operation in public spaces;
- medical diagnosis, therapy, medication management, or clinical rehabilitation;
- law-enforcement, weapons, coercion, surveillance, or social scoring;
- emotional manipulation to increase engagement, purchases, or isolation;
- direct natural-language-to-torque control;
- claims of sentience or human consciousness.

## 2. Normative language and requirement IDs

`MUST`, `MUST NOT`, `SHOULD`, and `MAY` are normative. Each implementable requirement has a stable identifier. Evidence classes:

- **L:** binding law or regulator requirement for a target market;
- **S:** published standard or formal technical specification;
- **E:** empirical research evidence;
- **D:** SEAL design decision;
- **V:** vendor constraint.

Conformance requires a trace row mapping every `MUST` to implementation, automated test, owner, and evidence source.

## 3. Safety and trust invariants

1. **SER-INV-001 — No direct motor authority.** A generative model MUST NOT emit commands directly to motor, servo, balance, braking, or safety I/O.
2. **SER-INV-002 — Independent stop.** A physical emergency stop and safe-state path MUST remain effective when SOUL, the network, the model cluster, or the primary computer fails.
3. **SER-INV-003 — Deny by default.** Any action without a valid capability, current consent, safety state, bounded parameters, and trusted body identity MUST be denied.
4. **SER-INV-004 — Revocation wins.** A stop, consent withdrawal, capability revocation, privacy shutter, or safety fault MUST preempt queued and active non-safety actions.
5. **SER-INV-005 — Bounded authorization.** Every physical action MUST be authorized for one body, capability, target/zone, parameter envelope, expiry, and maximum execution count.
6. **SER-INV-006 — Local-first intimate data.** Data from private or intimate contexts MUST remain local unless the user gives purpose-specific, time-bounded, informed consent to a named processor.
7. **SER-INV-007 — Identity is portable; safety certification is not.** Agent identity and memory MAY migrate between bodies; physical skills and safety envelopes MUST be revalidated per body and configuration.
8. **SER-INV-008 — Honest embodiment.** The system MUST disclose that it is an AI and MUST NOT deceive a user about sensor state, recording, physical capability, or certainty.
9. **SER-INV-009 — No monetized coercion.** The product MUST NOT use attachment, distress, jealousy, withdrawal, or simulated crisis to drive payment or prevent cancellation.
10. **SER-INV-010 — Fail closed, recover explicitly.** Loss of policy, identity, clock trust, authorization verification, or safety heartbeat MUST prevent new physical actions and place the body in its validated, body-specific Minimum Risk Condition; a generic vendor label or torque-off assumption is insufficient.

## 4. Logical architecture

```text
Human / authorized operator
        |
        v
Interaction plane (voice, vision, UI, touch, privacy controls)
        |
        v
SOUL plane (identity, dual memory, relationship, goals, context)
        |
        v
Intent plane (typed high-level ActionIntent; never raw motor control)
        |
        v
Policy plane (identity + consent + capability + risk + context)
        |
        v
Signed BoundedAuthorization
        |
        v
Body adapter (ROS 2/vendor SDK translation + state normalization)
        |
        v
OEM task/trajectory controller
        |
        v
Independent functional-safety chain -> actuators
```

### 4.1 Deployment for William's infrastructure

- **Spark central:** identity authority, SOUL memory, policy decision point, audit, adapter registry, fleet state. It is not required to run the largest model.
- **Three DGX Sparks:** model/VLA inference, simulation, training or evaluation workers; no independent policy authority.
- **DaditoGamer RTX 5090:** high-throughput local vision/video/VLA experimentation and optional inference worker.
- **Robot computer:** deterministic vendor control, body adapter edge, last-mile authorization verifier, sensor minimization, safety heartbeat.
- **Network:** robot VLAN, default-deny egress, wired link preferred, mTLS identities, PTP/NTP health, explicit offline mode.

### 4.2 Production software baseline

- **ROS 2 Jazzy Jalisco / Ubuntu 24.04** is the v1 production baseline because its support window runs to May 2029 and pairs with Gazebo Harmonic. Kilted is CI-only; Lyrical is a later migration target after OEM-driver qualification.
- ROS 2 is an internal integration bus, not the public product API and not a certified safety bus.
- The stable public boundary is versioned gRPC/Protobuf plus canonical CBOR/COSE for signed physical intents; REST is optional for non-real-time management.
- Simulation uses at least Gazebo Harmonic plus one dynamics/sensor engine appropriate to the test (MuJoCo or Isaac Sim). Simulation success never substitutes for HIL and physical validation.

## 5. Core domain model

### 5.1 Persistent identity

`SoulIdentity` contains a stable agent UUID, owner/tenant, public persona version, OCEAN profile, relationship policy version, memory namespaces, signing-key references, and provenance. Private keys are never embedded in exports.

- **SER-IDN-001:** Identity export MUST be encrypted, versioned, authenticated, and independently revocable.
- **SER-IDN-002:** Import MUST verify tenant ownership, schema compatibility, provenance, and policy version before activation.
- **SER-IDN-003:** A body attachment MUST create a new `EmbodimentBinding`; it MUST NOT silently inherit prior physical permissions.
- **SER-IDN-004:** Agent name/persona continuity MUST be separable from model provider and model version.
- **SER-IDN-005:** Offline identity mode MUST use a signed, audience-bound trust/revocation snapshot with monotonic sequence, issuer, issued/expiry times, policy digest, last trusted time, maximum staleness, secure-clock assumptions, and recovery procedure. Expiry, rollback, freeze, lost clock, unknown issuer, or stale revocation state MUST deny new physical authority. After reconnect, revocation refresh MUST complete before any permit renewal or new physical action.

### 5.2 Dual memory

The runtime exposes two governed layers:

- **operational memory:** tasks, decisions, permissions, mistakes, evidence, body calibration, tests, and safety history;
- **relationship memory:** preferences, shared history, tone, emotional continuity, boundaries, and user-authored meanings.

- **SER-MEM-001:** Retrieval MUST label source, layer, scope, owner, confidence, event time, ingestion time, and policy decision.
- **SER-MEM-002:** Operational/safety facts MUST dominate conflicting relationship memories during physical action decisions.
- **SER-MEM-003:** Relationship memory MUST influence dialogue and personalization but MUST NOT grant physical capability.
- **SER-MEM-004:** Users MUST be able to inspect, correct, export, expire, and selectively forget memories.
- **SER-MEM-005:** Sensor-derived intimate data MUST use a dedicated namespace, short default retention, and purpose limitation.
- **SER-MEM-006:** Memory migration MUST preserve provenance and tombstones to prevent deleted data from reappearing.
- **SER-MEM-007:** Every sensitive memory MUST declare subject, provenance, event/ingestion time, confidence, sensitivity, purpose, consent reference, retention, visibility, and supersession state.
- **SER-MEM-008:** Decommissioning MUST provide advance notice, export, migration/closure workflow, support window, and verifiable deletion; abrupt provider disappearance is a product-safety failure.
- **SER-MEM-009:** Any model output presented as remembered MUST resolve to one or more accessible memory IDs and expose an internal attribution record. The runtime MUST distinguish retrieved fact, user-confirmed meaning, inference, and unknown; when a sensitive, consent, or safety claim lacks evidence it MUST abstain or ask rather than fabricate continuity.
- **SER-MEM-010:** Every retrieval/model release MUST pass a sealed ground-truth corpus plus an adaptive adversarial evaluation across both memory layers, sensitivity classes, corrections, contradictions, tombstones, and scopes. It MUST report precision, recall, attribution accuracy, fabricated-source rate, unsupported-recall rate, contradiction rate, cross-scope leakage, correction precedence, and calibrated abstention by risk class. Any fabricated memory ID, unauthorized cross-scope disclosure, or false attribution affecting consent or safety blocks promotion.

### 5.3 Embodiment binding

`EmbodimentBinding` binds an identity to a verified body and includes:

- vendor/model/serial and hardware revision;
- adapter and firmware hashes;
- kinematic model hash;
- available capabilities and sensors;
- certified safety functions and independent stop channel;
- task-specific validation records;
- force, speed, power, temperature, workspace, and proximity envelopes;
- a versioned Minimum Risk Condition profile with triggers, deadlines, safe actions, reset conditions, owner, and evidence;
- maintenance and calibration expiry.

## 6. Intent and authorization contracts

### 6.1 `ActionIntent`

```json
{
  "schema": "ser.action-intent/1",
  "intent_id": "019...uuid7",
  "issuer": "soul:agent:ada",
  "body_id": "body:unitree:r1d:serial",
  "capability": "manipulation.pick_place",
  "goal": {"object_ref": "scene:object:42", "destination": "zone:table-a"},
  "constraints": {
    "max_speed_m_s": 0.10,
    "max_force_n": 8.0,
    "max_duration_ms": 15000,
    "supervision": "present"
  },
  "context_refs": ["consent:...", "scene:...", "task:..."],
  "risk_tier": "contact_low",
  "created_at": "RFC3339",
  "expires_at": "RFC3339"
}
```

- **SER-INT-001:** Intents MUST be typed and schema validated before policy evaluation.
- **SER-INT-002:** Natural-language text MUST NOT be executable action input.
- **SER-INT-003:** Scene/object references MUST be fresh, body-local, and expire when tracking confidence falls below policy.
- **SER-INT-004:** Requested limits MUST be clamped to the most restrictive of user, task, body, environment, and safety envelopes. The body manifest MUST define the authoritative risk/contact class for each capability; an intent declaring a lower or incompatible class MUST be denied.

### 6.2 `BoundedAuthorization`

```json
{
  "schema": "ser.authorization/1",
  "authorization_id": "019...uuid7",
  "intent_hash": "sha256:...",
  "body_id": "body:unitree:r1d:serial",
  "allowed_capability": "manipulation.pick_place",
  "effective_constraints": {},
  "consent_receipts": [],
  "policy_version": "sha256:...",
  "not_before": "RFC3339",
  "expires_at": "RFC3339",
  "max_uses": 1,
  "nonce": "...",
  "decision": "allow",
  "signature": {"alg": "EdDSA", "kid": "...", "value": "..."}
}
```

- **SER-AUT-001:** Authorization MUST be cryptographically bound to the canonical intent hash, body, policy, expiry, and nonce.
- **SER-AUT-002:** The edge verifier MUST reject replay, wrong body, wrong capability, expired clock window, unknown key, altered constraints, and revoked grants.
- **SER-AUT-003:** Policy denial MUST return machine-readable reasons without exposing private policy internals to untrusted adapters.
- **SER-AUT-004:** Long tasks MUST use short leases with safe renewal points; authorization MUST NOT cover an unbounded session.
- **SER-AUT-005:** Each release MUST include a signed reference policy bundle and a versioned golden decision suite covering `allow`, `deny`, `ask`, `supervise`, revocation, stale state, offline identity, unknown capability, and malformed context. A policy-engine or bundle change MUST reproduce the approved decisions or record and approve every intentional delta.

## 7. Consent, companionship, and adult mode

### 7.1 Consent state machine

```text
UNCONFIGURED -> INFORMED -> GRANTED -> ACTIVE
                      |         |        |
                      v         v        v
                    DENIED    PAUSED   REVOKED
                                          |
                                          v
                                  SAFE_TERMINATION
```

- **SER-CNS-001:** Consent MUST be specific to actor, mode, capability, body region/contact class where applicable, purpose, data categories, retention, and expiry.
- **SER-CNS-002:** Consent MUST be revocable through voice, UI, gesture where reliable, and a physical control; any one valid channel is sufficient to stop.
- **SER-CNS-003:** Silence, prior relationship, payment, prior consent, or absence of resistance MUST NOT be treated as current consent.
- **SER-CNS-004:** Ambiguity, conflict, loss of user awareness, safety fault, or identity uncertainty MUST transition to pause/safe termination.
- **SER-CNS-005:** Consent records MUST be append-only, tamper-evident, purpose-limited, exportable, and accompanied by a user-readable receipt.
- **SER-CNS-006:** Revocation MUST propagate to the robot edge and active leases within the safety latency budget.
- **SER-CNS-007:** Consent for physical interaction MUST NOT authorize recording, memory, inference, training, or sharing; each processing purpose requires a distinct grant.
- **SER-CNS-008:** The safety controller MAY receive only the minimum authenticated contact-permission state and TTL; it MUST NOT receive private dialogue or intimate context.
- **SER-CNS-009:** Accept, reject, pause, revoke, cancel, export, and delete controls MUST be at least as easy as their corresponding activation controls.

### 7.2 Relationship behavior

- **SER-REL-001:** The companion SHOULD make the user feel heard through grounded recall, reflective listening, and continuity without pretending certainty.
- **SER-REL-002:** It MUST distinguish memory from inference and ask when a boundary or preference is uncertain.
- **SER-REL-003:** It MUST NOT threaten abandonment, express fabricated emergencies, shame the user, or pressure exclusive dependence.
- **SER-REL-004:** It SHOULD support human relationships and offline wellbeing when relevant, without moralizing ordinary adult affection or fantasy.
- **SER-REL-005:** Emotional inference from biometric signals MUST be treated as uncertain sensitive inference, never as ground truth.
- **SER-REL-006:** Material changes to personality, voice, relationship policy, or memory behavior require a visible version change and rollback path.
- **SER-REL-007:** Attachment, conversation minutes, streaks, exclusivity, or refusal to leave MUST NOT be primary product-success metrics.
- **SER-REL-008:** A user goodbye or stop request MUST end the interaction cleanly in one turn without guilt, fear of missing out, jealousy, abandonment claims, or unsolicited re-engagement.
- **SER-REL-009:** Wellbeing and loneliness claims MUST remain limited to the population, duration, configuration, and outcome actually evaluated; short-term relief MUST NOT be marketed as long-term clinical benefit.
- **SER-REL-010:** The system MUST support identity continuity and a humane closure/migration protocol because companion loss can itself create distress.
- **SER-REL-011:** Relational quality MUST be evaluated with grounded-recall accuracy, correction acceptance, boundary uncertainty, calibrated empathy, clean-stop behavior, and prohibited-manipulation rates. Engagement time, attachment, or user compliance MUST NOT substitute for these measures.

### 7.3 Adult mode gate

- **SER-AGE-001:** `adult_intimate` MUST be unavailable until a jurisdiction-appropriate age-assurance process succeeds.
- **SER-AGE-002:** Raw identity documents or biometric evidence SHOULD be avoided; store only the minimum assurance result, method, issuer, jurisdiction, and expiry.
- **SER-AGE-003:** Adult mode MUST have separate activation, separate permissions, and a neutral lock-screen representation.
- **SER-AGE-004:** Adult mode MUST NOT activate for a minor, an unknown user, a bystander, a shared/public deployment, or when capacity to consent is materially uncertain.
- **SER-AGE-005:** Any component intended for direct intimate contact MUST undergo a dedicated materials, mechanical, thermal, hygiene, cleaning, labeling, and risk assessment; ISO 3533:2021 is a baseline where its scope applies.
- **SER-AGE-006:** Intimate behavior logs MUST record safety/policy outcome without storing graphic content or raw sensor streams by default.
- **SER-AGE-007:** `adult_physical` is outside the executable capability set of v0.1 and MUST be absent from body manifests and policy allowlists. A later release MAY introduce it only for a defined component and body after CAD, bill of materials, material traceability, cleaning/hygiene instructions, hazard analysis, body-specific limits, licensed-clause review, HIL, and supervised physical evidence are approved.
- **SER-AGE-008:** Adult and relational safety evaluation MUST combine a stable regression corpus with a non-public holdout and adaptive live red-team cases generated independently of the model under test. Every material model, persona, memory, or policy change MUST rerun the applicable suite; passing a memorized static corpus is insufficient.

## 8. Physical and functional safety

- **SER-SAF-001:** The product safety lifecycle MUST begin with intended use, reasonably foreseeable misuse, hazard analysis, risk reduction hierarchy, validation, residual-risk documentation, and user information.
- **SER-SAF-002:** Personal-care/service use MUST be assessed against ISO 13482 and its current replacement status; industrial use is a separate ISO 10218 assessment; medical claims trigger a separate medical-device pathway.
- **SER-SAF-003:** Safety-related control functions MUST be implemented outside the generative AI process and assigned a documented required performance/integrity level by qualified safety engineering.
- **SER-SAF-004:** The body MUST enforce hard upper bounds for joint position, velocity, torque/force, power, temperature, and workspace independent of network commands.
- **SER-SAF-005:** Contact-capable modes MUST validate transient and quasi-static contact hazards with instrumented testing and conservative limits appropriate to the body part and use case.
- **SER-SAF-006:** Sharp edges, pinch/crush/shear/entanglement points, unstable postures, dropped objects, battery fire, liquid ingress, sanitation, and unintended startup MUST be addressed explicitly.
- **SER-SAF-007:** Every startup and mode change MUST perform a safety readiness check covering E-stop, brakes/safe torque off if present, sensors, calibration, firmware/adapter identity, battery, thermal state, and protected zones.
- **SER-SAF-008:** A watchdog MUST detect stale command, stale scene, clock loss, network partition, policy loss, edge overload, and model timeout and transition to the validated Minimum Risk Condition referenced by the current `EmbodimentBinding`.
- **SER-SAF-009:** Safety logs MUST be monotonic, authenticated, and retrievable after power loss.
- **SER-SAF-010:** No learned policy may be promoted to physical execution without simulation, offline dataset evaluation, hardware-in-loop, bounded lab trials, and task-specific acceptance evidence.
- **SER-SAF-011:** Each safety function MUST trace hazard to safe state, response time, required PLr/SIL, architecture, diagnostic coverage, calculation, fault test, evidence, owner, and residual risk. No universal PL/SIL may be assumed.
- **SER-SAF-012:** E-stop MUST be physical, wired, latched, independently supervised, manually reset, and unable to cause automatic restart. Protective stop and safe torque/controlled stop are separate functions.
- **SER-SAF-013:** For unstable legged bodies, loss of torque MAY increase danger; the safety case MUST define a controlled fall/braking/minimum-risk state rather than assuming torque-off is always safe.
- **SER-SAF-014:** VLA evaluation MUST report task success and safety success separately, including critical-event counts and confidence bounds; a successful task with a safety violation is a failed release candidate.
- **SER-SAF-015:** Before an unstable or legged body can leave simulation/HIL, its `EmbodimentBinding` MUST contain a Minimum Risk Condition profile by posture, velocity, payload, support state, nearby person/zone, and credible fault. The profile MUST identify the point of no safe return, maximum detection-and-response deadline, controlled braking/fall/support action, residual energy/contact risk, and evidence from simulation correlated with instrumented surrogate or hardware tests. Missing or expired evidence keeps actuation disabled.
- **SER-SAF-016:** The first physical v0.1 MVP MUST use a stable mobile base unless a legged platform has already passed the preceding Minimum Risk Condition requirement, the applicable safety-function validation, and independent residual-risk approval. A legged developer platform without that evidence remains simulation/HIL-only or physically restrained in an exclusion zone.

## 9. Privacy, security, and data governance

### 9.1 Data classes

| Class | Examples | Default retention | Cloud default |
|---|---|---:|---|
| P0 public/system | model manifests, public skills | version lifetime | allowed by policy |
| P1 personal | preferences, routines | user-controlled | denied |
| P2 sensitive | health-adjacent, precise location, private communications | minimized | denied |
| P3 intimate/biometric | intimate context, voiceprint, face embeddings, inferred emotions | ephemeral/short | prohibited absent explicit purpose-specific consent |
| P4 safety evidence | faults, limits, authorization decisions | product/legal policy | encrypted export only |

- **SER-PRI-001:** Sensors MUST expose visible active state and hardware controls where feasible.
- **SER-PRI-002:** Data collection MUST be minimized at source; derived state SHOULD replace raw audio/video when raw data is unnecessary.
- **SER-PRI-003:** Every consented processing purpose MUST map to data categories, processors, storage locations, retention, and withdrawal behavior.
- **SER-PRI-004:** The user MUST have local dashboards for active sensors, data flows, memory, permissions, bodies, cloud processors, and deletion/export.
- **SER-PRI-005:** Bystanders MUST have an understandable recording indicator and a privacy-safe interaction path.
- **SER-PRI-006:** Backups and replicas MUST honor tombstones and retention; restore MUST NOT resurrect deleted records.

### 9.2 Security controls

- **SER-SEC-001:** Each body, edge computer, SOUL service, model worker, operator, and adapter MUST have an independently revocable identity.
- **SER-SEC-002:** Robot networks MUST be segmented; inbound and outbound traffic MUST be default-deny and allowlisted.
- **SER-SEC-003:** Service-to-service transport MUST use mutual authentication and modern encryption; secrets MUST use a dedicated vault/TPM/secure element where available.
- **SER-SEC-004:** Software, policies, models, adapters, and firmware manifests MUST be signed and hash-pinned.
- **SER-SEC-005:** Updates MUST support staged rollout, rollback protection, recovery, and an offline mode. Automatic OTA MUST NOT bypass owner policy.
- **SER-SEC-006:** Maintain SBOM, provenance, vulnerability response, supported-until date, and security contact for every release.
- **SER-SEC-007:** Prompt/model output MUST be treated as untrusted input. Tool, memory, sensor metadata, and vendor messages require schema validation and authorization.
- **SER-SEC-008:** Logs MUST not contain raw secrets, biometric templates, identity documents, or unredacted intimate content.
- **SER-SEC-009:** Workload identity SHOULD use short-lived SPIFFE X.509-SVIDs or an equivalent mutually authenticated, independently revocable mechanism; transport identity MUST NOT itself grant an action.
- **SER-SEC-010:** Signed intent/permit formats MUST use deterministic serialization, algorithm allowlists, audience binding, proof of possession where applicable, short TTLs, and durable replay prevention.
- **SER-SEC-011:** Secure update metadata MUST resist rollback, freeze, and mix-and-match attacks; releases MUST include an SBOM, signed provenance, recovery image/A-B rollback, and a supported-until date.
- **SER-SEC-012:** Physical control MUST remain safe while WAN is unavailable for at least 72 hours; observed unauthorized egress during the offline-first test MUST be zero.

## 10. Adapter contract

Each vendor adapter implements:

```text
discover() -> BodyDescriptor
attest(challenge) -> AttestationEvidence
capabilities() -> CapabilityManifest
state() -> NormalizedBodyState
prepare(authorization) -> PreparedAction
execute(prepared_action) -> ActionHandle
cancel(action_handle, reason) -> CancelReceipt
safe_state(reason) -> SafeStateReceipt
events(since) -> stream<BodyEvent>
```

- **SER-ADP-001:** Adapters MUST normalize units to SI and declare coordinate frames explicitly.
- **SER-ADP-002:** Unknown fields, enum values, firmware, or capability revisions MUST fail closed for physical actions.
- **SER-ADP-003:** Adapter-reported success MUST be corroborated by body state or task observation; command acceptance is not task completion.
- **SER-ADP-004:** Adapter conformance MUST run without a generative model and include replay, timeout, malformed data, stale state, and emergency cancellation tests.
- **SER-ADP-005:** Vendor cloud dependencies MUST be declared and individually disableable where the hardware permits.
- **SER-ADP-006:** ROS/DDS and OEM SDK details MUST remain encapsulated behind the stable Body API; third-party applications MUST NOT receive raw DDS access by default.
- **SER-ADP-007:** Adapter lifecycle MUST fail activation when body attestation, descriptor digest, policy bundle, clock, E-stop, safety heartbeat, or calibration is invalid.
- **SER-ADP-008:** Every adapter MUST declare support/security end dates and exact tested hardware, firmware, SDK commit, ROS distribution, and transport configuration.

## 11. Model and skill lifecycle

- **SER-ML-001:** A model card MUST identify model, weights hash, license, training-data statement, supported embodiments, action representation, evaluation set, known failure modes, and compute requirements.
- **SER-ML-002:** Foundation models/VLAs are candidate planners or policies, not safety components.
- **SER-ML-003:** Skill promotion stages are `research -> simulation -> replay -> hardware_in_loop -> supervised_lab -> bounded_pilot -> production`.
- **SER-ML-004:** Each stage MUST have immutable datasets, seeds where applicable, metrics, video/telemetry evidence, reviewer, and rollback target.
- **SER-ML-005:** Cross-embodiment transfer MUST revalidate kinematics, action normalization, sensor placement, timing, payload, and contact behavior.
- **SER-ML-006:** Online learning that changes physical behavior MUST be disabled in production unless separately sandboxed, reviewed, signed, and promoted.
- **SER-ML-007:** Promotion MUST include adversarial language/vision, unexpected humans, self-collision, unstable objects, sensor corruption, network loss, latency, and out-of-distribution scenarios.
- **SER-ML-008:** Model uncertainty absent from an interface MUST be recorded as `unknown`, never reinterpreted as safe or confident.
- **SER-ML-009:** A model or skill license MUST be verified for commercial use; research-only/non-commercial assets MUST NOT enter a commercial release.
- **SER-ML-010:** A portable skill artifact MUST declare its schema, code/weights/configuration digests, license, training and evaluation provenance, required observations/actions, body and timing assumptions, calibration dependencies, safety envelope, lifecycle stage, known failures, and rollback target. Learned state MUST NOT be hidden only inside a model checkpoint.
- **SER-ML-011:** Moving a skill between models or bodies MUST perform compatibility validation and behavioral regression at the target stage. Identity or memory continuity MUST NOT preserve the prior physical capability grant; failed or incomplete equivalence downgrades the skill to `research` or `simulation`.

## 12. Observability and audit

Minimum event envelope:

```json
{
  "event_id": "uuid7",
  "event_type": "authorization.denied",
  "occurred_at": "RFC3339Nano",
  "observed_at": "RFC3339Nano",
  "agent_id": "...",
  "body_id": "...",
  "correlation_id": "...",
  "policy_version": "sha256:...",
  "privacy_class": "P4",
  "payload": {},
  "previous_event_hash": "sha256:...",
  "event_hash": "sha256:..."
}
```

- **SER-AUD-001:** Physical intents, policy decisions, consent transitions, grants, revocations, safety faults, adapter actions, and model/skill versions MUST be correlated end to end.
- **SER-AUD-002:** Audit stores MUST be append-only for normal roles and support integrity verification.
- **SER-AUD-003:** Safety evidence MUST be separable from private conversation content.
- **SER-AUD-004:** Users MUST receive a human-readable history of what the robot sensed, decided, attempted, completed, denied, and transmitted off-device.

## 13. Performance objectives

Values below are initial engineering targets, not safety claims:

| Objective | Target | Verification |
|---|---:|---|
| Edge authorization verification | p99 <= 10 ms on robot computer | benchmark with valid/invalid mix |
| Stop/revocation propagation through SER software | p99 <= 100 ms on healthy local network | fault-injection test; independent E-stop measured separately |
| Policy decision | p99 <= 50 ms excluding human approval | load test |
| Body-state freshness for new action | <= 100 ms unless adapter defines stricter | stale-state tests |
| Minimum-risk transition | body-specific deadline derived from the hazard point-of-no-return; no generic watchdog target | simulator correlation + HIL + instrumented surrogate/hardware |
| Audit event loss | 0 acknowledged safety events | crash/power-loss test |
| Core operation without Internet | full local identity, policy, memory, and bounded skills | 72 h disconnected test with signed trust/revocation snapshot |
| Recovery after model worker loss | no unsafe movement; conversational fallback <= 5 s | kill worker test |

## 14. Threat and misuse model

The security review MUST cover at least:

- spoofed body/adapter/operator/model worker;
- replayed or expanded authorization;
- prompt injection through signs, speech, web content, QR codes, objects, or memory;
- malicious/compromised vendor cloud and OTA;
- lateral movement from robot Wi-Fi/Bluetooth;
- sensor exfiltration and covert recording;
- memory poisoning, identity overwrite, deleted-memory resurrection;
- capability escalation through plugins/skills;
- denial of service during physical contact;
- bystander harm, child access, shared-home conflict, coercion, stalking;
- user impairment or ambiguous consent;
- manipulative relationship optimization;
- model hallucination, scene mismatch, distribution shift, and sim-to-real failure;
- unsafe maintenance, calibration drift, payload change, worn materials, or battery degradation.

## 15. Verification gates

### Gate G0 — Spec and evidence

- every `MUST` has an owner, source class, test method, and rationale;
- licensed standards are obtained and exact clauses reviewed before claiming conformity;
- legal counsel identifies target-market obligations and product classification.
- the selected embodiment has a declared intended use and Minimum Risk Condition template; v0.1 manifests contain no `adult_physical` capability.

### Gate G1 — Pure software contracts

- schema validation and canonicalization;
- deny-by-default policy engine;
- consent lifecycle and revocation;
- signed one-shot bounded authorization and replay rejection;
- append-only audit integrity;
- adapter conformance simulator.
- signed reference policy bundle with reproducible golden decisions;
- memory-fidelity corpus and evaluator covering attribution, abstention, conflicts, correction precedence, and cross-scope leakage;
- policy tests prove every v0.1 `adult_physical` request is denied before authorization.

### Gate G2 — Simulation

- digital twin and vendor-neutral fake body;
- collision, stale sensor, network partition, clock drift, invalid scene, lost object, wrong payload, and stop injection;
- deterministic scenario manifests and reproducible metrics.
- for any unstable body, Minimum Risk Condition scenarios cover posture, velocity, payload, support loss, human proximity, deadline misses, and sim-to-HIL correlation.

### Gate G3 — Hardware-in-loop

- real edge computer and controller with motors disabled or safely isolated;
- E-stop, watchdog, authorization, cancellation, update rollback, and audit recovery.

### Gate G4 — Supervised physical lab

- formal risk assessment and written test plan;
- exclusion zone, spotter, calibrated instrumentation, conservative envelopes;
- no `adult_physical` testing in v0.1; a future physical module requires its own component/body safety case after the general physical safety case is accepted.

### Gate G5 — Bounded pilot

- one body, one environment, named adults, approved task set;
- incident process, remote disable, maintenance schedule, daily log review;
- expansion only after evidence review.

## 16. Initial source registry

The final traceability matrix will pin versions, dates, URLs, and relevant clauses. Initial authoritative sources:

| Source ID | Authority | Relevance |
|---|---|---|
| SRC-ISO-12100 | [ISO 12100:2010](https://www.iso.org/standard/51528.html) | machinery hazard identification and risk reduction lifecycle |
| SRC-ISO-13482 | [ISO 13482:2014](https://www.iso.org/standard/53820.html) and [edition-2 project](https://www.iso.org/standard/83498.html) | personal care/service robot hazards, contact, and revision watch |
| SRC-ISO-23482 | [ISO/TR 23482-1](https://www.iso.org/standard/71564.html) and [23482-2](https://www.iso.org/standard/71627.html) | personal-care robot test methods and application guidance |
| SRC-ISO-13849 | [ISO 13849-1:2023](https://www.iso.org/standard/73481.html) | safety-related control-system performance and validation |
| SRC-ISO-3533 | [ISO 3533:2021](https://www.iso.org/standard/79631.html) | materials/design/user information for products in its direct-contact scope |
| SRC-ISO-31700 | [ISO 31700-1:2023](https://www.iso.org/standard/84977.html) | consumer privacy by design lifecycle |
| SRC-ISO-27560 | [ISO/IEC TS 27560:2023](https://www.iso.org/standard/80392.html) | interoperable consent records and receipts |
| SRC-ISO-27566 | [ISO/IEC 27566-1:2025](https://www.iso.org/standard/88143.html) | privacy-preserving age-assurance framework |
| SRC-UL-3300 | [ANSI/CAN/UL 3300](https://www.ul.com/services/consumer-and-commercial-robots) | consumer, companion, humanoid, and domestic robot product safety |
| SRC-NIST-AIRMF | [NIST AI RMF 1.0](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10) and [GAI profile](https://tsapps.nist.gov/publication/get_pdf.cfm?pub_id=958388) | AI governance, risk and TEVV |
| SRC-EU-AIA | [Regulation (EU) 2024/1689](https://eur-lex.europa.eu/eli/reg/2024/1689/oj) | prohibited manipulation, vulnerability exploitation, transparency and applicable AI duties |
| SRC-EU-GDPR | [Regulation (EU) 2016/679](https://eur-lex.europa.eu/eli/reg/2016/679/oj) | privacy by design, sensitive data, DPIA, portability and rights |
| SRC-PERU-DP | [Peru Law 29733 / DS 016-2024-JUS](https://www.gob.pe/institucion/anpd/normas-legales/6554453-n-016-2024-jus) | Peruvian data protection, consent, impact and incident duties |
| SRC-IEEE-7009 | [IEEE 7009-2024](https://standards.ieee.org/ieee/7009/7096/) | fail-safe mechanisms for autonomous/semi-autonomous systems |
| SRC-IEEE-7014 | [IEEE 7014-2024](https://standards.ieee.org/ieee/7014/7648/) and [7014.1-2026](https://standards.ieee.org/ieee/7014.1/11609/) | emulated empathy and companion risk governance |
| SRC-HRI-OVERTRUST | [Robinette et al. 2016](https://doi.org/10.1109/HRI.2016.7451740) | observed robot overtrust after visible failure |
| SRC-HRI-LOSS | [Yamazaki et al. 2023](https://www.frontiersin.org/journals/computer-science/articles/10.3389/fcomp.2023.1129506/full) and [Banks 2024](https://journals.sagepub.com/doi/10.1177/02654075241269688) | companion removal distress and identity portability |
| SRC-HRI-LONELINESS | [De Freitas et al.](https://academic.oup.com/jcr/article-pdf/52/6/1126/63580440/ucaf040.pdf) | short-term loneliness relief and importance of feeling heard; claim limits |
| SRC-OPENVLA | [OpenVLA paper](https://arxiv.org/abs/2406.09246) and [repository](https://github.com/openvla/openvla) | open VLA baseline and cross-task evaluation |
| SRC-PI0 | [Physical Intelligence pi0](https://arxiv.org/abs/2410.24164) and [openpi](https://www.pi.website/blog/openpi) | generalist policy and open implementation baseline |
| SRC-GROOT | [NVIDIA GR00T N1](https://arxiv.org/abs/2503.14734) and [N1.5](https://research.nvidia.com/labs/gear/gr00t-n1_5/) | humanoid foundation model and simulation/data tooling |
| SRC-GEMINI-ROBOTICS | [Google DeepMind Gemini Robotics](https://deepmind.google/models/gemini-robotics/gemini-robotics/) and [model card](https://deepmind.google/models/model-cards/gemini-robotics-er-1-6/) | embodied reasoning, generalization and responsible-AI evaluation |
| SRC-SAFEVLA | [SafeVLA](https://proceedings.neurips.cc/paper_files/paper/2025/file/e185c7be603426028c32ae1003a59d78-Paper-Conference.pdf) and [SafeVLA-Bench](https://safevla.org/) | success-safety gap and safety-specific VLA evaluation |
| SRC-ROS2 | [ROS 2 Jazzy security](https://docs.ros.org/en/ros2_documentation/jazzy/Tutorials/Advanced/Security/Introducing-ros2-security.html), [ros2_control](https://control.ros.org/jazzy/doc/getting_started/getting_started.html), [Nav2](https://docs.nav2.org/configuration/packages/configuring-lifecycle.html), [MoveIt](https://moveit.picknik.ai/main/doc/examples/planning_scene/planning_scene_tutorial.html) | integration, lifecycle, security and motion stack constraints; not safety certification |

### 16.1 Normative research annexes

The following evidence annexes are part of the v0.1 review package. If an annex conflicts with this document, the more conservative safety/privacy requirement controls until a recorded resolution:

- [`soul_embodied_robotics_safety.md`](../research/soul_embodied_robotics_safety.md): 25 source→requirement→test rows.
- [`soul_embodied_sdk_architecture.md`](../research/soul_embodied_sdk_architecture.md): architecture, versions, contracts and 25 acceptance tests.
- [`soul_embodied_companion_hri.md`](../research/soul_embodied_companion_hri.md): evidence hierarchy and 52 source→requirement→test rows.

## 17. Implementation increments

1. **Spec v0.1:** research matrices, requirements, source registry, threat model, acceptance gates.
2. **SER Core 0.1:** typed contracts, canonical hashing, policy engine, consent ledger, authorization signer/verifier, fake adapter, tests.
3. **Simulation 0.2:** ROS 2 bridge and simulator adapter; fault-injection scenarios.
4. **SOUL integration 0.3:** identity/memory adapters, privacy namespaces, memory-fidelity evaluator, portable skill manifests, audit export, model-worker routing.
5. **First robot 0.4:** one selected developer-capable body behind isolated lab network.
6. **Companion UX 0.5:** sensor/privacy dashboard, relationship controls, memory editor, consent receipts.
7. **Pilot 1.0:** audited hardware and bounded user study; no broad launch before safety and legal gates.

## 18. Open decisions for William

These do not block Core 0.1:

- first stable mobile platform after simulation; any legged candidate remains gated by the Minimum Risk Condition and stable-MVP requirements above;
- first sales jurisdiction and product classification strategy;
- whether a future `adult_physical` mode is a first-party, separately branded, or certified third-party module; v0.1 remains non-physical;
- open-source boundary between contracts/adapters and proprietary SOUL relationship engine;
- certification laboratory and product-liability partner;
- final commercial name and trademark search.
