# Resumen del dump `pgdump_production_08-04-2026-22-02-09.sql`

## Lectura rapida

Archivo revisado: `pgdump_production_08-04-2026-22-02-09.sql`.

- Tamano: 325,427,341 bytes, aprox. 325 MB.
- Formato: dump PostgreSQL en SQL plano, con `DROP`, `CREATE TABLE`, `CREATE SEQUENCE`, defaults, bloques `COPY`, y constraints/FKs al final.
- Motor origen: PostgreSQL 17.7, generado con `pg_dump` 17.9. Evidencia: lineas 6-7.
- Schema: solo `public`.
- No vi funciones, vistas, tipos custom ni extensiones.
- No vi `CREATE INDEX`; los unicos indices implicitos salen de `PRIMARY KEY` y `UNIQUE`.
- Total de filas cargadas por `COPY`: 156,802.
- Lo mas pesado no son los usuarios ni pagos: `agent_runs.run` concentra casi todo el peso del dump, con ~297M caracteres de JSON.

Advertencia importante: este dump contiene datos sensibles de produccion. Antes de mandarle contenido a ChatGPT u otro tercero, hay que sanitizar. Hay PII y secretos: nombres, emails, telefonos, fechas de nacimiento, documentos, condiciones de salud, conversaciones, hashes de passwords, tokens de WhatsApp, codigos y PINs.

## Estructura general del dump

El archivo esta organizado en tres partes:

1. Pre-data/DDL: `DROP`, `CREATE TABLE`, `CREATE SEQUENCE`, defaults.
2. Data: bloques `COPY "public"."tabla" (...) FROM stdin;`.
3. Post-data: `PRIMARY KEY`, `UNIQUE` y `FOREIGN KEY`.

Evidencia:

```sql
-- lineas 2, 6-7
-- PostgreSQL database dump
-- Dumped from database version 17.7 (Debian 17.7-3.pgdg13+1)
-- Dumped by pg_dump version 17.9 (Homebrew)
```

```sql
-- lineas 1099+
COPY "public"."accounting" ("id", "workspace_id", "date", "description", "amount", "type", "email", "created_at", "updated_at", "payment_method_id", "financial_event_id") FROM stdin;
```

```sql
-- lineas 158078+
ALTER TABLE ONLY public.accounting
    ADD CONSTRAINT accounting_pkey PRIMARY KEY (id);
```

## Tablas y volumen de datos

| Tabla | Filas | Columnas | Linea COPY | Comentario |
|---|---:|---:|---:|---|
| `accounting` | 1,258 | 11 | 1099 | Ingresos/gastos por workspace |
| `agent_chats` | 7,967 | 10 | 2359 | Conversaciones/threads de IA por workspace |
| `agent_faqs` | 212 | 8 | 10328 | FAQs del agente |
| `agent_files` | 82 | 8 | 10542 | Archivos asociados al agente |
| `agent_mcps` | 2 | 11 | 10626 | Integraciones MCP/tools |
| `agent_runs` | 8,215 | 5 | 10630 | Runs de OpenAI, JSON enorme |
| `agent_texts` | 19 | 6 | 18847 | Textos base del agente |
| `agents` | 28 | 10 | 18868 | Agentes por workspace |
| `audiences` | 7 | 10 | 18898 | Audiencias para WhatsApp campaigns |
| `chat_messages` | 35,124 | 7 | 18907 | Mensajes user/assistant |
| `chat_responses` | 0 | 5 | 54033 | Vacia |
| `events` | 0 | 12 | 54035 | Vacia |
| `financial_events` | 9 | 4 | 54037 | Catalogo de eventos financieros |
| `lead_schedule` | 213 | 5 | 54048 | Agendamientos de leads |
| `leads` | 3,714 | 16 | 54263 | Leads/prospectos |
| `mailboxes` | 26 | 6 | 57979 | Mensajes internos por workspace |
| `payment_methods` | 10 | 5 | 58007 | Metodos de pago |
| `payment_options` | 4 | 8 | 58019 | Opciones de pago visibles por workspace |
| `payments` | 11,894 | 14 | 58025 | Pagos de planes/productos/servicios |
| `plan_user` | 0 | 6 | 69921 | Vacia |
| `plans` | 275 | 13 | 69923 | Planes/membresias |
| `product_pictures` | 166 | 6 | 70200 | Imagenes de productos |
| `products` | 103 | 9 | 70368 | Productos por workspace |
| `roles` | 4 | 6 | 70473 | Roles |
| `schedule_configs` | 1 | 6 | 70479 | Configuracion de reservas/cancelaciones |
| `schedules` | 906 | 13 | 70482 | Horarios recurrentes |
| `services` | 7 | 10 | 71390 | Servicios |
| `terms` | 7 | 5 | 71399 | Unidades de duracion/terminos |
| `user_schedule` | 78,616 | 7 | 71408 | Reservas/asistencias de usuarios |
| `user_workspace` | 3,899 | 8 | 150026 | Relacion usuario-workspace-rol |
| `users` | 3,886 | 29 | 153927 | Usuarios/clientes/admins |
| `whatsapp_campaigns` | 4 | 7 | 157815 | Campanas WhatsApp |
| `whatsapp_configs` | 13 | 11 | 157821 | Configuracion WhatsApp por workspace |
| `whatsapp_templates` | 7 | 20 | 157836 | Plantillas WhatsApp |
| `workspace_configs` | 73 | 5 | 157845 | Config usuario-workspace |
| `workspaces` | 51 | 20 | 157920 | Empresas/boxes/gimnasios/etc. |

