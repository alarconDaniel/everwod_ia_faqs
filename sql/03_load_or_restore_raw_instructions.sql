/*
  03_load_or_restore_raw_instructions.sql

  Este archivo documenta como cargar el pg_dump real en staging/raw.
  El dump entregado es un SQL plano con COPY ... FROM stdin y metacomandos
  de psql (\restrict / \unrestrict), por lo que NO debe ejecutarse completo
  desde el Query Tool normal de pgAdmin. Use psql 17+ o el PSQL Tool de pgAdmin.

  Archivo fuente inspeccionado:
    pgdump_production_08-04-2026-22-02-09.sql

  Importante:
  - El dump crea tablas en schema public y contiene DROP TABLE IF EXISTS public.*.
  - Ejecute el dump en una base limpia dedicada al MVP.
  - Despues de restaurarlo, ejecute el bloque SQL de este archivo para mover
    las tablas/secuencias restauradas desde public hacia everwod_raw.
  - No reescriba manualmente los INSERT/COPY: la fuente de verdad es el dump.

  Opcion recomendada con psql en Windows:

    psql -U postgres -d everwod_faq_mvp -f "D:\U\SeptimoSemestre\IA\Proyecto Integrador\pgdump_production_08-04-2026-22-02-09.sql"

  Opcion desde pgAdmin4:
  1. Crear/conectarse a la base everwod_faq_mvp.
  2. Abrir Tools > PSQL Tool.
  3. Ejecutar:
       \i 'D:/U/SeptimoSemestre/IA/Proyecto Integrador/pgdump_production_08-04-2026-22-02-09.sql'
  4. Volver al Query Tool y ejecutar el bloque "Mover public -> everwod_raw" de abajo.

  Si psql no reconoce \restrict, instale/use cliente psql 17+.
*/

CREATE SCHEMA IF NOT EXISTS everwod_raw;

/*
  Mover public -> everwod_raw

  Ejecute este bloque DESPUES de restaurar el dump. Es idempotente para el caso
  en que las tablas ya esten movidas: si no encuentra la tabla en public, solo
  emite NOTICE.
*/

DO $$
DECLARE
    table_name text;
    source_tables text[] := ARRAY[
        'accounting',
        'agent_chats',
        'agent_faqs',
        'agent_files',
        'agent_mcps',
        'agent_runs',
        'agent_texts',
        'agents',
        'audiences',
        'chat_messages',
        'chat_responses',
        'events',
        'financial_events',
        'lead_schedule',
        'leads',
        'mailboxes',
        'payment_methods',
        'payment_options',
        'payments',
        'plan_user',
        'plans',
        'product_pictures',
        'products',
        'roles',
        'schedule_configs',
        'schedules',
        'services',
        'terms',
        'user_schedule',
        'user_workspace',
        'users',
        'whatsapp_campaigns',
        'whatsapp_configs',
        'whatsapp_templates',
        'workspace_configs',
        'workspaces'
    ];
BEGIN
    FOREACH table_name IN ARRAY source_tables LOOP
        IF to_regclass(format('everwod_raw.%I', table_name)) IS NOT NULL THEN
            RAISE NOTICE 'everwod_raw.% ya existe; no se mueve public.%', table_name, table_name;
        ELSIF to_regclass(format('public.%I', table_name)) IS NOT NULL THEN
            EXECUTE format('ALTER TABLE public.%I SET SCHEMA everwod_raw', table_name);
            RAISE NOTICE 'Movida tabla public.% a everwod_raw.%', table_name, table_name;
        ELSE
            RAISE NOTICE 'No se encontro public.% para mover', table_name;
        END IF;
    END LOOP;
END $$;

DO $$
DECLARE
    sequence_name text;
    source_sequences text[] := ARRAY[
        'accounting_id_seq',
        'events_id_seq',
        'financial_events_id_seq',
        'leads_id_seq',
        'mailboxes_id_seq',
        'payment_methods_id_seq',
        'payments_id_seq',
        'plans_id_seq',
        'roles_id_seq',
        'schedules_id_seq',
        'terms_id_seq',
        'users_id_seq',
        'workspace_configs_id_seq',
        'workspaces_id_seq'
    ];
BEGIN
    FOREACH sequence_name IN ARRAY source_sequences LOOP
        IF to_regclass(format('everwod_raw.%I', sequence_name)) IS NOT NULL THEN
            RAISE NOTICE 'everwod_raw.% ya existe; no se mueve public.%', sequence_name, sequence_name;
        ELSIF to_regclass(format('public.%I', sequence_name)) IS NOT NULL THEN
            EXECUTE format('ALTER SEQUENCE public.%I SET SCHEMA everwod_raw', sequence_name);
            RAISE NOTICE 'Movida secuencia public.% a everwod_raw.%', sequence_name, sequence_name;
        ELSE
            RAISE NOTICE 'No se encontro public.% para mover', sequence_name;
        END IF;
    END LOOP;
END $$;

/*
  Verificacion rapida del staging/raw despues de mover:

  SELECT schemaname, tablename
  FROM pg_tables
  WHERE schemaname = 'everwod_raw'
  ORDER BY tablename;

  SELECT 'workspaces' AS table_name, count(*) FROM everwod_raw.workspaces
  UNION ALL SELECT 'agents', count(*) FROM everwod_raw.agents
  UNION ALL SELECT 'agent_chats', count(*) FROM everwod_raw.agent_chats
  UNION ALL SELECT 'chat_messages', count(*) FROM everwod_raw.chat_messages
  UNION ALL SELECT 'agent_faqs', count(*) FROM everwod_raw.agent_faqs;
*/
