import psycopg2

from app.core.common import FAQ_SCHEMA, get_db_config


DB_CONFIG = get_db_config()

try:
    conn = psycopg2.connect(**DB_CONFIG)
    print("Conexion exitosa a PostgreSQL.")

    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {FAQ_SCHEMA}.messages;")
    result = cur.fetchone()
    print(f"Numero de filas en '{FAQ_SCHEMA}.messages': {result[0]}")

    cur.execute(
        """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_schema IN ('faq_mvp', 'everwod_raw')
        ORDER BY table_schema, table_name;
        """
    )
    tables = cur.fetchall()
    print("Tablas disponibles del MVP/raw:")
    for schema, table in tables:
        print(f"- {schema}.{table}")

    cur.close()
    conn.close()
    print("Conexion cerrada.")

except psycopg2.Error as exc:
    print(f"Error de PostgreSQL: {exc}")
except Exception as exc:
    print(f"Error general: {exc}")