Tablas vacias: `chat_responses`, `events`, `plan_user`.

## Modelo conceptual

La BD parece ser un SaaS llamado Everwod para negocios tipo gimnasio, crossfit, escuelas deportivas, salud/servicios y otros comercios. Maneja empresas (`workspaces`), usuarios/clientes (`users`), leads/prospectos (`leads`), planes/membresias (`plans`), pagos (`payments`), productos (`products`), servicios (`services`), horarios (`schedules`), reservas (`user_schedule`, `lead_schedule`), contabilidad (`accounting`) y un modulo fuerte de IA/WhatsApp (`agents`, `agent_chats`, `agent_runs`, `chat_messages`, `whatsapp_*`).

Relacion central:

- `workspaces` es la entidad raiz de negocio.
- `users` son personas del sistema; se conectan a empresas via `user_workspace`.
- `roles` define permisos/dashboard por usuario en workspace.
- `leads` son prospectos por workspace, separados de `users`.
- `schedules` define clases/turnos recurrentes por dia de semana y hora.
- `user_schedule` reserva usuarios en horarios para fechas especificas.
- `lead_schedule` reserva leads en horarios para fechas especificas.
- `plans`, `services`, `products` son vendibles.
- `payments` registra pagos asociados a plan, producto o servicio.
- `accounting` registra ingresos/gastos, con metodo de pago y evento financiero.
- `agents` configura asistentes IA por workspace.
- `agent_runs`, `agent_chats` y `chat_messages` guardan la ejecucion y conversacion de la IA.
- `whatsapp_configs/templates/campaigns` conectan la plataforma con WhatsApp.

## Tablas principales por modulo

### Empresas / workspaces

`workspaces` define empresas/boxes/comercios.

```sql
-- lineas 952-973
CREATE TABLE public.workspaces (
    id bigint NOT NULL,
    name character varying(255) NOT NULL,
    logo character varying(255),
    is_logo_active boolean DEFAULT false NOT NULL,
    bio text,
    created_at timestamp(0) without time zone,
    updated_at timestamp(0) without time zone,
    deleted_at timestamp(0) without time zone,
    logo_thumbnail text,
    category character varying(255),
    phone character varying(255),
    nit character varying(255),
    address character varying(255),
    email character varying(255),
    phone_country_code character varying(255) DEFAULT '57'::character varying NOT NULL,
    longitude double precision,
    latitude double precision,
    address_details text,
    offer_plans boolean DEFAULT true NOT NULL,
    active boolean DEFAULT true NOT NULL
);
```

Datos relevantes:

- 51 workspaces.
- 18 activos y 33 inactivos (`active`).
- 37 ofrecen planes (`offer_plans = true`), 14 no.
- Categorias mas frecuentes: `crossfit` 10, `otro` 7, `escuela deportiva` 6, `gimnasio` 4, `veterinaria` 3.
- Fechas `created_at`: 2021-08-23 a 2026-04-07.

### Usuarios, roles y membresia por workspace

`users` contiene datos personales y medicos. Es altamente sensible.

```sql
-- lineas 799-832
CREATE TABLE public.users (
    id bigint NOT NULL,
    name character varying(255),
    email character varying(255) NOT NULL,
    email_verified_at timestamp(0) without time zone,
    password character varying(255),
    phone character varying(255),
    birthday date,
    bio text,
    picture text,
    gender character varying(255),
    remember_token character varying(100),
    created_at timestamp(0) without time zone,
    updated_at timestamp(0) without time zone,
    deleted_at timestamp(0) without time zone,
    picture_thumbnail text,
    nickname character varying(255),
    blood_type character varying(255),
    emergency_phone character varying(255),
    health_preconditions text,
    document_number character varying(255),
    document_type character varying(255),
    goals text,
    height real,
    experience_details text,
    completed_at timestamp(0) without time zone,
    occupation character varying(255),
    eps character varying(255),
    phone_country_code character varying(255) DEFAULT '57'::character varying NOT NULL,
    emergency_phone_country_code character varying(255) DEFAULT '57'::character varying NOT NULL
);
```

Checks/enums:

