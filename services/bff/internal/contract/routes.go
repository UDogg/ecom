package contract

import "time"

// THE PUBLIC API. Every route the BFF serves is in this file; there is no catch-all proxy, so a
// path or method that is not listed here does not exist (404/405) and never reaches a core service.
// The only way around the BFF is break-glass (docs/break-glass.md), which talks to the core
// services directly with an audited, short-lived token.
//
// Endpoint: Path is a net/http pattern under /api/ ({id} segments must be UUIDs), Upstream the
// fixed path on Service it is sent to, Auth the role the BFF checks first (the core decides for
// real), Query and Body the only fields a client may send, Response the schema that shapes what
// comes back (anything not in the schema is dropped). Cache lets concurrent and repeated anonymous
// reads within that window share one upstream call.

// PaymentSources are payments-sim's test cards.
var PaymentSources = []string{"tok_visa", "tok_mastercard", "tok_declined", "tok_insufficient", "tok_timeout"}

var schemas = map[string]Schema{
	"problem":            Same("type", "title", "status", "detail", "trace_id", "current_version", "available", "requested", "product_id").With(Schema{"errors": Raw()}),
	"category_ref":       Same("slug", "name"),
	"category_node":      Same("slug", "name").With(Schema{"children": ListOf("category_node")}),
	"category_tree":      Schema{"items": ListOf("category_node")},
	"image_public":       Same("url", "position"),
	"image_admin":        Same("key", "url", "position"),
	"product_public":     Same("id", "sku", "name", "description", "price_minor", "currency", "in_stock").With(Schema{"category": Object("category_ref"), "images": ListOf("image_public")}),
	"product_page":       Same("total", "page", "page_size", "pages").With(Schema{"items": ListOf("product_public")}),
	"product_admin":      Same("id", "sku", "name", "description", "price_minor", "currency", "in_stock", "status", "version", "available_quantity", "created_at", "updated_at").With(Schema{"category": Object("category_ref"), "images": ListOf("image_admin")}),
	"product_admin_page": Same("total", "page", "page_size", "pages").With(Schema{"items": ListOf("product_admin")}),
	"cart_line":          Same("product_id", "sku", "name", "quantity", "unit_price_minor", "currency", "available"),
	"cart":               Same("subtotal_minor").With(Schema{"items": ListOf("cart_line")}),
	"order_line":         Same("product_id", "sku", "name", "quantity", "unit_price_minor"),
	"payment":            Same("status", "failure_code"),
	"order":              Same("id", "status", "total_minor", "currency", "created_at").With(Schema{"items": ListOf("order_line"), "payment": Object("payment")}),
	"order_page":         Same("total", "page", "page_size", "pages").With(Schema{"items": ListOf("order")}),
	"order_admin":        Same("id", "status", "total_minor", "currency", "created_at", "customer_id").With(Schema{"items": ListOf("order_line"), "payment": Object("payment")}),
	"order_admin_page":   Same("total", "page", "page_size", "pages").With(Schema{"items": ListOf("order_admin")}),
	"adjustment":         Same("id", "product_id", "delta", "resulting_quantity", "reason", "created_at"),
	"adjustment_page":    Same("total", "page", "page_size", "pages").With(Schema{"items": ListOf("adjustment")}),
	"low_stock":          Same("product_id", "sku", "name", "available_quantity"),
	"system_status":      Same("outbox_pending", "outbox_oldest_pending_at"),
	"dashboard":          Schema{"recent_orders": ListOf("order"), "low_stock": ListOf("low_stock"), "system": Object("system_status")},
	"registered":         Same("id", "email"),
	"recommendation":     Same("product_id", "sku", "name", "price_minor", "currency"),
	"recommendations":    Same("strategy", "degraded").With(Schema{"items": ListOf("recommendation")}),
}

