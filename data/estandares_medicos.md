# Estándares médicos y privacidad · Investigación y decisiones de BD

**Parte investigación:** qué aspectos de cada estándar suelen impactar el diseño de datos.  
**Parte REDACTADO:** qué se adoptó ya en el esquema PEF (`esquema_base_datos_v2.md`), el ER y los [Data Standards](DataStandards/README.md).

**Fecha de elaboración:** septiembre 2026 · **Última actualización REDACTADO:** septiembre 2026  
**Alcance:** HL7 FHIR, DICOM, GDPR (UE) y LFPDPPP (México)  
**Aviso:** este documento es técnico-informativo; no constituye asesoría legal.

---

## 1. Criterio de versión usado


| Estándar         | Versión de trabajo recomendada (hoy)                                                                   | Alternativa / horizonte                                     | Por qué                                                                                                                                                                                                             |
| ---------------- | ------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **HL7 FHIR**     | **R4** (v4.0.1), núcleo normativo                                                                      | R5 publicada (2023) con poca adopción; **R6** en ballot     | R4 es la línea base regulatoria y de proveedores EHR; la encuesta global HL7/Firely 2025 sigue mostrando R4 como versión principal más citada; US Core y mandatos ONC/CMS apuntan a R4 y planean saltar R5 hacia R6 |
| **DICOM**        | Edición **current** (p. ej. PS3 · 2026c en HTML “current”)                                             | No fijar “año” en contratos de datos; enlazar a `/current/` | DICOM se republica varias veces al año; el estándar oficial es la edición más reciente + supplements Final Text                                                                                                     |
| **GDPR**         | Reglamento (UE) **2016/679** (vigente desde 2018)                                                      | Sin “versión” sucesora equivalente                          | Marco estable; cambios vía interpretaciones/directivas nacionales                                                                                                                                                   |
| **LFPDPPP (Mx)** | **Nueva ley** DOF **20-mar-2025** (vigente 21-mar-2025); texto consolidado con reforma **14-nov-2025** | Ley 2010 abrogada                                           | Cambio de autoridad (de INAI a Secretaría Anticorrupción y Buen Gobierno) y endurecimiento de consentimiento/finalidades                                                                                            |


---

## 2. HL7 FHIR

### 2.1 Versiones y cambios relevantes entre R4 → R5 → (R6)