- `gender`: `MALE`, `FEMALE`, `NONE`.
- `blood_type`: `A+`, `A-`, `B+`, `B-`, `AB+`, `AB-`, `O+`, `O-`.
- `document_type`: `CC`, `CE`, `NIT`, `TI`, `PP`, `RC`.

Datos:

- `users`: 3,886 filas.
- `user_workspace`: 3,899 filas, 3,896 activas y 3 inactivas.
- `roles`: 4 filas.
- `workspace_configs`: 73 filas.
- `users.email` tiene constraint unique.

Relacion:

```sql
-- lineas 782-791
CREATE TABLE public.user_workspace (
    workspace_id bigint NOT NULL,
    user_id bigint NOT NULL,
    role_id bigint NOT NULL,
    created_at timestamp(0) without time zone,
    updated_at timestamp(0) without time zone,
    deleted_at timestamp(0) without time zone,
    state character varying(255) DEFAULT 'ACTIVE'::character varying NOT NULL,
    created_by_migration boolean DEFAULT false NOT NULL
);
```

### Leads

`leads` son prospectos/capturas de WhatsApp o formularios, separados de `users`.

```sql
-- lineas 376-394
CREATE TABLE public.leads (
    id bigint NOT NULL,
    name character varying(255),
    email character varying(255),
    phone character varying(255) NOT NULL,
    birthday date,
    gender character varying(255),
    created_at timestamp(0) without time zone,
    updated_at timestamp(0) without time zone,
    experience_details text,
    health_preconditions text,
    blood_type character varying(3),
    emergency_contact character varying(255),
    workspace_id bigint,
    consent boolean DEFAULT false NOT NULL,
    consent_date timestamp(0) without time zone,
    consent_channel character varying(255) DEFAULT 'whatsapp'::character varying NOT NULL
);
```

Datos:

- 3,714 leads.
- 26 workspaces con leads.
- `consent = true`: 58; `false`: 3,656.
- Fechas `created_at`: 2023-12-16 a 2026-04-08.

### Horarios y reservas

`schedules` define bloques recurrentes por workspace:

```sql
-- lineas 675-690
CREATE TABLE public.schedules (
    id bigint NOT NULL,
    workspace_id bigint NOT NULL,
    "time" time(0) with time zone NOT NULL,
    duration integer NOT NULL,
    week_day character varying(255) NOT NULL,
    state character varying(255) DEFAULT 'ACTIVE'::character varying NOT NULL,
    note character varying(1000),
    created_at timestamp(0) without time zone,
    updated_at timestamp(0) without time zone,
    limit_users integer,
    title character varying(255),
    description text,
    deleted_at timestamp(0) without time zone
);
```

Checks:

- `state`: `ACTIVE`, `INACTIVE`.
- `week_day`: `MONDAY`, `TUESDAY`, `WEDNESDAY`, `THURSDAY`, `FRIDAY`, `SATURDAY`, `SUNDAY`.

Datos:

- 906 horarios.
- 837 activos, 69 inactivos.
- Distribucion por dia: jueves 179, lunes 174, miercoles 161, viernes 157, martes 150, sabado 71, domingo 14.
- `duration`: 1241 a 7200 segundos, promedio 3580.76.
- `limit_users`: 1 a 100, promedio 13.55.

Reservas de usuarios:

```sql
-- lineas 767-774
CREATE TABLE public.user_schedule (
    schedule_id bigint NOT NULL,
    user_id bigint NOT NULL,
    created_at timestamp(0) without time zone,
    updated_at timestamp(0) without time zone,
    schedule_date timestamp(0) without time zone DEFAULT '2025-03-04 15:41:20'::timestamp without time zone NOT NULL,
    scheduled_by_admin boolean DEFAULT false NOT NULL,
    device character varying(255)
);
```

- `user_schedule`: 78,616 filas.
- `scheduled_by_admin = true`: 11,267; `false`: 67,349.
- Rango de `schedule_date`: 2023-03-31 a 2026-04-17.

Reservas de leads:

```sql
-- lineas 363-368
CREATE TABLE public.lead_schedule (
    schedule_id bigint NOT NULL,
    lead_id bigint NOT NULL,
    created_at timestamp(0) without time zone,
    updated_at timestamp(0) without time zone,
    schedule_date timestamp(0) without time zone DEFAULT '2025-03-04 15:41:27'::timestamp without time zone NOT NULL
);
```

- `lead_schedule`: 213 filas.
- Rango de `schedule_date`: 2024-05-01 a 2026-04-10.

### Planes, pagos, productos y servicios

Planes:

```sql
-- lineas 556-571
CREATE TABLE public.plans (
    id bigint NOT NULL,
    term_id bigint NOT NULL,
    workspace_id bigint NOT NULL,
    title character varying(255),
    description character varying(1000),
    price bigint,
    duration bigint,
    created_at timestamp(0) without time zone,
    updated_at timestamp(0) without time zone,
    deleted_at timestamp(0) without time zone,
    credits_term character varying(255),
    credits integer,
    credits_duration integer
);
```