var endpoints = []*Endpoint{
	{Name: "catalog.list", Method: "GET", Path: "/api/catalog/products", Service: Catalog, Upstream: "/api/catalog/products", Auth: Public, Query: Fields{"sort": Enum("relevance", "newest", "price_asc", "price_desc", "name"), "category": Slug(80), "q": Text(0, 100), "min_price": Int(0, Unbounded), "max_price": Int(0, Unbounded), "page": Int(1, 10000), "page_size": Int(1, 100)}, Response: "product_page", Cache: time.Second},
	{Name: "catalog.search", Method: "GET", Path: "/api/catalog/search", Service: Catalog, Upstream: "/api/catalog/products", Auth: Public, Query: Fields{"sort": Enum("relevance", "newest", "price_asc", "price_desc", "name"), "category": Slug(80), "q": Text(0, 100), "min_price": Int(0, Unbounded), "max_price": Int(0, Unbounded), "page": Int(1, 10000), "page_size": Int(1, 100)}, Response: "product_page", Cache: time.Second},
	{Name: "catalog.get", Method: "GET", Path: "/api/catalog/products/{id}", Service: Catalog, Upstream: "/api/catalog/products/{id}", Auth: Public, Response: "product_public", Cache: time.Second},
	{Name: "catalog.categories", Method: "GET", Path: "/api/catalog/categories", Service: Catalog, Upstream: "/api/catalog/categories", Auth: Public, Response: "category_tree", Cache: 5 * time.Second},
	{Name: "recommendations", Method: "GET", Path: "/api/recommendations", Service: Intelligence, Upstream: "/api/intelligence/recommendations", Auth: Public, Query: Fields{"product_id": UUID(), "limit": Int(1, 20)}, Response: "recommendations", Timeout: 800 * time.Millisecond, Degrade: &Degrade{Status: 200, Body: `{"items":[],"strategy":"none","degraded":true}`}, Cache: 5 * time.Second},
	{Name: "auth.register", Method: "POST", Path: "/api/auth/register", Service: Auth, Upstream: "/api/auth/register", Auth: Public, Body: Fields{"email": Email(254).Mandatory(), "password": Text(12, 128).Mandatory()}, Response: "registered", Limit: "register"},
	{Name: "cart.get", Method: "GET", Path: "/api/cart", Service: Commerce, Upstream: "/api/commerce/cart", Auth: Customer, Response: "cart"},
	{Name: "cart.put", Method: "PUT", Path: "/api/cart", Service: Commerce, Upstream: "/api/commerce/cart", Auth: Customer, Body: Fields{"items": List(50, Fields{"product_id": UUID().Mandatory(), "quantity": Int(1, 1000).Mandatory()}).Mandatory()}, Response: "cart"},
	{Name: "checkout", Method: "POST", Path: "/api/checkout", Service: Commerce, Upstream: "/api/commerce/checkout", Auth: Customer, Body: Fields{"items": List(50, Fields{"product_id": UUID().Mandatory(), "quantity": Int(1, 1000).Mandatory()}).Mandatory()}, Response: "order", RequestHeaders: []string{"Idempotency-Key"}, ResponseHeaders: []string{"Idempotent-Replayed"}, Limit: "checkout", Timeout: 10000 * time.Millisecond},
	{Name: "orders.list", Method: "GET", Path: "/api/orders", Service: Commerce, Upstream: "/api/commerce/orders", Auth: Customer, Query: Fields{"page": Int(1, 10000), "page_size": Int(1, 100)}, Response: "order_page"},
	{Name: "orders.get", Method: "GET", Path: "/api/orders/{id}", Service: Commerce, Upstream: "/api/commerce/orders/{id}", Auth: Customer, Response: "order"},
	{Name: "orders.pay", Method: "POST", Path: "/api/orders/{id}/pay", Service: Commerce, Upstream: "/api/commerce/orders/{id}/pay", Auth: Customer, Body: Fields{"source": Enum(PaymentSources...).Mandatory()}, Response: "order", Limit: "checkout", Timeout: 10 * time.Second},
	{Name: "admin.products.list", Method: "GET", Path: "/api/admin/products", Service: Commerce, Upstream: "/api/commerce/admin/products", Auth: ShopOwner, Query: Fields{"status": Enum("draft", "active", "archived"), "category": Slug(80), "q": Text(0, 100), "page": Int(1, 10000), "page_size": Int(1, 100)}, Response: "product_admin_page"},
	{Name: "admin.products.create", Method: "POST", Path: "/api/admin/products", Service: Commerce, Upstream: "/api/commerce/admin/products", Auth: ShopOwner, Body: Fields{"name": Text(1, 200).Mandatory(), "description": Text(0, 20000), "price_minor": Int(0, 1000000000000).Mandatory(), "currency": Text(3, 3), "category": Slug(80).Mandatory(), "category_name": Text(0, 120), "status": Enum("draft", "active"), "image_keys": Strings(8, ImageKey()), "sku": SKU().Mandatory(), "initial_stock": Int(0, 1000000000)}, Response: "product_admin", ResponseHeaders: []string{"ETag"}},
	{Name: "admin.products.get", Method: "GET", Path: "/api/admin/products/{id}", Service: Commerce, Upstream: "/api/commerce/admin/products/{id}", Auth: ShopOwner, Response: "product_admin", ResponseHeaders: []string{"ETag"}},
	{Name: "admin.products.edit", Method: "PATCH", Path: "/api/admin/products/{id}", Service: Commerce, Upstream: "/api/commerce/admin/products/{id}", Auth: ShopOwner, Body: Fields{"name": Text(1, 200), "description": Text(0, 20000), "price_minor": Int(0, 1000000000000), "currency": Text(3, 3), "category": Slug(80), "category_name": Text(0, 120), "status": Enum("draft", "active"), "image_keys": Strings(8, ImageKey())}, Response: "product_admin", RequestHeaders: []string{"If-Match"}, ResponseHeaders: []string{"ETag"}},
	{Name: "admin.products.remove", Method: "DELETE", Path: "/api/admin/products/{id}", Service: Commerce, Upstream: "/api/commerce/admin/products/{id}", Auth: ShopOwner, Query: Fields{"permanent": Bool()}, Response: "product_admin", ResponseHeaders: []string{"ETag"}},
	{Name: "admin.products.restore", Method: "POST", Path: "/api/admin/products/{id}/restore", Service: Commerce, Upstream: "/api/commerce/admin/products/{id}/restore", Auth: ShopOwner, Response: "product_admin", ResponseHeaders: []string{"ETag"}},
	{Name: "admin.inventory.adjust", Method: "POST", Path: "/api/admin/inventory/{id}/adjust", Service: Commerce, Upstream: "/api/commerce/admin/inventory/{id}/adjust", Auth: ShopOwner, Body: Fields{"delta": Int(-1000000000, 1000000000).Mandatory(), "reason": Text(1, 200).Mandatory()}, Response: "adjustment"},
	{Name: "admin.inventory.history", Method: "GET", Path: "/api/admin/inventory/{id}/adjustments", Service: Commerce, Upstream: "/api/commerce/admin/inventory/{id}/adjustments", Auth: ShopOwner, Query: Fields{"page": Int(1, 10000), "page_size": Int(1, 100)}, Response: "adjustment_page"},
	{Name: "admin.orders.list", Method: "GET", Path: "/api/admin/orders", Service: Commerce, Upstream: "/api/commerce/admin/orders", Auth: ShopOwner, Query: Fields{"status": Enum("pending_payment", "paid", "payment_failed", "expired", "cancelled", "fulfilled", "refunded"), "page": Int(1, 10000), "page_size": Int(1, 100)}, Response: "order_admin_page"},
	{Name: "admin.orders.refund", Method: "POST", Path: "/api/admin/orders/{id}/refund", Service: Commerce, Upstream: "/api/commerce/admin/orders/{id}/refund", Auth: ShopOwner, Response: "order_admin", Timeout: 10 * time.Second},
	{Name: "admin.dashboard", Method: "GET", Path: "/api/admin/dashboard", Service: Commerce, Upstream: "/api/commerce/admin/dashboard", Auth: ShopOwner, Response: "dashboard"},
}

