# Medical and Privacy Standards · Research and Database Decisions

**Research section:** which aspects of each standard typically impact database design.  
**ADOPTED section:** what has already been adopted in the PEF schema (`esquema_base_datos_v2.md` = baseline snapshot), the ER diagram, and the [Data Standards](DataStandards/README.md). Operational runbook: root README. `docs/` is only a stub; no `Legacy/` / `porting_sources`.

**Initial authoring date:** September 2026 · **Last ADOPTED update:** November 2026  
**Scope:** HL7 FHIR, DICOM, GDPR (EU), and LFPDPPP (Mexico)  
**Disclaimer:** this document is technical and informational; it does not constitute legal advice.

---

## 1. Version Selection Criteria


| Standard | Recommended Working Version (Today) | Alternative / Horizon | Rationale |
| --- | --- | --- | --- |
| **HL7 FHIR** | **R4** (v4.0.1), normative core | R5 published (2023) with low adoption; **R6** in ballot | R4 is the regulatory and EHR vendor baseline; the 2025 global HL7/Firely survey still shows R4 as the top primary version; US Core and ONC/CMS mandates target R4 and plan to skip R5 straight to R6 |
| **DICOM** | **current** edition (e.g., PS3 · 2026c in HTML "current") | Do not pin a "year" in data contracts; link to `/current/` | DICOM is republished multiple times per year; the official standard is the latest edition plus Final Text supplements |
| **GDPR** | Regulation (EU) **2016/679** (in force since 2018) | No equivalent successor "version" | Stable framework; evolution occurs via national directives and supervisory guidance |
| **LFPDPPP (MX)** | **New law** DOF **2025-03-20** (in force 2025-03-21); consolidated text with **2025-11-14** reform | 2010 law repealed | Supervisory authority transferred (from INAI to the Ministry of Anti-Corruption and Good Governance) and stricter rules on consent/purposes |


---



## 2. HL7 FHIR



### 2.1 Versions and Key Changes Across R4 → R5 → (R6)