- 275 planes.
- 40 workspaces con planes.
- `price`: 0 a 1,800,000 COP, promedio 210,706.
- `credits_term`: `WEEK` 27, `MONTH` 7; muchos nulos.

Pagos:

```sql
-- lineas 501-516
CREATE TABLE public.payments (
    id bigint NOT NULL,
    plan_id bigint,
    payment_method_id bigint NOT NULL,
    user_id bigint,
    amount bigint,
    start_date date,
    end_date date,
    note character varying(1000),
    created_at timestamp(0) without time zone,
    updated_at timestamp(0) without time zone,
    deleted_at timestamp(0) without time zone,
    created_by_migration boolean DEFAULT false NOT NULL,
    product_id uuid,
    service_id uuid
);
```

- 11,894 pagos.
- `amount`: 0 a 1,800,000 COP, promedio 125,263.95.
- Suma agregada del dump: 1,489,889,415 COP.
- Rango `created_at`: 2021-09-30 a 2026-04-08.
- `created_by_migration = true`: 1,286; `false`: 10,608.
- Puede referenciar `plan_id`, `product_id` o `service_id`.

Productos:

```sql
-- lineas 611-621
CREATE TABLE public.products (
    id uuid NOT NULL,
    workspace_id bigint NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    price bigint NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp(0) without time zone,
    updated_at timestamp(0) without time zone,
    deleted_at timestamp(0) without time zone
);
```

- 103 productos.
- 11 workspaces con productos.
- Todos `is_active = true`.
- Hay 166 imagenes en `product_pictures`.
- Ojo: hay un `products.price` maximo de 2,850,000,000, probablemente outlier/error de datos.

Servicios:

- `services`: 7 filas.
- `amount`: 90 a 180,000 COP.
- Asociado a `workspace_id` y `term_id`.

Metodos/opciones de pago:

- `payment_methods`: 10 filas, catalogo global.
- `payment_options`: 4 filas por workspace, 3 activas y 1 inactiva.

### Contabilidad

```sql
-- lineas 94-106
CREATE TABLE public.accounting (
    id bigint NOT NULL,
    workspace_id bigint NOT NULL,
    date timestamp(0) without time zone NOT NULL,
    description character varying(1000),
    amount bigint,
    type character varying(255) NOT NULL,
    email character varying(255),
    created_at timestamp(0) without time zone,
    updated_at timestamp(0) without time zone,
    payment_method_id bigint DEFAULT '1'::bigint NOT NULL,
    financial_event_id bigint DEFAULT '6'::bigint
);
```

- 1,258 movimientos.
- `type`: `expense` 897, `income` 361.
- `amount`: 75 a 90,340,000 COP.
- Suma agregada sin separar ingresos/gastos: 687,230,208 COP.
- Rango de `date`: 2024-01-18 a 2026-04-08.

### IA / agentes

`agents` configura el asistente por workspace:

```sql
-- lineas 229-240
CREATE TABLE public.agents (
    id uuid NOT NULL,
    workspace_id bigint NOT NULL,
    name character varying(255),
    prompt text,
    start_message character varying(5000),
    deleted_at timestamp(0) without time zone,
    created_at timestamp(0) without time zone,
    updated_at timestamp(0) without time zone,
    model_vector_store_id character varying(255),
    model character varying(255) DEFAULT 'gpt-5'::character varying NOT NULL
);
```

- 28 agentes.
- Modelos en `agents`: `gpt-5` 27, `gpt-5.4` 1.
- `agent_faqs`: 212 FAQs.
- `agent_files`: 82 archivos, con `model_file_id` y `original_name`.
- `agent_texts`: 19 textos.

Nota de integridad: `agent_faqs`, `agent_files` y `agent_texts` tienen `agent_id`, pero en el dump no aparecen PK/FK declaradas para esas tablas. En cambio, `agent_mcps.agent_id` si tiene FK a `agents(id)`.

`agent_chats` guarda threads/conversations:

```sql
-- lineas 133-144
CREATE TABLE public.agent_chats (
    id uuid NOT NULL,
    workspace_id bigint NOT NULL,
    thread_id character varying(255),
    created_at timestamp(0) without time zone,
    updated_at timestamp(0) without time zone,
    phone character varying(255),
    thread jsonb,
    conversation_id character varying(255),
    conversation jsonb,
    model character varying(255) DEFAULT 'gpt-4o'::character varying NOT NULL
);
```

- 7,967 chats.
- Modelos en `agent_chats`: `gpt-4o` 4,805; `gpt-5` 2,736; `gpt-5.4` 420; `gpt-5.2` 6.
- `thread` no nulo: 1,368 filas.
- `conversation` no nulo: 3,161 filas.