// ── schema helpers ────────────────────────────────────────────────────────────

// Same exposes upstream fields under their own names.
func Same(names ...string) Schema {
	s := Schema{}
	for _, n := range names {
		s[n] = FieldSpec{}
	}
	return s
}

// With adds (or overrides) fields.
func (s Schema) With(more Schema) Schema {
	for k, v := range more {
		s[k] = v
	}
	return s
}

// As renames an upstream field.
func As(public string) FieldSpec { return FieldSpec{As: public} }

// Object shapes a nested object by another schema.
func Object(schema string) FieldSpec { return FieldSpec{Schema: schema} }

// ListOf shapes every element of a list by another schema.
func ListOf(schema string) FieldSpec { return FieldSpec{List: schema} }

// Raw passes a value through as is (only for problem `errors`, built by the services from field names).
func Raw() FieldSpec { return FieldSpec{Raw: true} }

// ── field helpers ─────────────────────────────────────────────────────────────

// Unbounded leaves a limit open.
const Unbounded = -1

func bound(n int64) *int64 {
	if n == Unbounded {
		return nil
	}
	return &n
}

func Text(min, max int64) Field   { return Field{Type: "string", Min: bound(min), Max: bound(max)} }
func Int(min, max int64) Field    { return Field{Type: "int", Min: bound(min), Max: bound(max)} }
func Enum(values ...string) Field { return Field{Type: "string", Enum: values} }
func Slug(max int64) Field        { return Field{Type: "slug", Max: bound(max)} }
func Email(max int64) Field       { return Field{Type: "email", Max: bound(max)} }
func UUID() Field                 { return Field{Type: "uuid"} }
func Bool() Field                 { return Field{Type: "bool"} }
func SKU() Field                  { return Field{Type: "sku"} }
func ImageKey() Field             { return Field{Type: "image_key"} }

// List is a list of objects with the given fields.
func List(max int64, item Fields) Field { return Field{Type: "list", Max: bound(max), Item: item} }

// Strings is a list of scalar values.
func Strings(max int64, of Field) Field { return Field{Type: "strings", Max: bound(max), Of: &of} }

// Mandatory marks a field as required.
func (f Field) Mandatory() Field {
	f.Required = true
	return f
}
