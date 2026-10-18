from django.db import migrations

# The only relation catalog-reader may SELECT: active products with stock,
# no costs, no audit data, nothing from orders. The grant is applied only if
# the role exists (db/init creates it in Compose; tests run without it).
VIEW = """
CREATE OR REPLACE VIEW catalog_public AS
SELECT p.id, p.sku, p.name, p.description, p.price_minor, p.currency,
       c.slug AS category_slug, c.name AS category_name,
       COALESCE(i.available_quantity, 0) AS available_quantity,
       COALESCE((SELECT json_agg(pi.object_key ORDER BY pi.position)
                 FROM product_image pi WHERE pi.product_id = p.id), '[]'::json) AS image_keys,
       p.created_at, p.updated_at
FROM product p
JOIN category c ON c.id = p.category_id
LEFT JOIN inventory i ON i.product_id = p.id
WHERE p.status = 'active';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'catalog_reader') THEN
        EXECUTE 'GRANT USAGE ON SCHEMA ' || quote_ident(current_schema()) || ' TO catalog_reader';
        EXECUTE 'GRANT SELECT ON catalog_public TO catalog_reader';
    END IF;
END $$;
"""


class Migration(migrations.Migration):
    dependencies = [("catalog", "0001_initial")]

    operations = [migrations.RunSQL(VIEW, reverse_sql="DROP VIEW IF EXISTS catalog_public")]