`agent_runs` es la tabla mas pesada:

```sql
-- lineas 202-207
CREATE TABLE public.agent_runs (
    id character varying(255) NOT NULL,
    thread_id character varying(255) NOT NULL,
    run jsonb NOT NULL,
    created_at timestamp(0) without time zone,
    updated_at timestamp(0) without time zone
);
```

- 8,215 runs.
- `run.status`: `completed` 8,131, `requires_action` 84.
- `run.model`: siempre `gpt-4o` dentro del JSON.
- Rango `created_at` del JSON: 2025-10-31 a 2026-02-17.
- `usage.total_tokens`: min 3,429, max 55,469, promedio 10,080.41.
- Tools mas comunes en los runs: `sendContact`, `sendDocument`, `sendImage`, `sendLocation`, `createFriendAsLead`, `file_search`, `getPlans`, `getProducts`, `getPaymentOptions`, `getSchedules`, `verifyDate`, `verifyDateTime`, `updateMyLeadInfo`, `getServices`, `createMeAsLead`, `appointments`, `cancelAppointment`, `makeAppointment`.

`chat_messages`:

```sql
-- lineas 265-272
CREATE TABLE public.chat_messages (
    id character varying(255) NOT NULL,
    message jsonb NOT NULL,
    created_at timestamp(0) without time zone,
    updated_at timestamp(0) without time zone,
    agent_chat_id uuid,
    model_output jsonb,
    message_type character varying(255) DEFAULT 'text'::character varying NOT NULL
);
```

- 35,124 mensajes.
- Roles dentro del JSON `message`: `user` 17,748; `assistant` 17,376.
- `message_type`: siempre `text`.
- `model_output`: todo nulo.
- Longitud de texto: min 0, max 17,453, promedio 141 caracteres.

Ejemplo sanitizado de `chat_messages.message`:

```json
{
  "role": "user",
  "content": [
    {
      "text": {
        "value": "Hola, necesito cambiar la direccion de mi pedido"
      },
      "type": "text"
    }
  ]
}
```

```json
{
  "role": "assistant",
  "content": [
    {
      "text": {
        "value": "Para procesarlo correctamente, contacta al equipo humano por WhatsApp <PHONE>."
      },
      "type": "text"
    }
  ]
}
```

### WhatsApp

`whatsapp_configs`:

```sql
-- lineas 873-884
CREATE TABLE public.whatsapp_configs (
    id uuid NOT NULL,
    workspace_id bigint NOT NULL,
    access_token text,
    phone_number_id character varying(255),
    waba_id character varying(255),
    created_at timestamp(0) without time zone,
    updated_at timestamp(0) without time zone,
    code text,
    business_pin character varying(6),
    status character varying(255) DEFAULT 'PENDING'::character varying NOT NULL,
    chat_close_after_seconds integer DEFAULT 300 NOT NULL
);
```

- 13 configs.
- Todas `status = AUTHORIZED`.
- Contiene secretos: `access_token`, `code`, `business_pin`, `phone_number_id`, `waba_id`.

`whatsapp_templates`:

```sql
-- lineas 892-913
CREATE TABLE public.whatsapp_templates (
    id uuid NOT NULL,
    workspace_id bigint NOT NULL,
    audience_id uuid NOT NULL,
    name character varying(255) NOT NULL,
    body text NOT NULL,
    status character varying(255) DEFAULT 'PENDING'::character varying NOT NULL,
    created_at timestamp(0) without time zone,
    updated_at timestamp(0) without time zone,
    template_meta_id character varying(255),
    template_meta_name character varying(255),
    deleted_at timestamp(0) without time zone,
    category character varying(255) DEFAULT 'UTILITY'::character varying,
    language character varying(255) DEFAULT 'es'::character varying NOT NULL,
    footer text,
    header character varying(255),
    header_type character varying(255),
    header_handle text,
    header_resource character varying(255),
    buttons json,
    rejection_info json
);
```

- 7 templates.
- `status`: `APPROVED` 4, `PENDING` 3.
- `category`: `UTILITY` 5, `MARKETING` 2.
- `language`: `es` 7.
- `buttons`: solo 1 no nulo y es `[]`.
- `rejection_info`: todo nulo.

`audiences`:

```sql
-- lineas 247-258
CREATE TABLE public.audiences (
    id uuid NOT NULL,
    workspace_id bigint NOT NULL,
    name character varying(255) NOT NULL,
    description character varying(255),
    file_path character varying(255) NOT NULL,
    created_at timestamp(0) without time zone,
    updated_at timestamp(0) without time zone,
    variables json,
    rows integer DEFAULT 0 NOT NULL,
    sample_rows json
);
```

- 7 audiences.
- `rows = 0` en todas.
- `variables` y `sample_rows` estan nulos en todas.

## JSON encontrados

Columnas JSON/JSONB:

