// Experimento 1 · carga legítima (REND-01) y latencia de autorización (SEG-01).
// 1.000 estudiantes autoguardan ~cada 10 s. Cada iteración responde una pregunta distinta
// (q0, q1, ...), así cada guardado confirmado es una fila única en la BD y la reconciliación
// se hace por conteo. El gateway devuelve X-Authz-Ms (tiempo de la decisión de autorización),
// que se registra como la métrica autorizacion_ms.
//
// Variables:  BASE      (https://balanceador:8443 dentro de Docker; https://localhost:8443 desde el host)
//             VUS       (1000)   RAMPA (30s)   DURACION (10m, después de la rampa)
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Trend } from 'k6/metrics';
import { SharedArray } from 'k6/data';

const estudiantes = new SharedArray('estudiantes', () => JSON.parse(open('./estudiantes.json')));
const confirmados = new Counter('guardados_confirmados');
const errores = new Counter('errores_guardado');          // por código: ver errores_guardado{estado:...}
const reintentos = new Counter('reintentos_guardado');
const autorizacion = new Trend('autorizacion_ms', true);

const BASE = __ENV.BASE || 'https://localhost:8443';
const VUS = Number(__ENV.VUS || 1000);

export const options = {
  insecureSkipTLSVerify: true,           // certificado autofirmado del laboratorio
  summaryTrendStats: ['avg', 'med', 'p(95)', 'p(99)', 'max'],
  scenarios: {
    guardado: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: __ENV.RAMPA || '30s', target: VUS },          // los estudiantes van entrando
        { duration: __ENV.DURACION || '10m', target: VUS },       // ventana medida
      ],
      gracefulRampDown: '15s',
    },
  },
  thresholds: {
    'http_req_duration{tipo:guardado}': ['p(95)<400', 'p(99)<600'],   // REND-01
    'http_req_failed{tipo:guardado}': ['rate<0.001'],
    'checks': ['rate>0.999'],                                           // confirmados tras el reintento
    autorizacion_ms: ['p(95)<5'],                                       // SEG-01
  },
};

function guardar(url, cuerpo, token, tipo) {
  return http.put(url, cuerpo, {
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    tags: { tipo: tipo, name: 'PUT /respuestas' },
  });
}

export default function () {
  const e = estudiantes[(__VU - 1) % estudiantes.length];
  const url = `${BASE}/api/instituciones/${e.tenant}/estudiantes/${e.id}/intentos/${e.intento}/respuestas/q${__ITER}`;
  const cuerpo = JSON.stringify({ valor: 'B', answer_id: `${__VU}-${__ITER}` });
  let r = guardar(url, cuerpo, e.token, 'guardado');
  // El autoguardado del Portal reintenta una vez si el servidor no confirmó (5xx); el
  // reintento se mide aparte (tipo:reintento) para no mezclarlo con la latencia del guardado.
  if (r.status >= 500) {
    errores.add(1, { estado: String(r.status) });
    reintentos.add(1);
    sleep(0.3);
    r = guardar(url, cuerpo, e.token, 'reintento');
  }
  if (check(r, { 'guardado confirmado (200)': (x) => x.status === 200 })) {
    confirmados.add(1);
  } else {
    errores.add(1, { estado: String(r.status) });
    if (r.status === 404 && __ITER === 0) {
      console.error(`Intento ${e.intento} no existe: ¿se ejecutó generar_datos.py durante la corrida?`);
    }
  }
  const authz = r.headers['X-Authz-Ms'];
  if (authz !== undefined) {
    autorizacion.add(Number(authz));
  }
  sleep(9 + Math.random() * 2);          // autoguardado cada ~10 s con jitter
}
