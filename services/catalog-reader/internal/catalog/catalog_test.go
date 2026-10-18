package catalog

// Integration tests against a real PostgreSQL (CATALOG_TEST_DATABASE_URL, a
// role that may create schemas). Each run builds a throwaway schema whose two
// tables have exactly the columns of commerce-core's views.

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"math/rand"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5/pgxpool"
)

func setup(t *testing.T) http.Handler {
	dsn := os.Getenv("CATALOG_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("CATALOG_TEST_DATABASE_URL not set")
	}
	schema := fmt.Sprintf("catalog_test_%d", rand.Int63())
	admin, err := pgxpool.New(context.Background(), dsn)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { admin.Exec(context.Background(), "DROP SCHEMA "+schema+" CASCADE"); admin.Close() })
	_, err = admin.Exec(context.Background(), `CREATE SCHEMA `+schema+`;
		CREATE TABLE `+schema+`.catalog_public (id uuid, sku text, name text, description text, price_minor bigint, currency text,
			category_slug text, category_name text, available_quantity int, image_keys json, created_at timestamptz, updated_at timestamptz);
		CREATE TABLE `+schema+`.catalog_category_public (slug text, name text, parent_slug text);
		INSERT INTO `+schema+`.catalog_public VALUES
		 ('11111111-1111-4111-8111-111111111111','MUG-001','Ceramic Mug','350 ml, dishwasher safe.',1299,'USD','kitchen','Kitchen',40,'["uploads/mug.webp"]', now() - interval '2 day', now()),
		 ('22222222-2222-4222-8222-222222222222','KTL-001','Gooseneck Kettle','For pour-over coffee 100%_off',4999,'USD','kitchen','Kitchen',0,'[]', now() - interval '1 day', now()),
		 ('33333333-3333-4333-8333-333333333333','CND-001','Soy Candle','Cedar and smoke.',1899,'USD','home','Home',60,'[]', now(), now());
		INSERT INTO `+schema+`.catalog_category_public VALUES ('home','Home',NULL),('kitchen','Kitchen','home'),('outdoor','Outdoor',NULL);`)
	if err != nil {
		t.Fatal(err)
	}
	cfg, _ := pgxpool.ParseConfig(dsn)
	cfg.ConnConfig.RuntimeParams["search_path"] = schema
	pool, err := pgxpool.NewWithConfig(context.Background(), cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)
	mux := http.NewServeMux()
	(&Handler{DB: pool, AssetBaseURL: "http://img/", PageSize: 24, Log: slog.New(slog.NewTextHandler(io.Discard, nil))}).Routes(mux)
	return mux
}

func get(t *testing.T, h http.Handler, target string) (int, map[string]any) {
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest("GET", target, nil))
	var body map[string]any
	json.Unmarshal(rec.Body.Bytes(), &body)
	return rec.Code, body
}

func skus(body map[string]any) string {
	var out []string
	for _, item := range body["items"].([]any) {
		out = append(out, item.(map[string]any)["sku"].(string))
	}
	return strings.Join(out, ",")
}

func TestListNewestFirstWithPaging(t *testing.T) {
	h := setup(t)
	code, body := get(t, h, "/api/catalog/products?page_size=2")
	if code != 200 || skus(body) != "CND-001,KTL-001" || body["total"].(float64) != 3 || body["pages"].(float64) != 2 {
		t.Fatalf("%d %v", code, body)
	}
	_, body = get(t, h, "/api/catalog/products?page_size=2&page=2")
	if skus(body) != "MUG-001" || body["total"].(float64) != 3 {
		t.Fatalf("page 2: %v", body)
	}
	_, body = get(t, h, "/api/catalog/products?page_size=2&page=9")
	if len(body["items"].([]any)) != 0 || body["total"].(float64) != 3 || body["pages"].(float64) != 2 {
		t.Fatalf("past the end: %v", body)
	}
}

func TestFiltersAndSearch(t *testing.T) {
	h := setup(t)
	cases := map[string]string{
		"/api/catalog/products?category=kitchen":              "KTL-001,MUG-001",
		"/api/catalog/products?min_price=1500&max_price=2000": "CND-001",
		"/api/catalog/products?q=smoke":                       "CND-001",
		"/api/catalog/products?q=kitchen":                     "MUG-001,KTL-001", // category names are searched too
		"/api/catalog/products?q=mug":                         "MUG-001",
		"/api/catalog/products?q=ktl-001":                     "KTL-001",
		"/api/catalog/products?q=%25":                         "", // a literal %, not a wildcard
		"/api/catalog/products?sort=price_asc":                "MUG-001,CND-001,KTL-001",
		"/api/catalog/products?sort=price_desc":               "KTL-001,CND-001,MUG-001",
		"/api/catalog/products?sort=name&category=kitchen":    "MUG-001,KTL-001",
	}
	for target, want := range cases {
		if _, body := get(t, h, target); skus(body) != want {
			t.Errorf("%s -> %q, want %q", target, skus(body), want)
		}
	}
}

func TestProductShapeMatchesCommerceCore(t *testing.T) {
	h := setup(t)
	code, body := get(t, h, "/api/catalog/products/11111111-1111-4111-8111-111111111111")
	if code != 200 || body["in_stock"] != true || body["category"].(map[string]any)["slug"] != "kitchen" {
		t.Fatalf("%d %v", code, body)
	}
	img := body["images"].([]any)[0].(map[string]any)
	if img["url"] != "http://img/uploads/mug.webp" || img["position"].(float64) != 0 {
		t.Errorf("image: %v", img)
	}
	if _, has := body["available_quantity"]; has {
		t.Error("stock level exposed")
	}
	_, out := get(t, h, "/api/catalog/products/22222222-2222-4222-8222-222222222222")
	if out["in_stock"] != false {
		t.Error("out-of-stock product shown in stock")
	}
}

func TestBadInputAndMissingProducts(t *testing.T) {
	h := setup(t)
	for _, target := range []string{"/api/catalog/products?page=0", "/api/catalog/products?category=Bad%20Slug", "/api/catalog/products?category=kitchen;x=1",
		"/api/catalog/products?q=" + strings.Repeat("a", 101), "/api/catalog/products?min_price=-1",
		"/api/catalog/products?sort=price_minor;DROP"} {
		if code, _ := get(t, h, target); code != 400 {
			t.Errorf("%s -> %d", target, code)
		}
	}
	if code, _ := get(t, h, "/api/catalog/products/99999999-9999-4999-8999-999999999999"); code != 404 {
		t.Error("missing product not 404")
	}
	if code, _ := get(t, h, "/api/catalog/products/not-a-uuid"); code != 404 {
		t.Error("bad id not 404")
	}
}

func TestCategoryTree(t *testing.T) {
	h := setup(t)
	_, body := get(t, h, "/api/catalog/categories")
	b, _ := json.Marshal(body)
	want := `{"items":[{"children":[{"children":[],"name":"Kitchen","slug":"kitchen"}],"name":"Home","slug":"home"},{"children":[],"name":"Outdoor","slug":"outdoor"}]}`
	if string(b) != want {
		t.Fatalf("got %s", b)
	}
}
