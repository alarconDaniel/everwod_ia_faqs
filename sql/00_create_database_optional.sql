/*
  00_create_database_optional.sql

  Uso:
  - Ejecute este archivo solo si necesita crear una base de datos nueva desde
    un usuario con permiso CREATEDB o superusuario.
  - No lo ejecute dentro de una base de datos que ya contiene el dump cargado.
  - En pgAdmin4 tambien puede crear la base desde la interfaz grafica y omitir
    este archivo.

  Recomendacion para el MVP:
  1. Crear una base limpia, por ejemplo everwod_faq_mvp.
  2. Conectarse a esa base.
  3. Ejecutar 01_create_schemas.sql.
  4. Restaurar el pg_dump en esa misma base siguiendo 03_load_or_restore_raw_instructions.sql.
*/

-- Descomente si necesita crear la base desde psql conectado a postgres.
-- CREATE DATABASE everwod_faq_mvp
--   WITH
--   OWNER = postgres
--   ENCODING = 'UTF8'
--   LC_COLLATE = 'Spanish_Colombia.1252'
--   LC_CTYPE = 'Spanish_Colombia.1252'
--   TEMPLATE = template0;

-- Alternativa mas portable si la configuracion regional anterior no existe:
-- CREATE DATABASE everwod_faq_mvp
--   WITH
--   OWNER = postgres
--   ENCODING = 'UTF8'
--   TEMPLATE = template0;
