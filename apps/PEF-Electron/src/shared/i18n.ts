import type { MonitorSnapshot, UiPhase } from './types';

export type Locale = MonitorSnapshot['locale'];

const en: Record<string, string> = {
  'Estación sin verificar': 'Station not verified',
  'Sesión': 'Session', 'Opciones': 'Options', 'DEMOSTRACIÓN · SIMULADO': 'DEMO · SIMULATED',
  'Paciente': 'Patient', 'Procedimiento': 'Procedure', 'Doctor': 'Doctor',
  'No disponible': 'Unavailable', 'Sin datos': 'No data',
  'Captura de video pregrabado': 'Recorded video capture', 'Captura de cámara': 'Camera capture',
  'Video procesado con detecciones de YOLO': 'Processed video with YOLO detections',
  'Último fotograma procesado por VisionWorker con detecciones': 'Latest frame processed by VisionWorker with detections',
  'Sin actualización · imagen no actual': 'No update · stale image',
  'Video pregrabado — Detecciones de YOLO': 'Recorded video — YOLO detections',
  'Captura histórica': 'Recorded capture', 'Observación en vivo': 'Live monitoring',
  'Fase de captura:': 'Capture phase:', 'Verificación final': 'Final verification', 'Inicial': 'Initial',
  'Procesamiento:': 'Processing:', 'Actualizado:': 'Updated:', 'pendiente': 'pending',
  'Sesión en modo manual: sin video.': 'Manual session: no video.',
  'Video en vivo no disponible en el servidor.': 'Live video is unavailable on the server.',
  'Video sin autorización.': 'Video access denied.',
  'VisionWorker no tiene esta sesión cargada.': 'VisionWorker does not have this session loaded.',
  'Conectando con VisionWorker…': 'Connecting to VisionWorker…',
  'Procesando video pregrabado con YOLO. Esperando los primeros fotogramas.': 'Processing recorded video with YOLO. Waiting for the first frames.',
  'Esperando que el Operador inicie la captura en el portal.': 'Waiting for the operator to start capture in the portal.',
  'Verde': 'Green', 'Amarillo': 'Yellow', 'Rojo': 'Red', 'Gris': 'Gray',
  'Conteo observado sobre referencia': 'Observed count versus reference',
  'Referencia vigente:': 'Current reference:', 'Sin conteo observado confiable': 'No reliable observed count',
  'Sin diferencia': 'No difference', 'CONFIRMADO OFICIALMENTE': 'OFFICIALLY CONFIRMED',
  'LECTURA PROVISIONAL · NO AUTORIZA CIERRE': 'PROVISIONAL READING · DOES NOT AUTHORIZE CLOSURE',
  'LECTURA PROVISIONAL': 'PROVISIONAL READING', 'estabilizando…': 'stabilizing…',
  'Sistema:': 'System:', 'Fuente:': 'Source:', 'Última sincronización:': 'Last sync:',
  'Registrada:': 'Registered:', 'simulada': 'simulated',
  'Sin cámaras registradas en Administración': 'No cameras registered in Administration',
  'dispositivo de video detectado en este equipo': 'video device detected on this computer',
  'ningún dispositivo de video en este equipo': 'no video device on this computer',
  'último reporte del portal:': 'last portal report:',
  'Estación': 'Station', 'Varias sesiones activas': 'Several active sessions',
  'Esperando sesión del Operador': 'Waiting for the operator session', 'iniciada': 'started',
  'Conexión': 'Connection', 'Cámara': 'Camera', 'Cuenta': 'Account',
  'Configuración de la estación': 'Station setup',
  'Este equipo muestra el monitoreo de la sesión que el Operador inicia en el portal. No crea, valida ni cierra sesiones.':
    'This computer monitors the session started by the operator in the portal. It does not create, validate, or close sessions.',
  'URL del API Gateway (o de los servicios)': 'API Gateway URL (or service URL)',
  'Identificador de la estación de conteo (UUID)': 'Counting station ID (UUID)',
  'Opciones avanzadas (sin gateway)': 'Advanced options (without gateway)',
  'WebSocket de VisionWorker': 'VisionWorker WebSocket',
  'Guardar configuración': 'Save settings', 'cambiar': 'change',
  'Correo del Operador': 'Operator email', 'Contraseña': 'Password',
  'Iniciar sesión': 'Sign in',
  'La contraseña no se guarda. El token de renovación se cifra con el almacén seguro de Windows.':
    'The password is not saved. The refresh token is encrypted with Windows secure storage.',
  'Almacenamiento seguro no disponible: deberá iniciar sesión cada vez que abra la aplicación.':
    'Secure storage is unavailable: you will need to sign in each time you open the app.',
  'No se puede monitorear esta estación': 'This station cannot be monitored',
  'Reintentar': 'Retry', 'Configuración': 'Settings', 'Servidor': 'Server',
  'Iniciar con Windows (minimizado en la bandeja)': 'Start with Windows (minimized to the tray)',
  'Minimizar a la bandeja': 'Minimize to tray', 'Sincronizar ahora': 'Sync now',
  'Cerrar sesión de la estación': 'Sign out of the station', 'Cerrar': 'Close',
  'Iniciando…': 'Starting…',
  'Conectado': 'Connected', 'Reconectando': 'Reconnecting', 'Sin conexión': 'Disconnected',
  'Sin autorización': 'Unauthorized', 'Verificando': 'Checking', 'Inactivo': 'Idle',
  'Conectando': 'Connecting',
  'Esperando sesión': 'Waiting for session', 'Conteo inicial': 'Initial count',
  'Procedimiento en curso': 'Procedure in progress', 'Validación y revisión': 'Validation and review',
  'Sesión terminada': 'Session ended', 'Sesión abortada': 'Session aborted',
  'Revisión: corrección requerida': 'Review: correction required',
  'Revisión: retención en quirófano': 'Review: operating room hold',
  'Fuente simulada (modo demostración)': 'Simulated source (demo mode)',
  'Video pregrabado (subido por el Operador)': 'Recorded video (uploaded by operator)',
  'Cámara del portal (dispositivo no registrado en la sesión)': 'Portal camera (device not registered for this session)',
  'Sin fuente activa': 'No active source',
  'Sin sesión activa': 'No active session', 'Verde confirmado': 'Confirmed green',
  'Sesión cerrada': 'Session closed', 'Discrepancia registrada': 'Recorded discrepancy',
  'Cerrada con discrepancias': 'Closed with discrepancies',
  'Esperando resultado oficial': 'Waiting for official outcome',
  'Sesión sin visión': 'Session without vision', 'Sin referencia': 'No reference',
  'Video no disponible': 'Video unavailable', 'Video sin autorización': 'Video unauthorized',
  'Reconectando video': 'Reconnecting video', 'Conectando video': 'Connecting video',
  'VisionWorker sin la sesión': 'Session not loaded in VisionWorker',
  'Error en VisionWorker': 'VisionWorker error', 'Sin captura': 'No capture',
  'Lecturas insuficientes': 'Insufficient readings',
  'Fase de captura desconocida': 'Unknown capture phase', 'Datos antiguos': 'Stale data',
  'Sobrante observado': 'Observed surplus', 'Faltante en verificación final': 'Missing instruments in final verification',
  'Faltan instrumentos en la charola': 'Instruments missing from tray',
  'Clasificación difiere': 'Classification differs', 'Verde provisional': 'Provisional green',
  'Estabilizando lectura': 'Stabilizing reading',
  'La credencial de la estación no es válida o fue revocada. Inicie sesión de nuevo.': 'The station credential is invalid or revoked. Sign in again.',
  'Esperando sesión del Operador.': 'Waiting for the operator session.',
  'Validación oficial del Operador sin discrepancias. Pendiente de revisión y cierre por SPD.': 'Operator validation found no discrepancies. Awaiting SPD review and closure.',
  'Cierre oficial sin discrepancias.': 'Official closure without discrepancies.',
  'La sesión se cerró después de discrepancias registradas. Consulte el resultado en el portal.': 'The session was closed after recorded discrepancies. Check the result in the portal.',
  'La sesión fue abortada. No hay conteo confirmado.': 'The session was aborted. There is no confirmed count.',
  'El resultado oficial aún no está disponible.': 'The official outcome is not available yet.',
  'Sin conexión con el servicio de sesiones: no se puede verificar la sesión.': 'Session service is disconnected; the session cannot be verified.',
  'Esta sesión se cuenta manualmente (sin aviso de privacidad): no hay monitoreo por cámara.': 'This session is counted manually; camera monitoring is unavailable.',
  'La sesión no tiene inventario esperado vigente.': 'This session has no current expected inventory.',
  'El monitor en vivo no está habilitado en el servidor.': 'Live monitoring is not enabled on the server.',
  'VisionWorker rechazó la autorización del monitor.': 'VisionWorker rejected monitor authorization.',
  'Sin enlace con VisionWorker.': 'No connection to VisionWorker.',
  'El worker no tiene esta sesión cargada (¿reinicio?). El Operador debe reabrir la captura en el portal.':
    'The worker has not loaded this session. The operator must reopen capture in the portal.',
  'La inferencia reportó un error.': 'Inference reported an error.',
  'El Operador no está transmitiendo cámara ni procesando video en este momento.': 'The operator is not streaming camera or processing video now.',
  'Aún no hay resultados de inferencia.': 'There are no inference results yet.',
  'No se conoce la fase en que se subió este video. Consulte el resultado en el portal.': 'The phase in which this video was uploaded is unknown. Check the portal.',
  'Esperando lecturas consecutivas suficientes.': 'Waiting for enough consecutive readings.',
  'Instrumental faltante durante la verificación final. Requiere conciliación.': 'Instruments are missing during final verification. Reconciliation is required.',
  'El total coincide, pero la clasificación por familia no coincide con la referencia. Requiere revisión.':
    'The total matches, but family classification differs from the reference. Review is required.',
  'La cantidad observada coincide con la referencia. No autoriza el cierre: la validación oficial se realiza en el sistema web.':
    'The observed quantity matches the reference. This does not authorize closure; official validation happens in the web system.',
  'Esperando lecturas consecutivas coherentes.': 'Waiting for consistent consecutive readings.',
  'La autorización de la estación expiró o fue revocada. Inicie sesión de nuevo.': 'Station authorization expired or was revoked. Sign in again.',
  'La cuenta no tiene permiso de Operador de estación.': 'This account does not have station operator permission.',
  'Estación no encontrada o no pertenece a su institución. Revise la configuración.': 'Station not found or outside your institution. Check the settings.',
  'La estación está deshabilitada en Administración.': 'The station is disabled in Administration.',
  'Sin conexión con el servidor de sesiones. Reintentando…': 'Session server disconnected. Retrying…',
  'Sesión de la estación cerrada.': 'Signed out of the station.',
  'No se pudo conectar con el servidor de autenticación.': 'Could not connect to the authentication server.',
  'Correo o contraseña incorrectos, o cuenta inactiva.': 'Incorrect email or password, or inactive account.',
  'Demasiados intentos. Espere antes de reintentar.': 'Too many attempts. Wait before trying again.',
  'No disponible en modo demostración': 'Unavailable in demo mode',
  'Configure primero la estación': 'Set up the station first',
  'Hay más de una sesión en curso para esta estación. No se selecciona ninguna: el Operador debe dejar solo una activa en el portal.':
    'More than one session is in progress at this station. The operator must leave only one active in the portal.',
};