- `agent_chats.thread jsonb`
- `agent_chats.conversation jsonb`
- `agent_mcps.tools jsonb`
- `agent_runs.run jsonb`
- `audiences.variables json`
- `audiences.sample_rows json`
- `chat_messages.message jsonb`
- `chat_messages.model_output jsonb`
- `chat_responses.response jsonb`
- `whatsapp_templates.buttons json`
- `whatsapp_templates.rejection_info json`

### `agent_chats.thread`

No nulo en 1,368 filas. Forma:

```json
{
  "id": "thread_<ID>",
  "object": "thread",
  "metadata": {},
  "created_at": 1234567890,
  "tool_resources": {}
}
```

### `agent_chats.conversation`

No nulo en 3,161 filas. Forma:

```json
{
  "id": "conv_<ID>",
  "object": "conversation",
  "metadata": {
    "phone": "<PHONE>",
    "userId": "<USER_ID>",
    "workspaceId": "<WORKSPACE_ID>"
  },
  "created_at": 1234567890
}
```

### `agent_mcps.tools`

2 filas no nulas. Es un array simple de tools:

```json
[
  {
    "name": "search_shop_catalog",
    "is_active": true
  }
]
```

### `agent_runs.run`

Forma general:

```json
{
  "id": "run_<ID>",
  "model": "gpt-4o",
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "getPlans",
        "strict": true,
        "parameters": {
          "type": "object",
          "required": ["workspace_id"],
          "properties": {
            "workspace_id": {
              "type": "number",
              "description": "Company id that you are serving, current id: <WORKSPACE_ID>"
            }
          },
          "additionalProperties": false
        },
        "description": "Get all plans from the workspace"
      }
    }
  ],
  "usage": {
    "total_tokens": 12345,
    "prompt_tokens": 12000,
    "completion_tokens": 345
  },
  "object": "thread.run",
  "status": "completed",
  "thread_id": "thread_<ID>",
  "temperature": 0.7,
  "tool_choice": "auto",
  "assistant_id": "asst_<ID>",
  "instructions": "# [CONFIGURATION & CONTEXT] ..."
}
```

Lo mas importante dentro de `run.instructions`:

- Contexto del sistema: timezone `America/Bogota`, fecha/hora del run, moneda COP.
- Identidad del asistente: nombre, rol de ventas, creador Everwod, plataforma WhatsApp, workspace empleador.
- Datos de la empresa: id, nombre, logo, bio, categoria, telefonos, direccion, email, `offer_plans`, trial, etc.
- FAQs de la empresa.
- Archivos asociados a la empresa/agente.
- Informacion del lead: id, nombre, email, telefono, workspace.
- Reglas operativas: idioma por defecto Espanol LatAm, tono profesional, brevedad, manejo de errores.
- Prohibiciones: no citas internas, no datos internos/sistema, no multimedia salvo flujo de tools, no datos financieros sensibles, no temas no relacionados, no confirmar cita sin `makeAppointment` exitoso.
- SOPs: saludo inicial, opciones de pago, productos, servicios/planes, agendamiento/cancelacion.
- Schemas de tools/function calling.

Ejemplos de tools dentro de `agent_runs.run.tools`:

```json
{
  "type": "function",
  "function": {
    "name": "verifyDateTime",
    "strict": true,
    "parameters": {
      "type": "object",
      "required": ["schedule_id", "schedule_date", "workspace_id"],
      "properties": {
        "schedule_id": {
          "enum": ["1522", "1523", "..."],
          "type": "string",
          "description": "Id of schedule to verify, available schedules"
        },
        "workspace_id": {
          "type": "number",
          "description": "Company id that you are serving, current id: <WORKSPACE_ID>"
        },
        "schedule_date": {
          "type": "string",
          "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
          "description": "Date to verify"
        }
      },
      "additionalProperties": false
    },
    "description": "Useful to verify if selected date matches the schedule_id, also validates the date"
  }
}
```

```json
{
  "type": "function",
  "function": {
    "name": "makeAppointment",
    "strict": true,
    "parameters": {
      "type": "object",
      "required": ["lead_id", "schedule_id", "schedule_date", "workspace_id"],
      "properties": {
        "lead_id": {"type": "number"},
        "schedule_id": {"enum": ["1522", "1523", "..."], "type": "string"},
        "workspace_id": {"type": "number"},
        "schedule_date": {
          "type": "string",
          "pattern": "^\\d{4}-\\d{2}-\\d{2}$"
        }
      },
      "additionalProperties": false
    },
    "description": "Create a new appointment for a lead"
  }
}
```

```json
{
  "type": "file_search",
  "file_search": {
    "ranking_options": {
      "ranker": "default_2024_08_21",
      "hybrid_search": null,
      "score_threshold": 0
    }
  }
}
```

### `chat_messages.message`

Forma:

