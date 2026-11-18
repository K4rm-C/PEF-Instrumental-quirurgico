# PEF — Tracking temporal de instrumental (MS-11, R11)

Módulo: `apps/VisionWorker/tracking.py` (versión `ms11-hungarian-cv-1`). Se integra en `inference.run_frame`, tanto en el procesamiento del video como en la vista previa en vivo.

## 1. Qué hace y qué no hace

| Hace | No hace |
| :--- | :--- |
| Da a cada objeto un **identificador temporal estable** (`track_id`) entre fotogramas | **No cuenta.** El conteo inicial es el primer fotograma válido y el final, el último; el final debe ser igual a la referencia vigente (MS-03.1, sin cambios) |
| Asocia detecciones sucesivas (asignación húngara con una predicción de velocidad constante) | No reemplaza el rechazo de un final distinto: probado, un final distinto sigue respondiendo 409 aunque el tracking "coincida" |
| Sigue al objeto durante oclusiones breves y **recupera el mismo ID** | No usa el ROI para filtrar detecciones (eso cambiaría los conteos) |
| Suaviza la clase con un voto exponencial e **histéresis** (evento `class_change`) | No suma estadísticas de trayectorias en ninguna cantidad |
| Registra eventos trazables: `confirmed`, `lost`, `recovered`, `terminated`, `class_change`, `enter_roi`, `exit_roi` | |

**Antes de MS-11**, `tracks.py` (IoU + EMA) existía pero **no se llamaba** en el pipeline (solo lo usaba un script de prueba), re-numeraba los IDs en cada fotograma e importaba parámetros que ya no existían en `config.py`. Se conserva sin usar y su sustituto es `tracking.py`.

## 2. Algoritmo

1. **Predicción:** caja anterior + velocidad (EMA de la velocidad del centro) × tiempo transcurrido, sin extrapolar más de `max_extrapolate_s`.
2. **Compuerta de asociación:** un par se admite si IoU ≥ `min_iou` **o** si la distancia entre centros es ≤ `center_gate` × la diagonal predicha. Para un track sin velocidad aún, la compuerta es `new_center_gate`; para uno perdido, `lost_center_gate`. Dos detecciones confiables de **clases distintas** solo se asocian con un solapamiento ≥ 0.5, lo que evita intercambiar instrumentos vecinos.
3. **Asignación:** algoritmo húngaro (`scipy.optimize.linear_sum_assignment`) sobre el costo `1 − (IoU + 0.5·proximidad)/1.5`.
4. **Confianza:** una detección por debajo de `min_create_conf` puede actualizar un track existente, pero nunca crea uno. El valor por defecto es el **mismo umbral de detección del pipeline** (`VISION_BOX_RECALL_CONF`).
5. **Ciclo de vida:** tentativo → confirmado (`min_hits`) → perdido (sin detección durante `lost_after_s`) → recuperado (mismo ID) o terminado (pasado `forget_after_s`).
6. **Clase:** un voto exponencial (`ema_alpha`) por familia. La clase estable solo cambia si otra la supera por `class_switch_margin`.
7. **ROI:** viene de la cámara configurada en Administración, llega a VisionWorker en el *bind* firmado y se escala al tamaño real del fotograma.

Parámetros (variables `VISION_TRACK_*`), reportados en cada resumen:

| Parámetro | Valor por defecto |
| :--- | :--- |
| `min_iou` | 0.20 |
| `center_gate` | 0.60 |
| `new_center_gate` | 1.50 |
| `lost_center_gate` | 1.00 |
| `min_hits` | 3 |
| `min_create_conf` | `VISION_BOX_RECALL_CONF` (0.15) |
| `lost_after_s` | 0.40 |
| `forget_after_s` | 3.0 |
| `max_extrapolate_s` | 0.50 |
| `ema_alpha` | 0.35 |
| `class_switch_margin` | 0.15 |
| `max_events` | 500 |

## 3. Persistencia y consulta

- El estado del worker (`/sessions/{id}/state`) incluye el resumen `tracking`.
- `GET /sessions/{id}/tracking` (interno, no publicado en el Gateway) devuelve el resumen y los eventos paginados (`offset`, `limit`).
- El conteo final **aceptado** (`count_event auto_count`, `trigger = final`) guarda el resumen en `payload.tracking`, con un máximo de 200 eventos. SessionService y el portal lo proyectan (`PAYLOAD_OBJECT_KEYS`).
- El Supervisor ve en la línea de tiempo una línea con: trayectorias estables, perdidas/recuperadas, cambios de clase, y si las trayectorias activas coinciden con el fotograma final. Esto último es una señal para revisar la evidencia, **no** una regla de aceptación.

## 4. Evaluación: ¿bastaba IoU + EMA?

### 4.1 Secuencias sintéticas reproducibles (`apps/VisionWorker/tests/test_tracking.py`, 16 pruebas)

