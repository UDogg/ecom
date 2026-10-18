from django.conf import settings
from django.db import migrations

SCHEMA = settings.DB_SCHEMA


class Migration(migrations.Migration):
    """Create the commerce schema before any table so tables land inside it.

    search_path is `commerce,public`, so once the schema exists every later
    CREATE TABLE in this connection goes into it. In Compose, db/init has
    already created it, and the commerce role has no CREATE on the database,
    so check first: CREATE SCHEMA IF NOT EXISTS would still demand that
    privilege.
    """

    initial = True
    dependencies = []

    operations = [
        migrations.RunSQL(
            sql=f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = '{SCHEMA}') THEN
                        CREATE SCHEMA "{SCHEMA}";
                    END IF;
                END $$;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
