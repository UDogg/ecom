package contract

import (
	"encoding/json"
	"errors"
	"net/url"
	"strings"
	"testing"
)

var services = []string{"catalog", "commerce", "auth", "intelligence"}
var limits = []string{"register", "checkout"}

func load(t *testing.T) *Contract {
	t.Helper()
	c, err := Load(Default, services, limits)
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func TestTheShippedContractLoads(t *testing.T) {
	c := load(t)
	if len(c.Endpoints) < 20 {
		t.Fatalf("only %d endpoints", len(c.Endpoints))
	}
}

func TestBrokenContractsAreRejectedAtBoot(t *testing.T) {
	cases := map[string]string{
		"unknown key":       `{"schemas":{"problem":{}},"endpoints":[],"extra":1}`,
		"no problem schema": `{"schemas":{},"endpoints":[]}`,
		"dangling schema":   `{"schemas":{"problem":{},"a":{"x":{"schema":"nope"}}},"endpoints":[]}`,
		"unknown service":   `{"schemas":{"problem":{}},"endpoints":[{"name":"x","method":"GET","path":"/api/x","service":"db","upstream":"/x","auth":"public"}]}`,
		"bad auth":          `{"schemas":{"problem":{}},"endpoints":[{"name":"x","method":"GET","path":"/api/x","service":"auth","upstream":"/x","auth":"admin"}]}`,
		"forwarded authz":   `{"schemas":{"problem":{}},"endpoints":[{"name":"x","method":"GET","path":"/api/x","service":"auth","upstream":"/x","auth":"public","request_headers":["Authorization"]}]}`,
		"forwarded gateway": `{"schemas":{"problem":{}},"endpoints":[{"name":"x","method":"GET","path":"/api/x","service":"auth","upstream":"/x","auth":"public","request_headers":["X-Gateway-Signature"]}]}`,
		"GET with body":     `{"schemas":{"problem":{}},"endpoints":[{"name":"x","method":"GET","path":"/api/x","service":"auth","upstream":"/x","auth":"public","body":{}}]}`,
		"duplicate route":   `{"schemas":{"problem":{}},"endpoints":[{"name":"x","method":"GET","path":"/api/x","service":"auth","upstream":"/x","auth":"public"},{"name":"y","method":"GET","path":"/api/x","service":"auth","upstream":"/y","auth":"public"}]}`,
		"non-api path":      `{"schemas":{"problem":{}},"endpoints":[{"name":"x","method":"GET","path":"/internal","service":"auth","upstream":"/x","auth":"public"}]}`,
	}
	for name, raw := range cases {
		if _, err := Load([]byte(raw), services, limits); err == nil {
			t.Errorf("%s: accepted", name)
		}
	}
}

func TestMatchRequiresUUIDParameters(t *testing.T) {
	c := load(t)
	e, params, _ := c.Match("GET", "/api/catalog/products/7520AD71-763d-470f-b7a4-754f1949f811")
	if e == nil || e.Name != "catalog.get" || params["id"] != "7520ad71-763d-470f-b7a4-754f1949f811" {
		t.Fatalf("got %v %v", e, params)
	}
	for _, p := range []string{"/api/catalog/products/../orders", "/api/catalog/products/1%20or%201=1", "/api/catalog/products/x/y"} {
		if e, _, _ := c.Match("GET", p); e != nil {
			t.Errorf("%s matched %s", p, e.Name)
		}
	}
	if _, _, other := c.Match("DELETE", "/api/catalog/products"); !other {
		t.Error("wrong method on a known path should be distinguishable (405)")
	}
	if e.UpstreamPath(params) != "/api/catalog/products/7520ad71-763d-470f-b7a4-754f1949f811" {
		t.Error("upstream path not filled")
	}
}

func TestQueryOnlyDeclaredParametersCanonicallyEncoded(t *testing.T) {
	c := load(t)
	e, _, _ := c.Match("GET", "/api/catalog/products")
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
	e, _, _ := c.Match("POST", "/api/checkout")
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
	e, _, _ := c.Match("POST", "/api/auth/register")
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
	c, _ := Load([]byte(`{"schemas":{"problem":{},"s":{"a":"a"}},"endpoints":[]}`), services, limits)
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