- **R4 (2019):** first release with a *normative* core; production baseline and IGs (e.g., US Core on R4). Spec: [https://hl7.org/fhir/R4/](https://hl7.org/fhir/R4/)
- **R4B (2022):** selective bridge release; does not replace R4 as the baseline. [https://hl7.org/fhir/R4B/](https://hl7.org/fhir/R4B/)
- **R5 (2023):** improvements (e.g., Subscriptions, clinical reasoning); **EHR adoption remains low**. Version history: [https://hl7.org/fhir/history.html](https://hl7.org/fhir/history.html) — notably, `Media` **is removed**; implementers are directed to use `DocumentReference` **or** `Observation`.
- **R6:** undergoing normative ballot (2026); final publication horizon typically 2026–2027. Ballot / drafts: [https://hl7.org/fhir/](https://hl7.org/fhir/) (follow the published versions index). In the U.S., community signals indicate that **US Core will skip R5 and target R6**.

**DB implication:** model exchange contracts using **R4 vocabulary** (stable resources and datatypes). If visual evidence is exported in the future, prefer an anchor compatible with `DocumentReference` **+** `Attachment` (valid in R4 and aligned with R5+) instead of depending solely on `Media`.

### 2.2 Datatypes and Resources That Commonly Affect Data Models


| FHIR Piece | Value to a Database | Exact Reference Page |
| --- | --- | --- |
| **Identifier** (`system` URI + `value`) | Dictionary of external IDs (HIS/MRN/staff) ↔ local UUID; lookup `system\|value` before inserting | [https://hl7.org/fhir/R4/datatypes.html#Identifier](https://hl7.org/fhir/R4/datatypes.html#Identifier) |
| **Patient** | Minimal patient record; does not require a full EHR | [https://hl7.org/fhir/R4/patient.html](https://hl7.org/fhir/R4/patient.html) |
| **Practitioner** | Physician / clinical professional | [https://hl7.org/fhir/R4/practitioner.html](https://hl7.org/fhir/R4/practitioner.html) |
| **Location** | Room / operating room | [https://hl7.org/fhir/R4/location.html](https://hl7.org/fhir/R4/location.html) |
| **Encounter** | Care episode / temporal context | [https://hl7.org/fhir/R4/encounter.html](https://hl7.org/fhir/R4/encounter.html) |
| **Procedure** | Surgical act / procedure | [https://hl7.org/fhir/R4/procedure.html](https://hl7.org/fhir/R4/procedure.html) |
| **Device** | Device / piece-level traceable instrument | [https://hl7.org/fhir/R4/device.html](https://hl7.org/fhir/R4/device.html) |
| **Attachment** | File metadata (`contentType`, `url`, `size`, `hash`, `title`) without embedding raw bytes | [https://hl7.org/fhir/R4/datatypes.html#Attachment](https://hl7.org/fhir/R4/datatypes.html#Attachment) |
| **Binary** | Raw byte container or REST bridge; **no clinical context**; access control via `securityContext` | [https://hl7.org/fhir/R4/binary.html](https://hl7.org/fhir/R4/binary.html) |
| **Media** (R4) | **Non-DICOM** image/video/audio with metadata; removed in R5 | [https://hl7.org/fhir/R4/media.html](https://hl7.org/fhir/R4/media.html) |
| **DocumentReference** | Searchable index of documents/evidence (R5+ path) | [https://hl7.org/fhir/R4/documentreference.html](https://hl7.org/fhir/R4/documentreference.html) · R5: [https://hl7.org/fhir/documentreference.html](https://hl7.org/fhir/documentreference.html) |
| **ImagingStudy** | **DICOM** studies (WADO-RS / endpoints); distinct from Media | [https://hl7.org/fhir/R4/imagingstudy.html](https://hl7.org/fhir/R4/imagingstudy.html) |


**Explicit FHIR boundary (ImagingStudy vs. Media):** *"ImagingStudy is used for DICOM imaging… Use Media to track non-DICOM images, video, or audio."* — see Boundaries section in ImagingStudy (link above).

### 2.3 Impact Today vs. Future (FHIR → DB)


| Horizon | What Matters to the DB | Rationale |
| --- | --- | --- |
| **Today** | External identifier table/contract (`system`+`value`+resource type); mapping Patient/Practitioner/Location/Procedure/Device to local UUIDs | Enables HIS integration without duplicating rows; most widely used exchange datatype |
| **Today** | Store evidence **URI + hash + contentType**, not BLOBs inside clinical rows | Aligned with Attachment/Binary; simplifies FHIR export without re-architecting |
| **Future** | FHIR REST export layers or Bundle messages; national profiles | Needed once a live HIS is connected or interoperability certification is required |
| **Future** | If overhead evidence is elevated to a "clinical imaging study," consider **ImagingStudy + DICOM**; if it remains tray JPEG/MP4, **DocumentReference/Attachment** suffices | Avoids DICOM over-engineering in an RGB-camera vision MVP |


**Adoption citation:** [2025 State of FHIR Survey (HL7 / Firely)](https://www.hl7.org/documentcenter/public/white-papers/2025%20State%20of%20FHIR%20Survey%20Report.pdf) — R4 remains the most widely reported primary version compared to R5.

### 2.4 ADOPTED · FHIR in the PEF DB

**Version decision:** contracts and identifiers are anchored to **FHIR R4**. R5 or removed `Media` is not modeled as a dependency.

**What was implemented in the schema / standards**


| FHIR R4 Concept | PEF Decision | Where |
| --- | --- | --- |
| Identifier (`system` + `value` + use) | `resource_identifier` table (no polymorphic FK); partial unique index on active rows | DDL §5.3 · [PEF-DS02](DataStandards/PEF-DS02-Clinical-Context-and-Identifiers.md) |
| Minimal Patient | `patient` (`display_name`, `birth_date`, `gender_id`, `institution_id`) | DS02 |
| Practitioner | `physician` + `physician_specialty` + surgical role on `operation_physician` | DS02 |
| Location | `operating_room` (+ `capture_station` as capture point) | DS02 |
| Encounter / Procedure | `operation` + `cat_procedure_type` / `cat_operation_status` | DS02 |
| Device (piece) | `instrument` + family; Identifier may type `instrument` | DS03 · DS02 |
| Attachment / Binary (non-DICOM) | `media_asset`: URI, `content_type`, `sha256`, `size_bytes`, `kind` — bytes in object storage | [PEF-DS05](DataStandards/PEF-DS05-Evidence-and-Object-Storage.md) |
| Machine client / export | `integration_client` + scopes; FHIR projection (not a second data model) | [PEF-DS08](DataStandards/PEF-DS08-Machine-Clients-and-FHIR-Export.md) |


**Intentionally left out**

- No IOD / `ImagingStudy` / Study Instance UID tables.
- Full FHIR resources are not persisted as the source of truth: PG is canonical; FHIR is an **export projection**.
- `DocumentReference` is not a standalone table: it can be projected from `media_asset` + session when export is exposed.
- Semester demo: full FHIR export and Bundle are **enabled by design** in DS08; they do not block the counting core.

---



## 3. DICOM



### 3.1 What It Is and Current Edition

DICOM (NEMA PS3 / ISO 12052) is the standard for **communicating and managing medical imaging information** and related data. The current edition is always consulted at:

- Current portal: [https://www.dicomstandard.org/current](https://www.dicomstandard.org/current)
- PS3.1 Introduction (HTML current, e.g., 2026c): [https://dicom.nema.org/medical/dicom/current/output/html/part01.html](https://dicom.nema.org/medical/dicom/current/output/html/part01.html)

**Changes between "versions":** there are no software-style major versions; there are **dated editions** (2025d, 2026a/b/c…) and supplements. For DB design, reference *capabilities* (IOD, tags, security profiles, DICOMweb), not a frozen year number in the DDL.

### 3.2 Parts with Potential Data Impact


| Part | Relevance for DB / Storage | Link |
| --- | --- | --- |
| **PS3.3** Information Object Definitions | Defines objects (Patient/Study/Series/Instance) and attributes; classic hierarchical PACS model | [https://dicom.nema.org/medical/dicom/current/output/html/part03.html](https://dicom.nema.org/medical/dicom/current/output/html/part03.html) |
| **PS3.6** Data Dictionary | Numeric tags (e.g., Patient ID, Study Instance UID) | [https://dicom.nema.org/medical/dicom/current/output/html/part06.html](https://dicom.nema.org/medical/dicom/current/output/html/part06.html) |
| **PS3.10** Media Storage / File Format | `.dcm` file, File Meta Information, 128-byte preamble | [https://dicom.nema.org/medical/dicom/current/output/html/part10.html](https://dicom.nema.org/medical/dicom/current/output/html/part10.html) |
| **PS3.15** Security | Security profiles, auditing, **Attribute Confidentiality / de-identification** (Annex E) | [https://dicom.nema.org/medical/dicom/current/output/html/part15.html](https://dicom.nema.org/medical/dicom/current/output/html/part15.html) |
| **PS3.18** Web Services (DICOMweb) | WADO-RS / STOW-RS / QIDO-RS: modern HTTP APIs vs. classic C-STORE | [https://dicom.nema.org/medical/dicom/current/output/html/part18.html](https://dicom.nema.org/medical/dicom/current/output/html/part18.html) |
| **PS3.21** Transformations | DICOM ↔ other representation transformations (bridge with FHIR/HL7) | [https://dicom.nema.org/medical/dicom/current/output/html/part21.html](https://dicom.nema.org/medical/dicom/current/output/html/part21.html) |


### 3.3 De-Identification (Critical When Pixels + PHI Coexist)

PS3.15 Annex E (*Basic Application Level Confidentiality Profile*) requires protecting/retaining listed attributes, maintaining dummy UID consistency across related instances, and warns that **burned-in identifiers in pixel data** and private attributes can leak identity. The profile **does not by itself guarantee** legal anonymization.

Section: [PS3.15 § Attribute Confidentiality Profiles / Annex E](https://dicom.nema.org/medical/dicom/current/output/html/part15.html#sect_E)

### 3.4 Impact Today vs. Future (DICOM → DB)


| Horizon | What Matters | Rationale |
| --- | --- | --- |
| **Today (RGB camera / YOLO / JPEG-MP4)** | DICOM IODs and Study UIDs are most likely **not** needed in tables | Tray evidence is not a radiology modality; FHIR Media/DocumentReference + object storage suffices |
| **Today (defensive design)** | Separate: (a) business metadata, (b) file URI, (c) optional nullable `sop_class` / `study_uid` | Leaves a hook without paying DICOM overhead in the MVP |
| **Future (PACS / digital OR / certification)** | Patient→Study→Series→Instance hierarchy; DICOMweb endpoints; PS3.15 profiles; FHIR `ImagingStudy` bridge | Required to interoperate with hospital PACS or export "clinical" imaging |
| **Future (AI / datasets)** | De-identification pipeline aligned with Annex E + local policy | Required for research, teaching, or third-party transfer |


**Practical conclusion:** DICOM is a **target standard** if the product enters the clinical imaging ecosystem; it is not a DB requirement for overhead RGB counting, but it is an architectural boundary worth documenting.

### 3.5 ADOPTED · DICOM in the PEF DB

**Scope decision:** the PEF system does **not** implement DICOM / PACS / DICOMweb in the core. Evidence consists of **JPEG/MP4 (and weights)** from an overhead RGB tray camera, not a radiological modality.

**What was kept in the DB (non-DICOM alternative)**


| Need | PEF Decision | Where |
| --- | --- | --- |
| Store evidence images/clips | `media_asset.kind` ∈ (`jpeg`, `mp4`, `mkv`, `webm`, `gif`, `weights`, `other`) + S3-compatible object storage (on-prem Garage) | DS05 |
| Attachment-style metadata | `gcs_uri`, `content_type`, `sha256`, `size_bytes`, `storage_provider` | DS05 |
| Detections / burst detail | MongoDB `checkpoint_frame` (not Study/Series/Instance) | [PEF-DS06](DataStandards/PEF-DS06-Mongo-Checkpoints-and-Telemetry.md) |
| Live vision | Station↔worker WSS; no C-STORE | [PEF-DS10](DataStandards/PEF-DS10-Vision-Realtime-Contracts.md) |


**Intentionally omitted from the DDL**

- No `study_uid`, `series_uid`, `sop_instance_uid`, or `sop_class`.
- No Patient→Study→Series→Instance tables.
- No PS3.15 Annex E de-identification requirement in the clinical runtime (if a DICOM dataset is ever needed, it will be an offline pipeline).

**Explicit boundary for successors:** if a hospital requires PACS integration, that is a **new phase** (ImagingStudy + DICOM), not a redesign of counting or the current `media_asset` table.

---



## 4. GDPR (Regulation EU 2016/679)

Official consolidated text: [https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679)  
Article index (helpful UK mirror): [https://www.legislation.gov.uk/eur/2016/679/contents](https://www.legislation.gov.uk/eur/2016/679/contents)

### 4.1 Articles with Direct Impact on DB Design


| Article | Operational Concept | Link |
| --- | --- | --- |
| **Art. 5** | Principles: lawfulness, minimization, accuracy, **storage limitation**, integrity/confidentiality, accountability | [Art. 5](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679#d1e1807-1-1) · mirror [legislation.gov.uk Art. 5](https://www.legislation.gov.uk/eur/2016/679/article/5) |
| **Art. 6** | Lawful basis for processing (contract, legitimate interest, legal obligation, etc.) | [Art. 6](https://www.legislation.gov.uk/eur/2016/679/article/6) |
| **Art. 9** | **Special categories**: health, genetic, biometric data — general prohibition unless an exception applies (e.g., 9(2)(h) healthcare provision, 9(2)(i) public health, 9(2)(a) explicit consent) | [Art. 9](https://www.legislation.gov.uk/eur/2016/679/article/9) |
| **Art. 15–18, 20–21** | Rights of access, rectification, erasure, portability, objection | Rights index in the same Regulation |
| **Art. 17** | Right to erasure — **yields** to legal clinical retention obligations | [Art. 17](https://www.legislation.gov.uk/eur/2016/679/article/17) |
| **Art. 25** | Privacy by design / by default | [Art. 25](https://www.legislation.gov.uk/eur/2016/679/article/25) |
| **Art. 30** | Record of processing activities | [Art. 30](https://www.legislation.gov.uk/eur/2016/679/article/30) |
| **Art. 32** | Security of processing (encryption, integrity, availability, risk assessment) | [Art. 32](https://www.legislation.gov.uk/eur/2016/679/article/32) |
| **Art. 33–34** | Breach notification | [Art. 33](https://www.legislation.gov.uk/eur/2016/679/article/33) |
| **Art. 35** | DPIA when processing poses high risk (health + vision/AI typically qualifies) | [Art. 35](https://www.legislation.gov.uk/eur/2016/679/article/35) |
| **Art. 44–49** | International transfers | Chapter V of the Regulation |



### 4.2 Translation to DB Decisions

1. **Classify columns/tables** as personal data vs. health data (special category) vs. pseudonymized vs. aggregated.
2. **Minimization:** do not persist faces, audio, or MRNs unless required for counting; prefer internal IDs and maskable display fields.
3. **Retention:** `retention_until` fields / deletion jobs; GDPR does not mandate a universal "90 days" — it mandates *necessity* + national law.
4. **Access auditing** (who read PHI) — Art. 5(1)(f) + Art. 32.
5. **Separation** of technical telemetry (FPS) from identifiable evidence.
6. **Controlled reversible pseudonymization** vs. irreversible anonymization (the latter falls outside GDPR scope if genuinely irreversible).

**Why it matters even when deployed in Mexico:** (a) EU partners / EU cloud; (b) GDPR serves as the *template* for many hospital policies; (c) transfers to vendors outside Mexico.

### 4.3 ADOPTED · GDPR (Design Template) in the PEF DB

PEF operates primarily under the Mexican legal framework; GDPR was used as a **design checklist** (minimization, retention, auditing, purpose separation), not as an EU certification.

**What was materialized**


| Principle / Art. (Guiding) | PEF Decision | Where |
| --- | --- | --- |
| Minimization | Minimal patient record; no OR audio; no radiology; worker does not transmit PII over WSS | DS02 · DS10 · architecture |
| Storage limitation | `media_asset.retention_until` / `purge_*`; MongoDB TTL; session `extended_retention` 90/180 days | DS05 · DS06 · DS04 |
| Integrity / confidentiality | `password_hash` / `secret_hash`; RBAC; JWT+Redis | DS01 · DS08 |
| Accountability / access | `access_audit` (user XOR client); distinct from `count_event` | [PEF-DS07](DataStandards/PEF-DS07-Privacy-Purposes-and-Subject-Rights.md) |
| Purposes | `cat_processing_purpose`; per-session agreement: `quality_ops` always true, `model_improvement` opt-in | DS07 |
| Privacy by design | Bytes outside PG; telemetry ≠ evidence; UI fan-out vs. SQL facts | DS04 · DS05 · DS06 |
| Telemetry / PHI separation | Short-TTL `session_telemetry`; checkpoints aligned with media retention | DS06 |


**What the DDL is not**

- Does not replace a formal DPIA, cloud DPA, or Art. 44–49 transfer mechanisms.
- No complete Art. 33–34 breach-notification module (remains operational / outside the core).

---



## 5. LFPDPPP (Mexico)



### 5.1 Current Statutory Text

- **DOF Decree March 20, 2025** (enacts the new LFPDPPP and related statutes): [https://www.dof.gob.mx/nota_detalle.php?codigo=5752569&fecha=20/03/2025](https://www.dof.gob.mx/nota_detalle.php?codigo=5752569&fecha=20/03/2025)
- Consolidated text, Mexican Chamber of Deputies (includes **DOF 2025-11-14** reform): [https://www.diputados.gob.mx/LeyesBiblio/pdf/LFPDPPP.pdf](https://www.diputados.gob.mx/LeyesBiblio/pdf/LFPDPPP.pdf)

**2025 vs. 2010 structural change:** the 2010 LFPDPPP is repealed; supervisory authority shifts (INAI → **Ministry of Anti-Corruption and Good Governance**); principles, privacy notices, and processing rules are tightened. Internal notices and policies should be updated even if the DDL does not change.

### 5.2 Statutory Articles Impacting DB / Systems

References to articles in the consolidated PDF (current 2025 numbering):


| Topic | Articles (approx.) | Data Impact |
| --- | --- | --- |
| Definitions (processing, ARCO rights, **sensitive personal data** incl. **health status**, genetic data) | Art. 2 | Tag PHI/sensitive fields in the data model and data inventory |
| Principles (lawfulness, purpose, loyalty, consent, quality, proportionality, notice, accountability) | Art. 5–13 | Explicit purposes; never reuse counting frames for marketing/training without new consent |
| Consent; **sensitive data = express and in writing** (signature / e-signature / authentication mechanism) | Art. 7–8 | Versioned consent log; not just an ephemeral checkbox |
| Exceptions (medical emergency / healthcare administration under professional secrecy, etc.) | Art. 9 | Hospital workflow need not prompt for consent on every frame, but still needs notice and documented legal basis |
| Quality and **deletion after blocking** once purpose ceases | Art. 10 | States: active → blocked → cancelled/deleted |
| Different purpose ⇒ **new consent** | Art. 11 | `purpose` fields / notice versions tied to datasets |
| Proportionality; effort to minimize retention period for sensitive data | Art. 12 | Aggressive TTL on visual evidence vs. audit metadata |
| Privacy notice (minimum contents) | Art. 14–17 | Not a clinical table, but the system must prove which purposes apply |
| Administrative/technical/physical security measures proportional to sensitivity | Art. 18 | Encryption, RBAC, environment segregation |
| Breach notification to data subjects | Art. 19 | Incident fields / playbooks (often outside the clinical core) |
| Staff confidentiality | Art. 20 | Roles and NDAs; access logs |
| **ARCO** rights (access, rectification, cancellation, opposition) | Art. 21–34 | APIs/processes that locate **all** stores (SQL + documents + object storage) by data subject |
| ARCO response timelines (e.g., 20 days + 15 days to execute) | Art. 31 | Operational SLA, not only a legal clause |
| Personal data officer / department | Art. 29 | Operational contact; not necessarily an EU-style DPO |



### 5.3 Impact Today vs. Future (LFPDPPP → DB)


| Horizon | What Matters | Rationale |
| --- | --- | --- |
| **Today** | Personal/sensitive data inventory; privacy notice; consent basis or healthcare exception; evidence retention and deletion | Operating in Mexico with real patients/staff |
| **Today** | Ability to **locate** a subject's data across PG + object store + telemetry | ARCO rights / cancellation |
| **Future** | Consent registry, pre-cancellation blocking, breach log, internal DPIA-like reviews | Hospital maturity / regulatory audit |
| **Future** | Alignment with Mexican NOM EHR standards and institutional health-sector policies | Needed if embedded into formal clinical EHR workflows |



### 5.4 ADOPTED · LFPDPPP in the PEF DB

**Decision:** the schema anticipates versioned privacy notices, per-session purposes, ARCO tracking, and multi-store blocking/purging. The **semester demo** may not exercise the full workflow; the tables already exist to avoid later redesign.

**What was materialized**


| LFPDPPP Topic | PEF Decision | Where |
| --- | --- | --- |
| Versioned privacy notice | `privacy_notice_version` (`version`, `document_uri`, `content_sha256`, `effective_at`) | DS07 |
| Purposes / no ML reuse without opt-in | `session_processing_agreement`: `purpose_quality_ops` = always true; `purpose_model_improvement` = opt-in; vocabulary `cat_processing_purpose` (incl. future `external_sharing`) | DS07 |
| Agreement not required at session creation | **0..1** agreement cardinality per `work_session` (e.g., pre-reservation) | DS04 · DS07 |
| Block → purge | `media_asset.blocked_at`, `purge_requested_at`, `purged_at` + idempotent job | DS05 · DS07 |
| ARCO | `privacy_request` (`access` / `rectify` / `cancel` / `oppose`) linked to `patient` | DS07 |
| Access audit | `access_audit` | DS07 |
| Access security | `user`/`role`/`user_role`; separate machine clients | DS01 · DS08 |
| Multi-store discovery | PG facts + MongoDB checkpoints + object storage via `media_asset_id` | DS04 · DS05 · DS06 |


**What remains operational (beyond DDL)**

- Legal text of the privacy notice and ARCO SLAs (Art. 31) require human workflows + UI.
- True cancellation requires orchestrating PG + MongoDB + bucket; the DDL enables the states and purge job.

---



## 6. Standards Crosswalk · DB Design Matrix


| Data Need | FHIR | DICOM | GDPR | LFPDPPP | **PEF ADOPTED** |
| --- | --- | --- | --- | --- | --- |
| External hospital IDs (`system`+`value`) | ● | ○ (Patient ID tags) | ○ | ○ | `resource_identifier` (DS02) |
| Minimal patient / physician | ● | ○ | ● (health = special) | ● (sensitive) | `patient` / `physician` (DS02) |
| Act / procedure / location | ● | ○ Study/Series | ○ | ○ | `operation` / room (DS02) |
| Instrument as Device | ● | ○ | ○ | ○ | `instrument` (DS03) |
| JPEG/MP4 evidence (non-radiology) | ● Attachment / DocumentReference | ○ | ● retention/minimization | ● retention/minimization | `media_asset` (DS05) |
| Evidence as PACS study | ● ImagingStudy | ● | ● | ● | **Out of scope** |
| De-identification for AI datasets | ○ | ● PS3.15 Annex E | ● anonymization | ● | ML opt-in + URI export (DS07/DS08); offline |
| Automatable retention / deletion | ○ | ○ | ● Art. 5/17 | ● Art. 10/12/24 | `retention_*` / `purge_*` / MongoDB TTL |
| PHI access auditing | ○ | ● PS3.15 audit profiles | ● Art. 32 | ● Art. 18/20 | `access_audit` (DS07) |
| Consent / purposes | ○ | ○ | ● Art. 6/9 | ● Art. 7–8/11 | agreement + purposes (DS07) |
| Interoperable HIS export | ● | ● (if imaging) | ○ | ○ | `integration_client` + projection (DS08) |


Legend: ● strong impact · ○ secondary impact or only if scope expands.

---



## 7. Status After Adoption

1. **FHIR R4** anchored: Identifier, minimal projectable resources, Attachment via `media_asset`; export in DS08.
2. **DICOM** explicitly **outside** the core; boundary documented so PACS is not forced onto RGB counting.
3. **Retention, blocking, and cancellation** modeled in `media_asset` + agreement/ARCO (DS05/DS07), aligned with LFPDPPP and the GDPR checklist.
4. **Classified vision pipeline:** facts in PG (`count_event`); detail in MongoDB; bytes in object storage; WSS free of patient PII.
5. **Checkpoint reason ≠ clinical phase ≠ privacy purpose** — three distinct axes (DS03/DS04/DS06/DS07).
6. Notices and authority: update legal texts to the **2025** law; the DDL stores `privacy_notice_version`.
7. **FHIR R6:** monitor; do not migrate the PEF model away from R4 for now.

---



## 8. Quick References



### FHIR

- R4 home: [https://hl7.org/fhir/R4/](https://hl7.org/fhir/R4/)
- Identifier: [https://hl7.org/fhir/R4/datatypes.html#Identifier](https://hl7.org/fhir/R4/datatypes.html#Identifier)
- Attachment: [https://hl7.org/fhir/R4/datatypes.html#Attachment](https://hl7.org/fhir/R4/datatypes.html#Attachment)
- Binary: [https://hl7.org/fhir/R4/binary.html](https://hl7.org/fhir/R4/binary.html)
- Media (R4): [https://hl7.org/fhir/R4/media.html](https://hl7.org/fhir/R4/media.html)
- DocumentReference (R4): [https://hl7.org/fhir/R4/documentreference.html](https://hl7.org/fhir/R4/documentreference.html)
- ImagingStudy (R4): [https://hl7.org/fhir/R4/imagingstudy.html](https://hl7.org/fhir/R4/imagingstudy.html)
- History / breaking changes R5: [https://hl7.org/fhir/history.html](https://hl7.org/fhir/history.html)
- State of FHIR 2025: [https://www.hl7.org/documentcenter/public/white-papers/2025%20State%20of%20FHIR%20Survey%20Report.pdf](https://www.hl7.org/documentcenter/public/white-papers/2025%20State%20of%20FHIR%20Survey%20Report.pdf)



### DICOM

- Current edition index: [https://www.dicomstandard.org/current](https://www.dicomstandard.org/current)
- PS3.1: [https://dicom.nema.org/medical/dicom/current/output/html/part01.html](https://dicom.nema.org/medical/dicom/current/output/html/part01.html)
- PS3.10: [https://dicom.nema.org/medical/dicom/current/output/html/part10.html](https://dicom.nema.org/medical/dicom/current/output/html/part10.html)
- PS3.15 (security / de-id): [https://dicom.nema.org/medical/dicom/current/output/html/part15.html](https://dicom.nema.org/medical/dicom/current/output/html/part15.html)
- PS3.18 (DICOMweb): [https://dicom.nema.org/medical/dicom/current/output/html/part18.html](https://dicom.nema.org/medical/dicom/current/output/html/part18.html)



### GDPR

- EUR-Lex 2016/679: [https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679)
- Art. 9 (special categories): [https://www.legislation.gov.uk/eur/2016/679/article/9](https://www.legislation.gov.uk/eur/2016/679/article/9)
- Art. 17 (erasure): [https://www.legislation.gov.uk/eur/2016/679/article/17](https://www.legislation.gov.uk/eur/2016/679/article/17)
- Art. 32 (security): [https://www.legislation.gov.uk/eur/2016/679/article/32](https://www.legislation.gov.uk/eur/2016/679/article/32)



### LFPDPPP

- DOF 2025-03-20 (decree): [https://www.dof.gob.mx/nota_detalle.php?codigo=5752569&fecha=20/03/2025](https://www.dof.gob.mx/nota_detalle.php?codigo=5752569&fecha=20/03/2025)
- Current text PDF: [https://www.diputados.gob.mx/LeyesBiblio/pdf/LFPDPPP.pdf](https://www.diputados.gob.mx/LeyesBiblio/pdf/LFPDPPP.pdf)

---
