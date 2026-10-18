from django.db import migrations

# outbox-publisher may read the outbox and mark rows published. Nothing else:
# it cannot see products, orders or customers (README section 12).
GRANTS = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'outbox_publisher') THEN
        EXECUTE 'GRANT USAGE ON SCHEMA ' || quote_ident(current_schema()) || ' TO outbox_publisher';
        GRANT SELECT, UPDATE (published_at, publish_attempts) ON outbox_event TO outbox_publisher;
    END IF;
END $$;
"""


class Migration(migrations.Migration):
    dependencies = [("outbox", "0001_initial")]

    operations = [migrations.RunSQL(GRANTS, reverse_sql=migrations.RunSQL.noop)]
