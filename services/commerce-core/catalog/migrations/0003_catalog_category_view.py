from django.db import migrations

# The category tree for catalog-reader, the second (and last) relation its
# role may SELECT. Granted only if the role exists (db/init creates it).
VIEW = """
CREATE OR REPLACE VIEW catalog_category_public AS
SELECT c.slug, c.name, p.slug AS parent_slug
FROM category c
LEFT JOIN category p ON p.id = c.parent_id;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'catalog_reader') THEN
        EXECUTE 'GRANT SELECT ON catalog_category_public TO catalog_reader';
    END IF;
END $$;
"""


class Migration(migrations.Migration):
    dependencies = [("catalog", "0002_catalog_public_view")]

    operations = [migrations.RunSQL(VIEW, reverse_sql="DROP VIEW IF EXISTS catalog_category_public")]
