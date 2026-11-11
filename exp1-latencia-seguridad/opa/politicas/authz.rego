# PDP (Motor de Autorización). Política ABAC: el estudiante solo guarda en su institución
# y con su propia identidad; el docente solo consulta calificaciones de su institución.
package evaluaciones.authz

import rego.v1

default allow := false

# PUT /api/instituciones/{tenant}/estudiantes/{estudiante}/intentos/{intento}/respuestas/{pregunta}
allow if {
	input.method == "PUT"
	input.path = ["api", "instituciones", tenant, "estudiantes", estudiante, "intentos", _, "respuestas", _]
	input.token.rol == "estudiante"
	input.token.tenant_id == tenant
	input.token.sub == estudiante
}

# GET /api/instituciones/{tenant}/cursos/{curso}/calificaciones
allow if {
	input.method == "GET"
	input.path = ["api", "instituciones", tenant, "cursos", _, "calificaciones"]
	input.token.rol == "docente"
	input.token.tenant_id == tenant
}
