package contract

import (
	"encoding/json"
	"errors"
	"net/url"
	"strings"
	"testing"
	"time"
)

var limits = []string{"register", "checkout"}

func load(t *testing.T) *Contract {
	t.Helper()
	c, err := Default(limits)
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func endpoint(t *testing.T, c *Contract, method, path string) *Endpoint {
	t.Helper()
	for _, e := range c.Endpoints {
		if e.Method == method && e.Path == path {
			return e
		}
	}
	t.Fatalf("no endpoint %s %s", method, path)
	return nil
}

func TestTheShippedContractLoads(t *testing.T) {
	c := load(t)
	if len(c.Endpoints) < 23 {
		t.Fatalf("only %d endpoints", len(c.Endpoints))
	}
	if e := endpoint(t, c, "GET", "/api/catalog/products/{id}"); len(e.Params()) != 1 || e.Params()[0] != "id" {
		t.Fatalf("params %v", e.Params())
	}
}

func TestBrokenContractsAreRejectedAtBoot(t *testing.T) {
	problem := map[string]Schema{"problem": {}}
	ok := func() *Endpoint {
		return &Endpoint{Name: "x", Method: "GET", Path: "/api/x", Service: Auth, Upstream: "/x", Auth: Public, Response: "problem"}
	}
	with := func(change func(*Endpoint)) []*Endpoint { e := ok(); change(e); return []*Endpoint{e} }
	cases := map[string]struct {
		schemas   map[string]Schema
		endpoints []*Endpoint
	}{
		"no problem schema": {map[string]Schema{}, nil},
		"dangling schema":   {map[string]Schema{"problem": {}, "a": {"x": Object("nope")}}, nil},
		"unknown service":   {problem, with(func(e *Endpoint) { e.Service = "db" })},
		"bad auth":          {problem, with(func(e *Endpoint) { e.Auth = "admin" })},
		"no response":       {problem, with(func(e *Endpoint) { e.Response = "" })},
		"forwarded authz":   {problem, with(func(e *Endpoint) { e.RequestHeaders = []string{"Authorization"} })},
		"forwarded gateway": {problem, with(func(e *Endpoint) { e.RequestHeaders = []string{"X-Gateway-Signature"} })},
		"GET with body":     {problem, with(func(e *Endpoint) { e.Body = Fields{} })},
		"duplicate route":   {problem, append(with(func(*Endpoint) {}), with(func(e *Endpoint) { e.Name = "y" })...)},
		"non-api path":      {problem, with(func(e *Endpoint) { e.Path = "/internal" })},
		"unused parameter":  {problem, with(func(e *Endpoint) { e.Path = "/api/x/{id}" })},
		"wildcard":          {problem, with(func(e *Endpoint) { e.Path, e.Upstream = "/api/x/{rest...}", "/x/{rest...}" })},
		"bad degrade":       {problem, with(func(e *Endpoint) { e.Degrade = &Degrade{Status: 200, Body: "{"} })},
		"unknown limit":     {problem, with(func(e *Endpoint) { e.Limit = "burst" })},
		"cached private":    {problem, with(func(e *Endpoint) { e.Auth, e.Cache = Customer, time.Second })},
		"cached write":      {problem, with(func(e *Endpoint) { e.Method, e.Cache = "POST", time.Second })},
	}
	for name, tc := range cases {
		if _, err := New(tc.schemas, tc.endpoints, limits); err == nil {
			t.Errorf("%s: accepted", name)
		}
	}
}

func TestUpstreamPathAndUUIDs(t *testing.T) {
	e := endpoint(t, load(t), "GET", "/api/catalog/products/{id}")
	if e.UpstreamPath(map[string]string{"id": "7520ad71-763d-470f-b7a4-754f1949f811"}) != "/api/catalog/products/7520ad71-763d-470f-b7a4-754f1949f811" {
		t.Error("upstream path not filled")
	}
	for _, bad := range []string{"..", "1 or 1=1", "7520ad71-763d-470f-b7a4-754f1949f81", ""} {
		if ValidUUID(bad) {
			t.Errorf("%q accepted as a UUID", bad)
		}
	}
}

func TestPaymentRoutes(t *testing.T) {
	c := load(t)
	pay := endpoint(t, c, "POST", "/api/orders/{id}/pay")
	if _, err := pay.ValidateBody([]byte(`{"source":"tok_visa"}`)); err != nil {
		t.Fatal(err)
	}
	for _, raw := range []string{`{"source":"4242424242424242"}`, `{}`, `{"source":"tok_visa","amount_minor":1}`} {
		if _, err := pay.ValidateBody([]byte(raw)); err == nil {
			t.Errorf("accepted %s", raw)
		}
	}
	if endpoint(t, c, "POST", "/api/admin/orders/{id}/refund").Auth != ShopOwner {
		t.Error("refunds are for the shop owner")
	}
}

func TestQueryOnlyDeclaredParametersCanonicallyEncoded(t *testing.T) {
	c := load(t)
	e := endpoint(t, c, "GET", "/api/catalog/products")
	q, err := e.QueryString(url.Values{"q": {"ceramic mug"}, "page": {"2"}, "category": {"kitchen"}})
	if err != nil || q != "category=kitchen&page=2&q=ceramic+mug" {
		t.Fatalf("got %q %v", q, err)
	}
	_, err = e.QueryString(url.Values{"debug": {"1"}, "page": {"0"}, "category": {"Kitchen; DROP"}, "q": {"a", "b"}})
	var verr ValidationError
	if !errors.As(err, &verr) || len(verr) != 4 {
		t.Fatalf("want 4 field errors, got %v", err)
	}
}

func TestBodyRefusesUnknownFieldsAndBadTypes(t *testing.T) {
	c := load(t)
	e := endpoint(t, c, "POST", "/api/checkout")
	if _, err := e.ValidateBody([]byte(`{"items":[{"product_id":"7520ad71-763d-470f-b7a4-754f1949f811","quantity":2}]}`)); err != nil {
		t.Fatal(err)
	}
	_, err := e.ValidateBody([]byte(`{"items":[{"product_id":"nope","quantity":0,"price_minor":1}],"customer_id":"x"}`))
	var verr ValidationError
	if !errors.As(err, &verr) {
		t.Fatal(err)
	}
	for _, field := range []string{"items[0].product_id", "items[0].quantity", "items[0].price_minor", "customer_id"} {
		if _, ok := verr[field]; !ok {
			t.Errorf("no error for %s: %v", field, verr)
		}
	}
	for _, raw := range []string{``, `[]`, `{"items":[]} {"x":1}`, `{"items":"all"}`} {
		if _, err := e.ValidateBody([]byte(raw)); err == nil {
			t.Errorf("accepted %q", raw)
		}
	}
}

func TestRegistrationCannotSmuggleRoles(t *testing.T) {
	c := load(t)
	e := endpoint(t, c, "POST", "/api/auth/register")
	if _, err := e.ValidateBody([]byte(`{"email":"a@b.co","password":"long-enough-pass","roles":["shop_owner"]}`)); err == nil {
		t.Fatal("roles accepted")
	}
}

func TestShapeStripsInternalFields(t *testing.T) {
	c := load(t)
	var upstream any
	json.Unmarshal([]byte(`{"id":"p1","sku":"MUG","name":"Mug","price_minor":1,"currency":"USD","in_stock":true,
		"status":"active","version":3,"available_quantity":40,"cost_minor":200,
		"category":{"slug":"kitchen","name":"Kitchen","internal_id":9},
		"images":[{"key":"uploads/a.png","url":"http://x/a.png","position":0}]}`), &upstream)
	out, err := c.Shape("product_public", upstream)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := json.Marshal(out)
	for _, leaked := range []string{"status", "version", "available_quantity", "cost_minor", "internal_id", `"key"`} {
		if strings.Contains(string(b), leaked) {
			t.Errorf("public product leaks %s: %s", leaked, b)
		}
	}
	if !strings.Contains(string(b), `"url":"http://x/a.png"`) {
		t.Errorf("public fields lost: %s", b)
	}
}

func TestUndeclaredNestedObjectsAreNeverPassedThrough(t *testing.T) {
	c, _ := New(map[string]Schema{"problem": {}, "s": Same("a")}, nil, limits)
	if _, err := c.Shape("s", map[string]any{"a": map[string]any{"secret": 1}}); err == nil {
		t.Fatal("an object without a schema was exposed")
	}
}

func TestRecursiveCategoryTree(t *testing.T) {
	c := load(t)
	var upstream any
	json.Unmarshal([]byte(`{"items":[{"slug":"home","name":"Home","id":"x","children":[{"slug":"kitchen","name":"Kitchen","children":[]}]}]}`), &upstream)
	out, err := c.Shape("category_tree", upstream)
	b, _ := json.Marshal(out)
	if err != nil || strings.Contains(string(b), `"id"`) || !strings.Contains(string(b), "kitchen") {
		t.Fatalf("%s %v", b, err)
	}
}

func TestFitsOnlyWhenNothingWouldChange(t *testing.T) {
	c := load(t)
	var ok, extra, nested any
	json.Unmarshal([]byte(`{"items":[{"id":"p1","sku":"MUG","name":"Mug","category":{"slug":"k","name":"K"},"images":[]}],"total":1,"page":1,"page_size":24,"pages":1}`), &ok)
	json.Unmarshal([]byte(`{"items":[{"id":"p1","cost_minor":200}],"total":1}`), &extra)
	json.Unmarshal([]byte(`{"items":[{"id":"p1","category":{"slug":"k","internal_id":9}}]}`), &nested)
	if !c.Fits("product_page", ok) {
		t.Error("an exact answer must pass through")
	}
	if c.Fits("product_page", extra) || c.Fits("product_page", nested) {
		t.Error("an answer with an unlisted field must be shaped")
	}
	if c.Fits("product_page", map[string]any{"total": map[string]any{"x": 1}}) {
		t.Error("an object where a scalar is listed must be shaped (and refused)")
	}
}