```json
{
  "role": "user",
  "content": [
    {
      "text": {
        "value": "Mensaje del usuario"
      },
      "type": "text"
    }
  ]
}
```

```json
{
  "role": "assistant",
  "content": [
    {
      "text": {
        "value": "Respuesta del asistente"
      },
      "type": "text"
    }
  ]
}
```

### JSON vacios o nulos

- `audiences.variables`: 0 no nulos.
- `audiences.sample_rows`: 0 no nulos.
- `chat_messages.model_output`: 0 no nulos.
- `chat_responses.response`: tabla vacia.
- `whatsapp_templates.rejection_info`: 0 no nulos.
- `whatsapp_templates.buttons`: 1 no nulo, valor `[]`.

## Constraints y relaciones

Primary keys/uniques importantes:

- Todas estas tienen PK: `accounting`, `agent_chats`, `agent_mcps`, `agent_runs`, `agents`, `audiences`, `chat_messages`, `chat_responses`, `events`, `financial_events`, `leads`, `mailboxes`, `payment_methods`, `payment_options`, `payments`, `plans`, `product_pictures`, `products`, `roles`, `schedule_configs`, `schedules`, `services`, `terms`, `users`, `whatsapp_campaigns`, `whatsapp_configs`, `whatsapp_templates`, `workspace_configs`, `workspaces`.
- `schedule_configs.workspace_id` es unique.
- `users.email` es unique.
- `workspace_configs.user_id` es unique.

Fks clave:

- `accounting.workspace_id -> workspaces.id`, `payment_method_id -> payment_methods.id`, `financial_event_id -> financial_events.id`.
- `agent_chats.workspace_id -> workspaces.id`.
- `agent_mcps.agent_id -> agents.id`.
- `agents.workspace_id -> workspaces.id`.
- `audiences.workspace_id -> workspaces.id`.
- `chat_messages.agent_chat_id -> agent_chats.id`.
- `events.workspace_id/member_id/user_id/plan_id/schedule_id -> workspaces/users/plans/schedules`.
- `lead_schedule.lead_id -> leads.id`, `schedule_id -> schedules.id`.
- `leads.workspace_id -> workspaces.id`.
- `payments.payment_method_id -> payment_methods.id`, `product_id -> products.id`, `service_id -> services.id`.
- `plan_user.workspace_id/user_id/plan_id -> workspaces/users/plans`.
- `plans.workspace_id -> workspaces.id`, `term_id -> terms.id`.
- `products.workspace_id -> workspaces.id`.
- `product_pictures.product_id -> products.id`.
- `schedules.workspace_id -> workspaces.id`.
- `services.workspace_id -> workspaces.id`, `term_id -> terms.id`.
- `user_schedule.schedule_id -> schedules.id`, `user_id -> users.id`.
- `user_workspace.workspace_id/user_id/role_id -> workspaces/users/roles`.
- `whatsapp_campaigns.workspace_id/template_id -> workspaces/whatsapp_templates`.
- `whatsapp_configs.workspace_id -> workspaces.id`.
- `whatsapp_templates.workspace_id/audience_id -> workspaces/audiences`.
- `workspace_configs.user_id/workspace_id -> users/workspaces`.

Observaciones de integridad:

- No hay `CREATE INDEX` explicitos para FKs; en Postgres los FKs no crean indice automaticamente. Puede haber problemas de performance en joins/deletes si no existen indices fuera del dump.
- `agent_faqs`, `agent_files`, `agent_texts` no aparecen con PK/FK declaradas aunque tienen `id uuid` y `agent_id uuid`.
- `plan_user`, `user_schedule`, `lead_schedule` no muestran PK compuesta/unique; podrian permitir duplicados salvo que la app los controle.

## Rangos temporales importantes

- `workspaces.created_at`: 2021-08-23 a 2026-04-07.
- `users.created_at`: 2021-08-23 a 2026-04-08.
- `payments.created_at`: 2021-09-30 a 2026-04-08.
- `schedules.created_at`: 2023-03-22 a 2026-03-29.
- `user_schedule.schedule_date`: 2023-03-31 a 2026-04-17.
- `leads.created_at`: 2023-12-16 a 2026-04-08.
- `agent_chats.created_at`: 2025-04-16 a 2026-04-08.
- `chat_messages.created_at`: 2025-10-31 a 2026-04-08.
- `agent_runs.created_at`: 2025-10-31 a 2026-02-17.

## Hallazgos raros o relevantes

