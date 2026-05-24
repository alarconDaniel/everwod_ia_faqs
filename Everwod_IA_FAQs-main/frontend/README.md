# Everwod FAQ Intelligence Frontend

Panel React/Vite para revisar sugerencias de FAQ generadas por el backend FastAPI.

## Configuracion

```bash
cp .env.example .env
npm ci
npm run dev
```

Variables:

```env
VITE_USE_MOCKS=false
VITE_INGEST_API_URL=http://127.0.0.1:8001
VITE_SUGGESTION_API_URL=http://127.0.0.1:8003
VITE_VALIDATION_API_URL=http://127.0.0.1:8004
```

## Scripts

```bash
npm run dev
npm run lint
npm run build
```