Cubren 3 objetos estáticos con ID estable, movimiento rápido sin solapamiento (IoU = 0), oclusión breve (invisible), oclusión de 1 s (el mismo ID se recupera), desaparición larga (nuevo ID y `terminated`), parpadeos de clase (sin cambio) frente a un cambio sostenido (un `class_change`), confianza baja (no crea objetos pero sí sigue uno existente), dos instrumentos que se cruzan (sin intercambio de IDs), fragmentación que nunca se vuelve conteo, eventos de ROI, ROI escalado y bitácora acotada.

### 4.2 Videos reales con el modelo provisional (`scripts/measure_tracking.py`, contenedor de VisionWorker)

Pipeline real (YOLO + asignación + tracker) sobre los videos de prueba de MS-03.1, una copia con oclusión y una copia de 10 s con oclusión de 1 s. Las clases del modelo se usan como kit (1 c/u).

| Video | Variante | Frames procesados | Tracks únicos | Activos al final = fotograma final | Perdidos / recuperados / terminados | Duración media del track (s) | ms por frame |
| :--- | :--- | ---: | ---: | :--- | :--- | ---: | ---: |
| ms031_full | MS-11 | 9 | 9 | sí (9 = 9) | 0 / 0 / 0 | 1.32 | 0.31 |
| ms031_full | solo IoU | 9 | 9 | sí | 0 / 0 / 0 | 1.32 | 0.29 |
| 10 s con oclusión de 1 s | **MS-11** | 60 | **9** | sí | 2 / **2** / 0 | **9.82** | 0.33 |
| 10 s con oclusión de 1 s | solo IoU | 60 | **11** | sí | 2 / 0 / **2** | 7.82 | 0.31 |
| ms031_one_removed | MS-11 | 12 | 8 | sí (7 = 7) | 1 / 0 / 0 | 1.69 | 0.26 |
| ms031_masked | MS-11 | 9 | 8 | sí (8 = 8) | 0 / 0 / 0 | 1.31 | 0.27 |

Conclusiones:

- **Solo IoU fragmenta** tras una oclusión: 11 IDs para 9 objetos, 2 terminados, tracks más cortos. El tracker de MS-11 **recupera los dos objetos ocultos con su mismo ID**.
- Con el umbral de creación alineado al del pipeline, las trayectorias activas al final **coinciden con el fotograma final en todos los videos**. Con un umbral de 0.30, que fue la primera versión medida, 26–35 detecciones reales quedaban sin track y el resumen marcaba diferencias falsas. Se corrigió.
- **Costo:** ~0.3 ms por fotograma procesado, despreciable frente a la inferencia.

### 4.3 Limitaciones observadas

- Los videos de prueba son **cortos y casi estáticos**: no ejercitan movimientos rápidos reales ni cambios de clase reales (estos solo se probaron con secuencias sintéticas).
- Los "tracks únicos" dependen de las detecciones del modelo **provisional**: el número de cajas varía entre fotogramas (por ejemplo, 8 → 9 en `ms031_full`) por el propio detector, no por el tracking.
- Con `VISION_FRAME_STRIDE = 5` se procesan unos 6 fotogramas por segundo. Un objeto que cruza la imagen en menos de ~0.3 s puede no confirmarse (`min_hits = 3`).
- El ROI solo genera eventos. Si se quisiera contar solo dentro del ROI, sería un cambio de regla de conteo y requiere decisión funcional.
- La calibración de cámara no está definida en los RF: no se implementó.

## 5. Pruebas

`apps/VisionWorker/tests/test_tracking.py` (16), suite de VisionWorker 72/72. En el portal, `tests/test_ms11_tracking_camera.py`: la persistencia del resumen, el total que sigue siendo el último fotograma, el final distinto que sigue rechazado y el *bind* de la cámara. También el E2E de Docker Compose (`scripts/e2e_ms11_compose.py`, pasos 6 y 9).

## MS-12: bitácora completa persistida (ms-tracking-instrumental)

- Antes la bitácora solo vivía en la memoria del worker y una copia acotada (200 eventos) en el conteo final.
- Ahora, al **aceptar** el conteo final, el portal lee del worker **todos** los eventos del recorrido (`GET /sessions/{id}/tracking`, paginado de 500, solo si el `run_id` coincide) y los guarda en `tracking_event` (migración 019), ligados al `count_event` aceptado. Las filas son **inmutables** (*trigger*).
- Si el worker no responde, se guarda la copia acotada del conteo y la bitácora queda marcada **incompleta** (`events_persisted < events_recorded`). Nunca bloquea ni cambia el conteo.
- Un conteo final **rechazado** no guarda bitácora.
- Lectura para el Supervisor: `GET /api/v1/sessions/{id}/tracking-events?offset&limit` (SessionService; `complete`, resumen y eventos) y la sección *Integridad de la validación y bitácora de seguimiento* del detalle de la sesión.
- Pruebas: `apps/BackendWebFlask/tests/test_ms12_tracking_events.py`, `apps/SessionService/tests/test_ms12_tracking_events.py`, E2E Compose M5.