- El dump parece tener dos marcadores `-- PostgreSQL database dump complete`: uno antes de los `COPY` y otro al final. No impide lectura, pero sugiere dump concatenado/seccionado.
- Hay datos con posible mojibake/encoding roto en algunos textos al inspeccionar, por ejemplo acentos representados como `Ã±`. Conviene revisar encoding antes de migrar o analizar NLP.
- El archivo incluye operaciones futuras respecto a algunas fechas del dump: por ejemplo `payments.end_date` llega a 2038-01-19 y `user_schedule.schedule_date` llega a 2026-04-17.
- `products.price` tiene un maximo muy alto: 2,850,000,000 COP; probablemente outlier o error.
- `events` esta vacia aunque tiene muchas FKs, tal vez una feature antigua/no usada.
- `chat_responses` esta vacia y `chat_messages.model_output` todo nulo; el estado real del LLM se guarda en `agent_runs.run`.
- `agent_runs.run.instructions` guarda mucha informacion duplicada/snapshot por run: company data, FAQs, lead info, SOPs y tool schemas. Eso explica el tamano brutal del archivo.

## Resumen listo para pegar en ChatGPT

Estoy trabajando con un dump PostgreSQL grande (~325 MB) de una app SaaS llamada Everwod. La BD esta en schema `public`, viene de PostgreSQL 17.7/pg_dump 17.9 y contiene 33 tablas con 156,802 filas. No hay funciones, vistas ni indices explicitos (`CREATE INDEX`); solo PK/unique/FK constraints.

La app modela negocios tipo gimnasios/crossfit/servicios: `workspaces` son empresas, `users` son clientes/admins, `user_workspace` conecta usuarios con empresas y roles, `leads` son prospectos, `schedules` son horarios recurrentes, `user_schedule` y `lead_schedule` son reservas por fecha, `plans/payments/products/services/accounting` cubren monetizacion y contabilidad. Tambien tiene un modulo fuerte de IA y WhatsApp: `agents`, `agent_chats`, `agent_runs`, `chat_messages`, `whatsapp_configs`, `whatsapp_templates`, `whatsapp_campaigns`, `audiences`.

Tablas mas grandes por filas: `user_schedule` 78,616; `chat_messages` 35,124; `payments` 11,894; `agent_runs` 8,215; `agent_chats` 7,967; `users` 3,886; `leads` 3,714. Tablas vacias: `chat_responses`, `events`, `plan_user`.

La parte mas pesada del dump es `agent_runs.run jsonb`: 8,215 filas pero ~297M caracteres. Cada run guarda un objeto tipo OpenAI `thread.run` con `id`, `model`, `tools`, `usage`, `status`, `thread_id`, `assistant_id`, `instructions`, etc. `run.status`: 8,131 completed y 84 requires_action; `run.model`: `gpt-4o`. `usage.total_tokens` promedio ~10,080, max 55,469. Las instrucciones embeben contexto del negocio, FAQs, archivos, lead info, reglas operativas, SOPs y schemas de tools. Tools comunes: `getPlans`, `getProducts`, `getPaymentOptions`, `getSchedules`, `getServices`, `verifyDate`, `verifyDateTime`, `createFriendAsLead`, `makeAppointment`, `cancelAppointment`, `appointments`, `sendImage`, `sendDocument`, `sendLocation`, `sendContact`, `file_search`.

JSONs importantes:

- `agent_chats.thread`: `{id:"thread_<ID>", object:"thread", metadata:{}, created_at:int, tool_resources:{}}`.
- `agent_chats.conversation`: `{id:"conv_<ID>", object:"conversation", metadata:{phone,userId,workspaceId}, created_at:int}`.
- `agent_runs.run`: objeto OpenAI run con tools y prompt completo.
- `chat_messages.message`: `{role:"user|assistant", content:[{type:"text", text:{value:"..."}}]}`.
- `agent_mcps.tools`: array de `{name,is_active}`.
- `audiences.variables/sample_rows`, `chat_messages.model_output`, `whatsapp_templates.rejection_info` estan nulos; `whatsapp_templates.buttons` casi todo nulo o `[]`.

Datos sensibles presentes: nombres, emails, telefonos, birthdays, documentos, condiciones de salud, EPS, conversaciones, password hashes, WhatsApp access tokens, codigos y business pins. No se debe compartir el dump crudo sin sanitizar.

Relaciones clave: `workspaces` es raiz. `agents`, `leads`, `plans`, `products`, `schedules`, `services`, `whatsapp_*` cuelgan de `workspaces`. `user_workspace` une users/workspaces/roles. `user_schedule` une users/schedules. `lead_schedule` une leads/schedules. `payments` referencia metodo de pago y opcionalmente plan/producto/servicio. `chat_messages` referencia `agent_chats`. `agent_runs` se une por `thread_id`, no por FK declarada.

Riesgos/observaciones: no hay indices explicitos para FKs; `agent_faqs`, `agent_files`, `agent_texts` tienen `agent_id` pero no FK/PK declarada en el dump; tablas puente como `user_schedule`, `lead_schedule`, `plan_user` no muestran PK compuesta/unique; hay posible mojibake en textos con acentos; `products.price` tiene un outlier de 2,850,000,000; `events` y `chat_responses` estan vacias.

