/*
  01_create_schemas.sql

  Crea los schemas usados por la solucion:
  - everwod_raw: staging/raw para alojar el pg_dump real de Everwod.
  - faq_mvp: modelo ordenado para analisis conversacional, FAQs, embeddings,
    clustering, sugerencias y validacion humana.
*/

CREATE SCHEMA IF NOT EXISTS everwod_raw;
CREATE SCHEMA IF NOT EXISTS faq_mvp;

-- pgcrypto se usa para gen_random_uuid() en tablas operativas del pipeline.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

COMMENT ON SCHEMA everwod_raw IS
  'Staging/raw: contiene las tablas restauradas desde el pg_dump anonimizado de Everwod, sin inventar ni transformar datos de origen.';

COMMENT ON SCHEMA faq_mvp IS
  'Modelo ordenado del MVP para deteccion de patrones conversacionales, FAQs existentes, candidatos, embeddings, clustering y validacion humana.';
