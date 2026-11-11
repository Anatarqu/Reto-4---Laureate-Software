# Pruebas del Pipeline de políticas: la política solo se publica al Servicio de Decisiones
# si estas pruebas pasan (docker compose --profile pipeline run --rm pipeline-politicas).
package evaluaciones.authz_test

import rego.v1

import data.evaluaciones.authz

guardar(tenant, est) := ["api", "instituciones", tenant, "estudiantes", est, "intentos", "i1", "respuestas", "q1"]

test_estudiante_guarda_en_su_institucion if {
	authz.allow with input as {
		"method": "PUT",
		"path": guardar("inst-a", "est-a-0001"),
		"token": {"sub": "est-a-0001", "tenant_id": "inst-a", "rol": "estudiante"},
	}
}

test_niega_credencial_de_otro_estudiante if {
	not authz.allow with input as {
		"method": "PUT",
		"path": guardar("inst-b", "est-b-0001"),
		"token": {"sub": "est-a-0001", "tenant_id": "inst-a", "rol": "estudiante"},
	}
}

test_niega_otra_institucion_mismo_id if {
	not authz.allow with input as {
		"method": "PUT",
		"path": guardar("inst-b", "est-a-0001"),
		"token": {"sub": "est-a-0001", "tenant_id": "inst-a", "rol": "estudiante"},
	}
}

test_niega_estudiante_en_endpoint_de_docente if {
	not authz.allow with input as {
		"method": "GET",
		"path": ["api", "instituciones", "inst-a", "cursos", "c1", "calificaciones"],
		"token": {"sub": "est-a-0001", "tenant_id": "inst-a", "rol": "estudiante"},
	}
}

test_docente_consulta_su_institucion if {
	authz.allow with input as {
		"method": "GET",
		"path": ["api", "instituciones", "inst-a", "cursos", "c1", "calificaciones"],
		"token": {"sub": "doc-a-1", "tenant_id": "inst-a", "rol": "docente"},
	}
}

test_niega_docente_de_otra_institucion if {
	not authz.allow with input as {
		"method": "GET",
		"path": ["api", "instituciones", "inst-b", "cursos", "c1", "calificaciones"],
		"token": {"sub": "doc-a-1", "tenant_id": "inst-a", "rol": "docente"},
	}
}
