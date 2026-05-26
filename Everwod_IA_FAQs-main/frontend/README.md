# Everwod FAQ Intelligence Frontend

Panel web desarrollado con **React + Vite + TypeScript** para revisar, editar, aprobar y rechazar sugerencias de preguntas frecuentes generadas por el backend de Everwod FAQ Intelligence.

El frontend funciona como una interfaz de demostración y revisión humana sobre el microservicio principal. Permite seleccionar empresas, generar sugerencias, visualizar métricas del pipeline, revisar evidencia conversacional y validar manualmente cada FAQ candidata.

---

## Funcionalidades principales

| Funcionalidad               | Descripción                                                                  |
| --------------------------- | ---------------------------------------------------------------------------- |
| Selección de empresa        | Permite cargar sugerencias por `workspace_id`.                               |
| Selección de rango temporal | Permite generar sugerencias con ventanas como 7, 30, 60, 90, 180 o 365 días. |
| Generación de sugerencias   | Ejecuta el pipeline del backend para una empresa seleccionada.               |
| Visualización de métricas   | Muestra empresas, clusters, ejemplos y `Silhouette pipeline`.                |
| Revisión de candidatos      | Presenta pregunta sugerida, respuesta, score, estado y evidencia.            |
| Validación humana           | Permite aprobar, rechazar o marcar como `needs_review`.                      |
| Edición manual              | Permite corregir pregunta y respuesta antes de validar.                      |
| Historial de validaciones   | Muestra decisiones humanas recientes y trazabilidad.                         |

---

## Requisito previo

Antes de ejecutar el frontend, debe estar corriendo la API unificada del backend:

```powershell
uvicorn app.api.main:app --reload --port 8003
```

La API debe responder correctamente en:

```text
http://127.0.0.1:8003/health
```

---

## Configuración recomendada

Crear un archivo `.env.local` dentro de la carpeta `frontend/`:

```env
VITE_API_URL=http://127.0.0.1:8003
VITE_INGEST_API_URL=http://127.0.0.1:8003
VITE_SUGGESTION_API_URL=http://127.0.0.1:8003
VITE_VALIDATION_API_URL=http://127.0.0.1:8003
VITE_USE_MOCKS=false
```

> La versión actual recomienda usar la **API unificada** en el puerto `8003`. Los servicios separados (`8001`, `8003`, `8004`) se conservan solo como compatibilidad legacy del backend.

---

## Instalación

Desde la carpeta `frontend/`:

```bash
npm install
```

Si se desea una instalación limpia y reproducible:

```bash
npm ci
```

---

## Ejecución en desarrollo

```bash
npm run dev
```

Por defecto, Vite expondrá la aplicación en:

```text
http://localhost:5173
```

---

## Validación de tipos

```bash
npm run lint
```

En este proyecto, el comando de lint ejecuta TypeScript en modo verificación:

```bash
tsc --noEmit
```

---

## Build de producción

```bash
npm run build
```

El resultado queda en:

```text
frontend/dist/
```

Para previsualizar el build:

```bash
npm run preview
```

---

## Flujo recomendado de uso

1. Levantar backend:

```powershell
uvicorn app.api.main:app --reload --port 8003
```

2. Levantar frontend:

```bash
cd frontend
npm run dev
```

3. Abrir:

```text
http://localhost:5173
```

4. Seleccionar una empresa.
5. Seleccionar el rango de días.
6. Generar sugerencias.
7. Revisar métricas del pipeline.
8. Abrir una sugerencia.
9. Editar pregunta/respuesta si es necesario.
10. Aprobar, rechazar o marcar como `needs_review`.

---

## Métricas visibles en la interfaz

| Métrica             | Significado                                                     |
| ------------------- | --------------------------------------------------------------- |
| Empresas            | Cantidad de empresas con sugerencias cargadas.                  |
| Clusters            | Cantidad de grupos o sugerencias visibles en la corrida actual. |
| Ejemplos            | Mensajes usados como evidencia conversacional.                  |
| Silhouette pipeline | Calidad del agrupamiento calculada durante el pipeline.         |

### Sobre `Silhouette pipeline`

El valor de silhouette indica qué tan coherentes y separados están los clusters semánticos. Puede mostrarse aunque el número final de sugerencias visibles sea bajo, porque corresponde a la calidad del agrupamiento generado por el pipeline antes de filtros finales, deduplicación y revisión humana.

---

## Estados visuales de una sugerencia

| Estado o badge | Significado                                                        |
| -------------- | ------------------------------------------------------------------ |
| `PENDING`      | Candidato generado y pendiente de revisión.                        |
| `NEEDS_REVIEW` | Candidato útil, pero requiere revisión humana especial.            |
| `APPROVED`     | FAQ aprobada.                                                      |
| `REJECTED`     | FAQ rechazada.                                                     |
| `REVIEW`       | Indica que la sugerencia requiere intervención humana.             |
| `RESPUESTA`    | La pregunta es útil, pero la respuesta necesita revisión o ajuste. |
| `ALTA`         | Candidato de mayor confianza según métricas internas.              |

---

## Endpoints consumidos

La interfaz consume la API unificada:

| Endpoint                            | Uso                                                     |
| ----------------------------------- | ------------------------------------------------------- |
| `GET /health`                       | Verificación del backend.                               |
| `GET /workspaces`                   | Carga de empresas disponibles.                          |
| `POST /suggest`                     | Generación de nuevas sugerencias.                       |
| `GET /suggestions`                  | Listado de sugerencias.                                 |
| `PATCH /suggestions/{candidate_id}` | Edición de pregunta/respuesta.                          |
| `POST /validate`                    | Aprobación, rechazo o revisión.                         |
| `GET /validations`                  | Historial de validaciones.                              |
| `POST /ingest`                      | Ingesta segura en modo `dry_run` o escritura explícita. |

---

## Modo mocks

El frontend conserva soporte para datos simulados mediante:

```env
VITE_USE_MOCKS=true
```

Para la integración real con backend, debe estar en:

```env
VITE_USE_MOCKS=false
```

---

## Notas para entrega

* El frontend es un panel de revisión y demostración del microservicio.
* El núcleo productivo del sistema está en el backend FastAPI y el pipeline de sugerencias.
* Para producción, se recomienda desplegar el frontend como sitio estático y conectar contra `faq-api`.
* No se deben exponer archivos `.env.local`, credenciales ni datos sensibles.

---

## Scripts disponibles

```bash
npm run dev      # servidor de desarrollo
npm run lint     # validación TypeScript
npm run build    # build de producción
npm run preview  # previsualización del build
```