- **R4 (2019):** primera release con núcleo *normative*; base de producción e IGs (p. ej. US Core sobre R4). Spec: [https://hl7.org/fhir/R4/](https://hl7.org/fhir/R4/)
- **R4B (2022):** puente selectivo; no sustituye a R4 como línea base. [https://hl7.org/fhir/R4B/](https://hl7.org/fhir/R4B/)
- **R5 (2023):** mejoras (p. ej. Subscriptions, razonamiento clínico); **adopción EHR aún baja**. Historial de cambios: [https://hl7.org/fhir/history.html](https://hl7.org/fhir/history.html) — entre otros, **se elimina** `Media`; se indica usar `DocumentReference` **u** `Observation`.
- **R6:** en proceso de ballot normativo (2026); horizonte de publicación final típicamente 2026–2027. Ballot / drafts: [https://hl7.org/fhir/](https://hl7.org/fhir/) (seguir índice de versiones publicadas). En EE. UU., señales de comunidad indican que **US Core saltaría R5 y apuntaría a R6**.

**Implicación para BD:** modelar contratos de intercambio en **vocabulario R4** (recursos y datatypes estables). Si a futuro se exporta evidencia visual, preferir ya un ancla compatible con `DocumentReference` **+** `Attachment` (válido en R4 y alineado a R5+) en lugar de depender solo de `Media`.

### 2.2 Datatypes y recursos que suelen tocar el modelo de datos


| Pieza FHIR                              | Qué aporta a una BD                                                                                  | Página exacta                                                                                                                                                                                     |
| --------------------------------------- | ---------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Identifier** (`system` URI + `value`) | Diccionario de IDs externos (HIS/MRN/staff) ↔ UUID local; búsqueda `system                           | value` antes de insertar                                                                                                                                                                          |
| **Patient**                             | Paciente mínimo; no obliga a EHR completo                                                            | [https://hl7.org/fhir/R4/patient.html](https://hl7.org/fhir/R4/patient.html)                                                                                                                      |
| **Practitioner**                        | Médico / profesional                                                                                 | [https://hl7.org/fhir/R4/practitioner.html](https://hl7.org/fhir/R4/practitioner.html)                                                                                                            |
| **Location**                            | Sala / quirófano                                                                                     | [https://hl7.org/fhir/R4/location.html](https://hl7.org/fhir/R4/location.html)                                                                                                                    |
| **Encounter**                           | Episodio de atención / contexto temporal                                                             | [https://hl7.org/fhir/R4/encounter.html](https://hl7.org/fhir/R4/encounter.html)                                                                                                                  |
| **Procedure**                           | Acto/procedimiento quirúrgico                                                                        | [https://hl7.org/fhir/R4/procedure.html](https://hl7.org/fhir/R4/procedure.html)                                                                                                                  |
| **Device**                              | Dispositivo / instrumento trazable por pieza                                                         | [https://hl7.org/fhir/R4/device.html](https://hl7.org/fhir/R4/device.html)                                                                                                                        |
| **Attachment**                          | Metadatos de archivo (`contentType`, `url`, `size`, `hash`, `title`) sin embeber bytes               | [https://hl7.org/fhir/R4/datatypes.html#Attachment](https://hl7.org/fhir/R4/datatypes.html#Attachment)                                                                                            |
| **Binary**                              | Contenedor de bytes o puente REST; **sin contexto clínico**; control de acceso vía `securityContext` | [https://hl7.org/fhir/R4/binary.html](https://hl7.org/fhir/R4/binary.html)                                                                                                                        |
| **Media** (R4)                          | Imagen/video/audio **no DICOM** con metadatos; en R5 eliminado                                       | [https://hl7.org/fhir/R4/media.html](https://hl7.org/fhir/R4/media.html)                                                                                                                          |
| **DocumentReference**                   | Índice buscable de documentos/evidencia (camino R5+)                                                 | [https://hl7.org/fhir/R4/documentreference.html](https://hl7.org/fhir/R4/documentreference.html) · R5: [https://hl7.org/fhir/documentreference.html](https://hl7.org/fhir/documentreference.html) |
| **ImagingStudy**                        | Estudios **DICOM** (WADO-RS / endpoints); distinto de Media                                          | [https://hl7.org/fhir/R4/imagingstudy.html](https://hl7.org/fhir/R4/imagingstudy.html)                                                                                                            |


**Límite explícito FHIR (ImagingStudy vs Media):** *“ImagingStudy is used for DICOM imaging… Use Media to track non-DICOM images, video, or audio.”* — ver sección Boundaries en ImagingStudy (enlace arriba).

### 2.3 Impacto hoy vs futuro (FHIR → BD)


| Horizonte  | Qué interesa a la BD                                                                                                                                                       | Por qué                                                                             |
| ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| **Hoy**    | Tabla/contrato de identificadores externos (`system`+`value`+tipo de recurso); mapeo Patient/Practitioner/Location/Procedure/Device a UUIDs locales                        | Permite integración HIS sin duplicar filas; es el datatype más usado en intercambio |
| **Hoy**    | Guardar **URI + hash + contentType** de evidencia, no blobs en filas clínicas                                                                                              | Alineado a Attachment/Binary; facilita export FHIR sin re-arquitectura              |
| **Futuro** | Capas de exportación FHIR REST o mensajes Bundle; perfiles nacionales                                                                                                      | Cuando exista HIS conectado o certificación de interoperabilidad                    |
| **Futuro** | Si la evidencia cenital se eleva a “estudio de imagen clínica”, valorar **ImagingStudy + DICOM**; si sigue siendo JPEG/GIF de tray, **DocumentReference/Attachment** basta | Evita over-engineering DICOM en MVP de visión por cámara RGB                        |


**Citación de adopción:** [2025 State of FHIR Survey (HL7 / Firely)](https://www.hl7.org/documentcenter/public/white-papers/2025%20State%20of%20FHIR%20Survey%20Report.pdf) — R4 sigue siendo la versión principal más reportada frente a R5.

### 2.4 REDACTADO · FHIR en la BD PEF

**Decisión de versión:** contratos e identificadores anclados a **FHIR R4**. No se modela R5/`Media` eliminado como dependencia.

**Qué quedó implementado en esquema / estándares**


| Idea FHIR R4                          | Decisión PEF                                                                                 | Dónde                                                                             |
| ------------------------------------- | -------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| Identifier (`system` + `value` + use) | Tabla `resource_identifier` (sin FK polimórfica); índice único parcial de activos            | DDL §5.3 · [PEF-DS02](DataStandards/PEF-DS02-Clinical-Context-and-Identifiers.md) |
| Patient mínimo                        | `patient` (`display_name`, `birth_date`, `gender_id`, `institution_id`)                      | DS02                                                                              |
| Practitioner                          | `physician` + `physician_specialty` + rol quirúrgico en `operation_physician`                | DS02                                                                              |
| Location                              | `operating_room` (+ `capture_station` como punto de captura)                                 | DS02                                                                              |
| Encounter / Procedure                 | `operation` + `cat_procedure_type` / `cat_operation_status`                                  | DS02                                                                              |
| Device (pieza)                        | `instrument` + familia; Identifier puede tipar `instrument`                                  | DS03 · DS02                                                                       |
| Attachment / Binary (no DICOM)        | `media_asset`: URI, `content_type`, `sha256`, `size_bytes`, `kind` — bytes en object storage | [PEF-DS05](DataStandards/PEF-DS05-Evidence-and-Object-Storage.md)                 |
| Cliente máquina / export              | `integration_client` + scopes; proyección FHIR (no segundo modelo)                           | [PEF-DS08](DataStandards/PEF-DS08-Machine-Clients-and-FHIR-Export.md)             |


**Qué quedó fuera a propósito**

- No hay tablas IOD / `ImagingStudy` / Study Instance UID.
- No se persiste un recurso FHIR completo como fuente de verdad: PG es canónico; FHIR es **proyección de export**.
- `DocumentReference` no es tabla propia: se puede proyectar desde `media_asset` + sesión cuando se exponga export.
- Demo de semestre: export FHIR completo y Bundle son **capacitados** en DS08; no bloquean el núcleo de conteo.

---

## 3. DICOM

### 3.1 Qué es y edición vigente

DICOM (NEMA PS3 / ISO 12052) es el estándar de **comunicación y gestión de información de imagen médica** y datos relacionados. La edición vigente se consulta siempre en:

- Portal current: [https://www.dicomstandard.org/current](https://www.dicomstandard.org/current)
- PS3.1 Introduction (HTML current, p. ej. 2026c): [https://dicom.nema.org/medical/dicom/current/output/html/part01.html](https://dicom.nema.org/medical/dicom/current/output/html/part01.html)

**Cambio entre “versiones”:** no hay major version estilo software; hay **ediciones fechadas** (2025d, 2026a/b/c…) y supplements. Para BD, referenciar *capacidades* (IOD, tags, perfiles de seguridad, DICOMweb), no un número de año congelado en el DDL.

### 3.2 Partes con impacto potencial en datos


| Parte                                    | Relevancia para BD / almacenamiento                                                           | Enlace                                                                                                                                       |
| ---------------------------------------- | --------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| **PS3.3** Information Object Definitions | Define objetos (Patient/Study/Series/Instance) y atributos; modelo jerárquico clásico de PACS | [https://dicom.nema.org/medical/dicom/current/output/html/part03.html](https://dicom.nema.org/medical/dicom/current/output/html/part03.html) |
| **PS3.6** Data Dictionary                | Tags numéricos (p. ej. Patient ID, Study Instance UID)                                        | [https://dicom.nema.org/medical/dicom/current/output/html/part06.html](https://dicom.nema.org/medical/dicom/current/output/html/part06.html) |
| **PS3.10** Media Storage / File Format   | Archivo `.dcm`, File Meta Information, preamble 128 bytes                                     | [https://dicom.nema.org/medical/dicom/current/output/html/part10.html](https://dicom.nema.org/medical/dicom/current/output/html/part10.html) |
| **PS3.15** Security                      | Perfiles de seguridad, auditoría, **Attribute Confidentiality / de-identification** (Annex E) | [https://dicom.nema.org/medical/dicom/current/output/html/part15.html](https://dicom.nema.org/medical/dicom/current/output/html/part15.html) |
| **PS3.18** Web Services (DICOMweb)       | WADO-RS / STOW-RS / QIDO-RS: APIs HTTP modernas vs C-STORE clásico                            | [https://dicom.nema.org/medical/dicom/current/output/html/part18.html](https://dicom.nema.org/medical/dicom/current/output/html/part18.html) |
| **PS3.21** Transformations               | Transformaciones DICOM ↔ otras representaciones (puente con FHIR/HL7)                         | [https://dicom.nema.org/medical/dicom/current/output/html/part21.html](https://dicom.nema.org/medical/dicom/current/output/html/part21.html) |


### 3.3 De-identificación (crítico si hay pixels + PHI)

PS3.15 Annex E (*Basic Application Level Confidentiality Profile*) exige proteger/retener atributos listados, coherencia de UIDs dummy entre instancias relacionadas, y advierte que **identificar burned-in en pixel data** y atributos privados puede filtrar identidad. El perfil **no garantiza** por sí solo el anonimato legal.

Sección: [PS3.15 § Attribute Confidentiality Profiles / Annex E](https://dicom.nema.org/medical/dicom/current/output/html/part15.html#sect_E)

### 3.4 Impacto hoy vs futuro (DICOM → BD)


| Horizonte                                             | Qué interesa                                                                                             | Por qué                                                                                               |
| ----------------------------------------------------- | -------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| **Hoy (cámara RGB / YOLO / JPEG-GIF)**                | Probablemente **no** hace falta IOD DICOM ni Study UID en tablas                                         | La evidencia de tray no es modalidad radiológica; FHIR Media/DocumentReference + object storage basta |
| **Hoy (diseño defensivo)**                            | Separar: (a) metadatos de negocio, (b) URI de archivo, (c) eventual `sop_class` / `study_uid` nullable   | Deja gancho sin pagar costo DICOM en MVP                                                              |
| **Futuro (PACS / quirófano digital / certificación)** | Jerarquía Patient→Study→Series→Instance; endpoints DICOMweb; perfiles PS3.15; puente FHIR `ImagingStudy` | Interoperar con PACS hospitalario o exportar evidencia “clínica”                                      |
| **Futuro (IA / datasets)**                            | Pipeline de de-identificación alineado a Annex E + política local                                        | Requisitos de investigación, docencia o transferencia a terceros                                      |


**Conclusión práctica:** DICOM es **estándar de destino** si el producto se inserta en el ecosistema de imagen clínica; no es requisito de BD para un conteo cenital RGB, pero sí una frontera de arquitectura a documentar.

### 3.5 REDACTADO · DICOM en la BD PEF

**Decisión de alcance:** el sistema PEF **no** implementa DICOM / PACS / DICOMweb en el núcleo. La evidencia es **JPEG/GIF (y pesos)** de charola cenital RGB, no modalidad radiológica.

**Qué quedó en BD (alternativa no DICOM)**


| Necesidad                       | Decisión PEF                                                                        | Dónde                                                                 |
| ------------------------------- | ----------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| Guardar imagen de evidencia     | `media_asset.kind` ∈ (`jpeg`, `gif`, `weights`, `other`) + object storage GCS/MinIO | DS05                                                                  |
| Metadatos tipo Attachment       | `gcs_uri`, `content_type`, `sha256`, `size_bytes`, `storage_provider`               | DS05                                                                  |
| Detalle de detecciones / ráfaga | Mongo `checkpoint_frame` (no Study/Series/Instance)                                 | [PEF-DS06](DataStandards/PEF-DS06-Mongo-Checkpoints-and-Telemetry.md) |
| Visión en vivo                  | WSS estación↔worker; sin C-STORE                                                    | [PEF-DS10](DataStandards/PEF-DS10-Vision-Realtime-Contracts.md)       |


**Qué no se añadió al DDL (a propósito)**

- Sin `study_uid`, `series_uid`, `sop_instance_uid`, `sop_class`.
- Sin tablas Patient→Study→Series→Instance.
- Sin obligación de de-identificación PS3.15 Annex E en el runtime clínico (si un día hay dataset DICOM, será pipeline offline).

**Frontera explícita para herederos:** si un hospital exige PACS, eso es **fase nueva** (ImagingStudy + DICOM), no un rediseño del conteo ni de `media_asset` actual.

---

## 4. GDPR (Reglamento UE 2016/679)

Texto consolidado oficial: [https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679)  
Índice por artículos (versión UK mirror útil): [https://www.legislation.gov.uk/eur/2016/679/contents](https://www.legislation.gov.uk/eur/2016/679/contents)

### 4.1 Artículos con impacto directo en diseño de BD


| Artículo              | Idea operativa                                                                                                                                                                                            | Enlace                                                                                                                                                                                 |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Art. 5**            | Principios: licitud, minimización, exactitud, **limitación de conservación**, integridad/confidencialidad, responsabilidad                                                                                | [Art. 5](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679#d1e1807-1-1) · mirror [legislation.gov.uk Art. 5](https://www.legislation.gov.uk/eur/2016/679/article/5) |
| **Art. 6**            | Base legal del tratamiento (contrato, interés legítimo, obligación legal, etc.)                                                                                                                           | [Art. 6](https://www.legislation.gov.uk/eur/2016/679/article/6)                                                                                                                        |
| **Art. 9**            | **Categorías especiales**: datos de salud, genéticos, biométricos… — prohibición general salvo excepciones (p. ej. 9(2)(h) asistencia sanitaria, 9(2)(i) salud pública, 9(2)(a) consentimiento explícito) | [Art. 9](https://www.legislation.gov.uk/eur/2016/679/article/9)                                                                                                                        |
| **Art. 15–18, 20–21** | Derechos de acceso, rectificación, borrado, portabilidad, oposición                                                                                                                                       | Índice derechos en el mismo Reglamento                                                                                                                                                 |
| **Art. 17**           | Derecho de supresión — **cede** ante obligaciones legales de conservación clínica                                                                                                                         | [Art. 17](https://www.legislation.gov.uk/eur/2016/679/article/17)                                                                                                                      |
| **Art. 25**           | Privacy by design / by default                                                                                                                                                                            | [Art. 25](https://www.legislation.gov.uk/eur/2016/679/article/25)                                                                                                                      |
| **Art. 30**           | Registro de actividades de tratamiento                                                                                                                                                                    | [Art. 30](https://www.legislation.gov.uk/eur/2016/679/article/30)                                                                                                                      |
| **Art. 32**           | Seguridad del tratamiento (cifrado, integridad, disponibilidad, evaluación de riesgo)                                                                                                                     | [Art. 32](https://www.legislation.gov.uk/eur/2016/679/article/32)                                                                                                                      |
| **Art. 33–34**        | Notificación de brechas                                                                                                                                                                                   | [Art. 33](https://www.legislation.gov.uk/eur/2016/679/article/33)                                                                                                                      |
| **Art. 35**           | DPIA cuando el tratamiento es de alto riesgo (salud + visión/IA suele calificar)                                                                                                                          | [Art. 35](https://www.legislation.gov.uk/eur/2016/679/article/35)                                                                                                                      |
| **Art. 44–49**        | Transferencias internacionales                                                                                                                                                                            | Capítulo V del Reglamento                                                                                                                                                              |


### 4.2 Traducción a decisiones de BD

1. **Clasificar columnas/tablas** como dato personal vs dato de salud (categoría especial) vs seudonimizado vs agregado.
2. **Minimización:** no persistir rostros, audio, ni MRN si no son necesarios para el conteo; preferir IDs internos y display enmascarable.
3. **Retención:** campos `retention_until` / jobs de borrado; el GDPR no fija “90 días” universales — fija *necesidad* + ley nacional.
4. **Auditoría de acceso** (quién leyó PHI) — Art. 5(1)(f) + 32.
5. **Separación** de telemetría técnica (FPS) vs evidencia nominativa.
6. **Seudonimización** reversible bajo control vs anonimización irreversible (esta última sale del ámbito GDPR si es verdadera).

**Por qué importa aunque el despliegue sea México:** (a) socios UE / cloud EU; (b) GDPR es el *template* de muchas políticas hospitalarias; (c) transferencia a proveedores fuera de México.

### 4.3 REDACTADO · GDPR (plantilla de diseño) en la BD PEF

PEF opera primero bajo marco mexicano; GDPR se usó como **checklist de diseño** (minimización, retención, auditoría, separación de finalidades), no como certificación UE.

**Qué quedó materializado**


| Principio / Art. (orientativo) | Decisión PEF                                                                                         | Dónde                                                                     |
| ------------------------------ | ---------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| Minimización                   | Paciente mínimo; sin audio de quirófano; sin radiología; worker no escribe PII en WSS                | DS02 · DS10 · arquitectura                                                |
| Limitación de conservación     | `media_asset.retention_until` / `purge_*`; Mongo TTL; sesión `extended_retention` 90/180 días        | DS05 · DS06 · DS04                                                        |
| Integridad / confidencialidad  | `password_hash` / `secret_hash`; RBAC; JWT+Redis                                                     | DS01 · DS08                                                               |
| Responsabilidad / acceso       | `access_audit` (user xor client); distinto de `count_event`                                          | [PEF-DS07](DataStandards/PEF-DS07-Privacy-Purposes-and-Subject-Rights.md) |
| Finalidades                    | `cat_processing_purpose`; acuerdo por sesión: `quality_ops` siempre true, `model_improvement` opt-in | DS07                                                                      |
| Privacy by design              | Bytes fuera de PG; telemetría ≠ evidencia; fan-out UI vs hechos SQL                                  | DS04 · DS05 · DS06                                                        |
| Separación telemetría / PHI    | `session_telemetry` TTL corto; checkpoints con retención alineada a media                            | DS06                                                                      |


**Qué no es el DDL**

- No sustituye DPIA formal, DPA con cloud ni mecanismos de transferencia Art. 44–49.
- No hay módulo completo de notificación de brechas Art. 33–34 (queda operativo/fuera del núcleo).

---

## 5. LFPDPPP (México)

### 5.1 Texto vigente

- **Decreto DOF 20 de marzo de 2025** (expide la nueva LFPDPPP y otras leyes): [https://www.dof.gob.mx/nota_detalle.php?codigo=5752569&fecha=20/03/2025](https://www.dof.gob.mx/nota_detalle.php?codigo=5752569&fecha=20/03/2025)
- Texto consolidado Cámara de Diputados (incluye reforma **DOF 14-11-2025**): [https://www.diputados.gob.mx/LeyesBiblio/pdf/LFPDPPP.pdf](https://www.diputados.gob.mx/LeyesBiblio/pdf/LFPDPPP.pdf)

**Cambio estructural 2025 vs ley 2010:** se abroga la LFPDPPP de 2010; se redistribuye la autoridad de supervisión (INAI → **Secretaría Anticorrupción y Buen Gobierno**); se precisan principios, aviso de privacidad y tratamiento. Conviene actualizar avisos y políticas internas aunque el DDL no cambie.

### 5.2 Artículos de la ley con impacto en BD / sistemas

Referencias al articulado del PDF consolidado (numeración vigente 2025):


| Tema                                                                                                              | Artículos (aprox.) | Impacto en datos                                                                                             |
| ----------------------------------------------------------------------------------------------------------------- | ------------------ | ------------------------------------------------------------------------------------------------------------ |
| Definiciones (tratamiento, derechos ARCO, **datos personales sensibles** incl. **estado de salud**, genéticos)    | Art. 2             | Etiquetar PHI/sensibles en el modelo y en el inventario de datos                                             |
| Principios (licitud, finalidad, lealtad, consentimiento, calidad, proporcionalidad, información, responsabilidad) | Art. 5–13          | Finalidades explícitas; no reutilizar frames de conteo para marketing/entrenamiento sin nuevo consentimiento |
| Consentimiento; **sensibles = expreso y por escrito** (firma / e-firma / mecanismo de autenticación)              | Art. 7–8           | Bitácora de consentimiento versionada; no solo checkbox efímero                                              |
| Excepciones (urgencia médica / gestión sanitaria con secreto profesional, etc.)                                   | Art. 9             | Flujo hospitalario puede no pedir consentimiento en cada frame, pero sí aviso y base legal documentada       |
| Calidad y **supresión tras bloqueo** cuando cesa la finalidad                                                     | Art. 10            | Estados: activo → bloqueado → cancelado/suprimido                                                            |
| Finalidad distinta ⇒ **nuevo consentimiento**                                                                     | Art. 11            | Campos `purpose` / versiones de aviso ligados a datasets                                                     |
| Proporcionalidad; esfuerzo por minimizar periodo de sensibles                                                     | Art. 12            | TTL agresivo en evidencia visual vs metadatos de auditoría                                                   |
| Aviso de privacidad (contenido mínimo)                                                                            | Art. 14–17         | No es tabla clínica, pero el sistema debe poder demostrar qué finalidades aplican                            |
| Medidas de seguridad admin/técnicas/físicas; proporcional a sensibilidad                                          | Art. 18            | Cifrado, RBAC, segregación de ambientes                                                                      |
| Aviso de vulneraciones al titular                                                                                 | Art. 19            | Campos para incidentes / playbooks (a menudo fuera del núcleo clínico)                                       |
| Confidencialidad del personal                                                                                     | Art. 20            | Roles y NDAs; logs de acceso                                                                                 |
| Derechos **ARCO** (acceso, rectificación, cancelación, oposición)                                                 | Art. 21–34         | APIs/procesos que localicen **todos** los almacenes (SQL + documentos + object storage) por titular          |
| Plazos respuesta ARCO (p. ej. 20 días + 15 para hacer efectivo)                                                   | Art. 31            | SLA operativo, no solo legal                                                                                 |
| Persona/departamento de datos personales                                                                          | Art. 29            | Contacto operativo; no necesariamente DPO al estilo GDPR                                                     |


### 5.3 Impacto hoy vs futuro (LFPDPPP → BD)


| Horizonte  | Qué interesa                                                                                                                    | Por qué                                           |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| **Hoy**    | Inventario de datos personales/sensibles; aviso; base de consentimiento o excepción sanitaria; retención y borrado de evidencia | Operar en México con pacientes/staff reales       |
| **Hoy**    | Capacidad de **localizar** datos del titular en PG + object store + telemetría                                                  | Derechos ARCO / cancelación                       |
| **Futuro** | Registro de consentimientos, bloqueo previo a cancelación, bitácora de vulneraciones, DPIA-like interno                         | Madurez hospitalaria / auditoría de la Secretaría |
| **Futuro** | Alineación con NOM de expediente clínico electrónico y políticas institucionales (sector salud)                                 | Si el producto se embebe en flujo clínico formal  |


### 5.4 REDACTADO · LFPDPPP en la BD PEF

**Decisión:** el esquema anticipa aviso versionado, finalidades por sesión, ARCO y bloqueo/purga multi-almacén. La **demo de semestre** puede no ejercer el flujo completo; las tablas ya existen para no rediseñar después.

**Qué quedó materializado**


| Tema LFPDPPP                               | Decisión PEF                                                                                                                                                                       | Dónde              |
| ------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------ |
| Aviso de privacidad versionado             | `privacy_notice_version` (`version`, `document_uri`, `content_sha256`, `effective_at`)                                                                                             | DS07               |
| Finalidades / no reusar para ML sin opt-in | `session_processing_agreement`: `purpose_quality_ops` = siempre true; `purpose_model_improvement` = opt-in; vocabulario `cat_processing_purpose` (incl. `external_sharing` futuro) | DS07               |
| Acuerdo no obligado al crear sesión        | Cardinalidad **0..1** acuerdo por `work_session` (p. ej. reserva previa)                                                                                                           | DS04 · DS07        |
| Bloqueo → purga                            | `media_asset.blocked_at`, `purge_requested_at`, `purged_at` + job idempotente                                                                                                      | DS05 · DS07        |
| ARCO                                       | `privacy_request` (`access` / `rectify` / `cancel` / `oppose`) ligado a `patient`                                                                                                  | DS07               |
| Auditoría de acceso                        | `access_audit`                                                                                                                                                                     | DS07               |
| Seguridad de acceso                        | `user`/`role`/`user_role`; clientes máquina separados                                                                                                                              | DS01 · DS08        |
| Localización multi-almacén                 | PG hechos + Mongo checkpoints + object storage vía `media_asset_id`                                                                                                                | DS04 · DS05 · DS06 |


**Qué queda operativo (no solo DDL)**

- Texto legal del aviso y plazos ARCO (Art. 31) son proceso humano + UI.
- Cancelación real exige orquestar PG + Mongo + bucket; el DDL solo habilita estados y el job.

---

## 6. Cruce de estándares · matriz para diseño de BD


| Necesidad de datos                       | FHIR                             | DICOM                   | GDPR                     | LFPDPPP                  | **REDACTADO PEF**                           |
| ---------------------------------------- | -------------------------------- | ----------------------- | ------------------------ | ------------------------ | ------------------------------------------- |
| IDs externos hospital (`system`+`value`) | ●                                | ○ (Patient ID tags)     | ○                        | ○                        | `resource_identifier` (DS02)                |
| Paciente / médico mínimos                | ●                                | ○                       | ● (salud = especial)     | ● (sensibles)            | `patient` / `physician` (DS02)              |
| Acto / procedimiento / ubicación         | ●                                | ○ Study/Series          | ○                        | ○                        | `operation` / sala (DS02)                   |
| Instrumento como Device                  | ●                                | ○                       | ○                        | ○                        | `instrument` (DS03)                         |
| Evidencia JPEG/GIF (no radiología)       | ● Attachment / DocumentReference | ○                       | ● retención/minimización | ● retención/minimización | `media_asset` (DS05)                        |
| Evidencia como estudio PACS              | ● ImagingStudy                   | ●                       | ●                        | ●                        | **Fuera de alcance**                        |
| De-identificación para datasets IA       | ○                                | ● PS3.15 Annex E        | ● anonimización          | ●                        | Opt-in ML + export URI (DS07/DS08); offline |
| Retención / borrado automatizable        | ○                                | ○                       | ● Art. 5/17              | ● Art. 10/12/24          | `retention_*` / `purge_*` / TTL Mongo       |
| Auditoría de acceso a PHI                | ○                                | ● audit profiles PS3.15 | ● Art. 32                | ● Art. 18/20             | `access_audit` (DS07)                       |
| Consentimiento / finalidades             | ○                                | ○                       | ● Art. 6/9               | ● Art. 7–8/11            | acuerdo + purposes (DS07)                   |
| Export interoperable HIS                 | ●                                | ● (si imagen)           | ○                        | ○                        | `integration_client` + proyección (DS08)    |


Leyenda: ● impacto fuerte · ○ impacto secundario o solo si se amplía el alcance.

---

## 7. Estado tras REDACTADO

1. **FHIR R4** anclado: Identifier, recursos mínimos proyectables, Attachment vía `media_asset`; export en DS08.
2. **DICOM** explícitamente **fuera** del núcleo; frontera documentada para no forzar PACS en el conteo RGB.
3. **Retención, bloqueo y cancelación** modelados en `media_asset` + acuerdo/ARCO (DS05/DS07), alineados a LFPDPPP y checklist GDPR.
4. **Visión clasificada:** hechos en PG (`count_event`); detalle en Mongo; bytes en object storage; WSS sin PII de paciente.
5. **Motivo de checkpoint ≠ fase clínica ≠ finalidad de privacidad** — tres ejes distintos (DS03/DS04/DS06/DS07).
6. Avisos y autoridad: actualizar textos legales a ley **2025**; el DDL guarda `privacy_notice_version`.
7. **FHIR R6:** monitorear; no migrar el modelo PEF desde R4 por ahora.

---

## 8. Referencias rápidas

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
- Art. 9 (categorías especiales): [https://www.legislation.gov.uk/eur/2016/679/article/9](https://www.legislation.gov.uk/eur/2016/679/article/9)
- Art. 17 (supresión): [https://www.legislation.gov.uk/eur/2016/679/article/17](https://www.legislation.gov.uk/eur/2016/679/article/17)
- Art. 32 (seguridad): [https://www.legislation.gov.uk/eur/2016/679/article/32](https://www.legislation.gov.uk/eur/2016/679/article/32)

### LFPDPPP

- DOF 20/03/2025 (decreto): [https://www.dof.gob.mx/nota_detalle.php?codigo=5752569&fecha=20/03/2025](https://www.dof.gob.mx/nota_detalle.php?codigo=5752569&fecha=20/03/2025)
- Texto vigente PDF: [https://www.diputados.gob.mx/LeyesBiblio/pdf/LFPDPPP.pdf](https://www.diputados.gob.mx/LeyesBiblio/pdf/LFPDPPP.pdf)

---