export function translate(locale: Locale, value: string | null | undefined): string {
  if (!value) return '';
  if (locale !== 'en') return value;
  if (en[value]) return en[value];
  let match = /^Faltan (\d+)$/.exec(value);
  if (match) return `Missing ${match[1]}`;
  match = /^Sobran (\d+)$/.exec(value);
  if (match) return `Surplus ${match[1]}`;
  match = /^Discrepancia oficial abierta \((\d+)\)\. Requiere conciliación en el sistema web\.$/.exec(value);
  if (match) return `Official discrepancy open (${match[1]}). Reconciliation is required in the web system.`;
  match = /^Sin resultados nuevos desde hace (\d+) s\. No se muestra un conteo antiguo como actual\.$/.exec(value);
  if (match) return `No new results for ${match[1]} s. An old count is not shown as current.`;
  match = /^Se observan (\d+) pieza\(s\) más que la referencia(.*)\. Discrepancia explícita: no es un conteo confirmado\.$/.exec(value);
  if (match) return `${match[1]} more instruments than the reference${translate(locale, match[2])}. This is not a confirmed count.`;
  match = /^Faltan (\d+) respecto a la referencia(.*)\. Pueden estar en uso: no es una pérdida confirmada\.$/.exec(value);
  if (match) return `${match[1]} fewer than the reference${translate(locale, match[2])}. They may be in use; this is not a confirmed loss.`;
  if (value === 'El total coincide, pero la clasificación por familia no coincide con la referencia (resultado del video procesado, no en vivo). Requiere revisión.')
    return 'The total matches, but family classification differs from the reference (processed video result, not live). Review is required.';
  if (value === 'La cantidad observada coincide con la referencia (resultado del video procesado, no en vivo). No autoriza el cierre: la validación oficial se realiza en el sistema web.')
    return 'The observed quantity matches the reference (processed video result, not live). This does not authorize closure; official validation happens in the web system.';
  if (value === ' (resultado del video procesado, no en vivo)') return ' (processed video result, not live)';
  if (value.startsWith('Cámara registrada: ')) return value.replace(/^Cámara registrada: /, 'Registered camera: ');
  if (value.startsWith('Fuente simulada: ')) return value.replace(/^Fuente simulada: /, 'Simulated source: ');
  if (value.startsWith('Cámara virtual: ')) return value.replace(/^Cámara virtual: /, 'Virtual camera: ');
  match = /^Error del servidor \((.+)\)\.$/.exec(value);
  if (match) return `Server error (${match[1]}).`;
  return value;
}

