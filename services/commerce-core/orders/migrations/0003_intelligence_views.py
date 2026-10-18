from django.db import migrations

# What the intelligence service may read: aggregates over active products only.
# No customer ids, no e-mails, no order ids, no prices paid. Granted only if
# the role exists (db/init creates it).
VIEWS = """
CREATE OR REPLACE VIEW intel_product_popularity AS
SELECT p.id AS product_id, p.sku, p.name, p.price_minor, p.currency,
       coalesce(sum(oi.quantity) FILTER (WHERE o.id IS NOT NULL), 0)::bigint AS units
FROM product p
LEFT JOIN order_item oi ON oi.product_id = p.id
LEFT JOIN orders o ON o.id = oi.order_id AND o.status NOT IN ('cancelled', 'expired')
WHERE p.status = 'active'
GROUP BY p.id;

CREATE OR REPLACE VIEW intel_copurchase AS
SELECT a.product_id, b.product_id AS other_id, count(DISTINCT a.order_id)::bigint AS together
FROM order_item a
JOIN order_item b ON b.order_id = a.order_id AND b.product_id <> a.product_id
JOIN orders o ON o.id = a.order_id AND o.status NOT IN ('cancelled', 'expired')
JOIN product p ON p.id = b.product_id AND p.status = 'active'
GROUP BY a.product_id, b.product_id;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'intelligence') THEN
        EXECUTE 'GRANT USAGE ON SCHEMA ' || quote_ident(current_schema()) || ' TO intelligence';
        GRANT SELECT ON intel_product_popularity, intel_copurchase TO intelligence;
    END IF;
END $$;
"""


class Migration(migrations.Migration):
    dependencies = [("orders", "0002_order_customer_email"), ("catalog", "0003_catalog_category_view")]

    operations = [migrations.RunSQL(VIEWS, reverse_sql="DROP VIEW IF EXISTS intel_copurchase; "
                                                        "DROP VIEW IF EXISTS intel_product_popularity;")]
