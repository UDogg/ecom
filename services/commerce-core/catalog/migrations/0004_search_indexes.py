from django.db import migrations

# Indexes for what the storefront asks most (catalog-reader's list query): full-text search over
# product names and descriptions and over category names, and the active catalog by newest and by
# price. The expressions match the query's exactly, so Postgres can use them through the
# catalog_public view.
FORWARD = """
CREATE INDEX IF NOT EXISTS product_search ON product USING gin (to_tsvector('english', name || ' ' || description));
CREATE INDEX IF NOT EXISTS category_search ON category USING gin (to_tsvector('english', name));
CREATE INDEX IF NOT EXISTS product_active_newest ON product (created_at DESC, id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS product_active_price ON product (price_minor, name, id) WHERE status = 'active';
"""
REVERSE = """
DROP INDEX IF EXISTS product_search;
DROP INDEX IF EXISTS category_search;
DROP INDEX IF EXISTS product_active_newest;
DROP INDEX IF EXISTS product_active_price;
"""


class Migration(migrations.Migration):
    dependencies = [("catalog", "0003_catalog_category_view")]

    operations = [migrations.RunSQL(FORWARD, REVERSE)]