export function phaseText(locale: Locale, phase: UiPhase, statusCode?: string | null): string {
  if (phase === 'ended' && statusCode === 'aborted') return translate(locale, 'Sesión abortada');
  if (phase === 'review' && statusCode === 'correction_required') return translate(locale, 'Revisión: corrección requerida');
  if (phase === 'review' && statusCode === 'or_hold') return translate(locale, 'Revisión: retención en quirófano');
  const labels: Record<UiPhase, string> = {
    none: 'Esperando sesión', initial: 'Conteo inicial', procedure: 'Procedimiento en curso',
    final: 'Verificación final', review: 'Validación y revisión', ended: 'Sesión terminada',
  };
  return translate(locale, labels[phase]);
}

const procedures: Record<string, [string, string]> = {
  demo: ['Demostración', 'Demo'],
  lap_chole: ['Colecistectomía laparoscópica', 'Laparoscopic cholecystectomy'],
  open_chole: ['Colecistectomía abierta', 'Open cholecystectomy'],
  appendectomy: ['Apendicectomía abierta', 'Open appendectomy'],
  hernia_repair: ['Hernioplastia abierta', 'Open hernioplasty'],
  cesarean: ['Cesárea', 'Cesarean section'],
  vaginal_delivery: ['Parto vaginal', 'Vaginal delivery'],
  wound_debridement: ['Desbridamiento y cierre de herida quirúrgica', 'Surgical wound debridement and closure'],
  soft_tissue_excision: ['Escisión superficial de lesión o tejido blando', 'Superficial lesion excision / soft-tissue minor surgery'],
  hip_replace: ['Artroplastia de cadera', 'Hip arthroplasty'],
};

export function procedureText(locale: Locale, code: string | null, name: string | null): string {
  const known = code ? procedures[code] : null;
  return known ? known[locale === 'en' ? 1 : 0] : name || '—';
}
