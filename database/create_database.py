import psycopg2
from psycopg2 import sql
from configuration.config import Config


def ensure_database_exists(dbname=Config.DATABASE_NAME, user=Config.DATABASE_USER, password=Config.DATABASE_PASSWORD,
                           host=Config.DATABASE_HOST, port=Config.DATABASE_PORT):
    # Connect to default 'postgres' DB to create new one if missing
    conn = psycopg2.connect(dbname='postgres', user=user, password=password, host=host, port=port)
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute(sql.SQL("SELECT 1 FROM pg_database WHERE datname = %s"), [dbname])
    exists = cur.fetchone()

    if not exists:
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
        print(f"✅ Database '{dbname}' created successfully!")

    cur.close()
    conn.close()
