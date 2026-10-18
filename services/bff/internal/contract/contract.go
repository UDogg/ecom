// Package contract is the public API, declared in Go (routes.go) and compiled
// into the binary: the deployed API is exactly what was reviewed, with nothing
// to edit on a running host.
//
// Every endpoint a browser can call, every query parameter and body field it
// can send, and every response field it can see is listed there and nowhere
// else. A field a core service adds is not exposed until it is added here; a
// field a client sends that is not listed is refused. The contract describes
// representation only: no business rules, no authorisation decisions (the core
// services make those), no arithmetic.
package contract

import (
	"encoding/json"
	"fmt"
	"net/mail"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
)

var (
	uuidRE = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`)
	slugRE = regexp.MustCompile(`^[a-z0-9]+(?:-[a-z0-9]+)*$`)
	skuRE  = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`)
	keyRE  = regexp.MustCompile(`^uploads/[A-Za-z0-9][A-Za-z0-9/_.-]{0,250}\.(?i:jpe?g|png|webp)$`)
)

// Auth levels an endpoint can require.
const (
	Public    = "public"
	Customer  = "customer"
	ShopOwner = "shop_owner"
)

// The core services the BFF talks to. Their internal base URLs come from config.
const (
	Auth         = "auth"
	Commerce     = "commerce"
	Catalog      = "catalog"
	Intelligence = "intelligence"
)

// Services lists every upstream a route may name.
var Services = []string{Auth, Commerce, Catalog, Intelligence}

// FieldSpec says how one upstream response field appears publicly.
type FieldSpec struct {
	As     string
	Schema string
	List   string
	Raw    bool
}

// Schema maps upstream field names to their public form.
type Schema map[string]FieldSpec

// Field is one allowed request field (query parameter or body field).
type Field struct {
	Type     string // string, int, bool, uuid, slug, sku, email, image_key, list, strings
	Min      *int64
	Max      *int64
	Required bool
	Enum     []string
	Item     Fields // for list: the allowed object fields
	Of       *Field // for strings: each element
}

// Fields are the allowed fields of a query string or JSON object.
type Fields map[string]Field

// Degrade is what the BFF answers when an optional upstream is down.
type Degrade struct {
	Status int
	Body   string // JSON
}

// Endpoint is one public route.
type Endpoint struct {
	Name            string
	Method          string
	Path            string // a net/http pattern; {name} segments must be UUIDs
	Service         string
	Upstream        string
	Auth            string
	Query           Fields
	Body            Fields
	Response        string
	RequestHeaders  []string
	ResponseHeaders []string
	Limit           string
	Timeout         time.Duration
	Degrade         *Degrade
	Cache           time.Duration // public GETs only: share one upstream answer for this long

	params []string
}

// Params are the path parameter names, in order.
func (e *Endpoint) Params() []string { return e.params }

// Contract is the validated contract.
type Contract struct {
	Schemas   map[string]Schema
	Endpoints []*Endpoint
}

// Default is the compiled-in contract (routes.go), validated against the rate limits the server has.
func Default(limits []string) (*Contract, error) { return New(schemas, endpoints, limits) }

// New validates a contract; any mistake is fatal at boot, never at request time.
func New(schemas map[string]Schema, endpoints []*Endpoint, limits []string) (*Contract, error) {
	c := Contract{Schemas: schemas, Endpoints: endpoints}
	if _, ok := c.Schemas["problem"]; !ok {
		return nil, fmt.Errorf("contract: a `problem` schema is required (it shapes every error response)")
	}
	for name, s := range c.Schemas {
		for field, spec := range s {
			for _, ref := range []string{spec.Schema, spec.List} {
				if ref != "" && c.Schemas[ref] == nil {
					return nil, fmt.Errorf("contract: schema %s.%s refers to unknown schema %q", name, field, ref)
				}
			}
		}
	}
	seen := map[string]bool{}
	routes := map[string]bool{}
	for _, e := range c.Endpoints {
		switch {
		case e.Name == "" || seen[e.Name]:
			return nil, fmt.Errorf("contract: endpoint name %q missing or duplicated", e.Name)
		case !oneOf(e.Method, "GET", "POST", "PUT", "PATCH", "DELETE"):
			return nil, fmt.Errorf("contract: %s: method %q", e.Name, e.Method)
		case !strings.HasPrefix(e.Path, "/api/") || !strings.HasPrefix(e.Upstream, "/"):
			return nil, fmt.Errorf("contract: %s: public paths start with /api/, upstream paths with /", e.Name)
		case !oneOf(e.Service, Services...):
			return nil, fmt.Errorf("contract: %s: unknown service %q", e.Name, e.Service)
		case !oneOf(e.Auth, Public, Customer, ShopOwner):
			return nil, fmt.Errorf("contract: %s: auth must be public, customer or shop_owner", e.Name)
		case e.Response == "" || c.Schemas[e.Response] == nil:
			return nil, fmt.Errorf("contract: %s: unknown response schema %q", e.Name, e.Response)
		case e.Limit != "" && !oneOf(e.Limit, limits...):
			return nil, fmt.Errorf("contract: %s: unknown rate limit %q", e.Name, e.Limit)
		case e.Body != nil && e.Method == "GET":
			return nil, fmt.Errorf("contract: %s: GET cannot take a body", e.Name)
		case e.Cache > 0 && (e.Method != "GET" || e.Auth != Public):
			return nil, fmt.Errorf("contract: %s: only public GET endpoints may be cached (nothing user-specific is ever shared)", e.Name)
		case e.Degrade != nil && !json.Valid([]byte(e.Degrade.Body)):
			return nil, fmt.Errorf("contract: %s: degrade body is not JSON", e.Name)
		}
		for _, h := range e.RequestHeaders {
			if strings.EqualFold(h, "Authorization") || strings.EqualFold(h, "Cookie") || strings.HasPrefix(strings.ToLower(h), "x-gateway-") {
				return nil, fmt.Errorf("contract: %s: %s is set by the BFF, never forwarded from a client", e.Name, h)
			}
		}
		if err := checkFields(e.Name, e.Query); err != nil {
			return nil, err
		}
		if err := checkFields(e.Name, e.Body); err != nil {
			return nil, err
		}
		e.params = nil
		for _, seg := range strings.Split(strings.Trim(e.Path, "/"), "/") {
			if strings.HasPrefix(seg, "{") {
				name := strings.Trim(seg, "{}")
				if name == "" || strings.HasSuffix(name, "...") || !strings.Contains(e.Upstream, "{"+name+"}") {
					return nil, fmt.Errorf("contract: %s: path parameter %q must be a single segment used in the upstream path", e.Name, seg)
				}
				e.params = append(e.params, name)
			}
		}
		key := e.Method + " " + e.Path
		if routes[key] {
			return nil, fmt.Errorf("contract: %s: duplicate route %s", e.Name, key)
		}
		seen[e.Name], routes[key] = true, true
	}
	return &c, nil
}

func checkFields(endpoint string, fields Fields) error {
	for name, f := range fields {
		if !oneOf(f.Type, "string", "int", "bool", "uuid", "slug", "sku", "email", "image_key", "list", "strings") {
			return fmt.Errorf("contract: %s: field %s has unknown type %q", endpoint, name, f.Type)
		}
		if f.Type == "list" {
			if err := checkFields(endpoint, f.Item); err != nil {
				return err
			}
		}
		if f.Type == "strings" && f.Of == nil {
			return fmt.Errorf("contract: %s: field %s needs `of`", endpoint, name)
		}
	}
	return nil
}

func oneOf(s string, options ...string) bool {
	for _, o := range options {
		if s == o {
			return true
		}
	}
	return false
}

// ValidUUID reports whether a path parameter is a UUID (the only kind the API has).
func ValidUUID(s string) bool { return uuidRE.MatchString(s) }

// UpstreamPath fills the endpoint's upstream template with matched parameters.
func (e *Endpoint) UpstreamPath(params map[string]string) string {
	p := e.Upstream
	for k, v := range params {
		p = strings.ReplaceAll(p, "{"+k+"}", url.PathEscape(v))
	}
	return p
}

// ValidationError lists every problem with a request, keyed by field.
type ValidationError map[string]string

func (v ValidationError) Error() string {
	keys := make([]string, 0, len(v))
	for k := range v {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return "invalid request: " + strings.Join(keys, ", ")
}

// Query validates the client's query string and returns the exact string the
// BFF will send upstream: only declared parameters, canonically encoded.
func (e *Endpoint) QueryString(q url.Values) (string, error) {
	errs := ValidationError{}
	out := url.Values{}
	for name, values := range q {
		spec, ok := e.Query[name]
		if !ok {
			errs[name] = "not an accepted parameter"
			continue
		}
		if len(values) != 1 {
			errs[name] = "give it once"
			continue
		}
		v, err := coerce(spec, values[0])
		if err != nil {
			errs[name] = err.Error()
			continue
		}
		out.Set(name, fmt.Sprint(v))
	}
	for name, spec := range e.Query {
		if spec.Required && q.Get(name) == "" {
			errs[name] = "required"
		}
	}
	if len(errs) > 0 {
		return "", errs
	}
	return out.Encode(), nil
}

// coerce parses a query string value.
func coerce(f Field, raw string) (any, error) {
	switch f.Type {
	case "int":
		n, err := strconv.ParseInt(raw, 10, 64)
		if err != nil {
			return nil, fmt.Errorf("must be an integer")
		}
		return n, checkRange(f, n)
	case "bool":
		b, err := strconv.ParseBool(raw)
		if err != nil {
			return nil, fmt.Errorf("must be true or false")
		}
		return b, nil
	default:
		return raw, checkString(f, raw)
	}
}

func checkRange(f Field, n int64) error {
	if f.Min != nil && n < *f.Min {
		return fmt.Errorf("must be at least %d", *f.Min)
	}
	if f.Max != nil && n > *f.Max {
		return fmt.Errorf("must be at most %d", *f.Max)
	}
	return nil
}

func checkString(f Field, s string) error {
	n := int64(utf8.RuneCountInString(s))
	if !utf8.ValidString(s) || strings.ContainsRune(s, 0) {
		return fmt.Errorf("must be valid text")
	}
	if f.Min != nil && n < *f.Min {
		return fmt.Errorf("must be at least %d characters", *f.Min)
	}
	if f.Max != nil && n > *f.Max {
		return fmt.Errorf("must be at most %d characters", *f.Max)
	}
	if len(f.Enum) > 0 && !oneOf(s, f.Enum...) {
		return fmt.Errorf("must be one of %s", strings.Join(f.Enum, ", "))
	}
	switch f.Type {
	case "uuid":
		if !uuidRE.MatchString(s) {
			return fmt.Errorf("must be a UUID")
		}
	case "slug":
		if !slugRE.MatchString(s) {
			return fmt.Errorf("must be lower-case letters, digits and hyphens")
		}
	case "sku":
		if !skuRE.MatchString(s) {
			return fmt.Errorf("must be a SKU (letters, digits, . _ -)")
		}
	case "email":
		if a, err := mail.ParseAddress(s); err != nil || a.Address != s {
			return fmt.Errorf("must be an e-mail address")
		}
	case "image_key":
		if strings.Contains(s, "..") || strings.Contains(s, "//") || !keyRE.MatchString(s) {
			return fmt.Errorf("must be an uploaded image key")
		}
	}
	return nil
}

// Body validates a JSON body against the declared fields. Unknown fields are
// refused, not silently dropped, so a client learns its request was wrong.
func (e *Endpoint) ValidateBody(raw []byte) (map[string]any, error) {
	if e.Body == nil {
		if len(strings.TrimSpace(string(raw))) > 0 {
			return nil, ValidationError{"body": "this endpoint takes no body"}
		}
		return nil, nil
	}
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.UseNumber()
	var body map[string]any
	if err := dec.Decode(&body); err != nil || body == nil {
		return nil, ValidationError{"body": "must be a JSON object"}
	}
	if dec.More() {
		return nil, ValidationError{"body": "must be a single JSON object"}
	}
	errs := ValidationError{}
	validateObject(e.Body, body, "", errs, 0)
	if len(errs) > 0 {
		return nil, errs
	}
	return body, nil
}

func validateObject(fields Fields, obj map[string]any, prefix string, errs ValidationError, depth int) {
	if depth > 4 {
		errs[prefix] = "nested too deeply"
		return
	}
	for name, v := range obj {
		f, ok := fields[name]
		if !ok {
			errs[prefix+name] = "not an accepted field"
			continue
		}
		validateValue(f, v, prefix+name, errs, depth)
	}
	for name, f := range fields {
		if _, ok := obj[name]; f.Required && !ok {
			errs[prefix+name] = "required"
		}
	}
}

func validateValue(f Field, v any, path string, errs ValidationError, depth int) {
	switch f.Type {
	case "int":
		n, ok := v.(json.Number)
		i, err := n.Int64()
		if !ok || err != nil {
			errs[path] = "must be an integer"
			return
		}
		if err := checkRange(f, i); err != nil {
			errs[path] = err.Error()
		}
	case "bool":
		if _, ok := v.(bool); !ok {
			errs[path] = "must be true or false"
		}
	case "list":
		items, ok := v.([]any)
		if !ok {
			errs[path] = "must be a list"
			return
		}
		if f.Max != nil && int64(len(items)) > *f.Max {
			errs[path] = fmt.Sprintf("at most %d items", *f.Max)
			return
		}
		for i, item := range items {
			obj, ok := item.(map[string]any)
			if !ok {
				errs[fmt.Sprintf("%s[%d]", path, i)] = "must be an object"
				continue
			}
			validateObject(f.Item, obj, fmt.Sprintf("%s[%d].", path, i), errs, depth+1)
		}
	case "strings":
		items, ok := v.([]any)
		if !ok {
			errs[path] = "must be a list"
			return
		}
		if f.Max != nil && int64(len(items)) > *f.Max {
			errs[path] = fmt.Sprintf("at most %d items", *f.Max)
			return
		}
		for i, item := range items {
			validateValue(*f.Of, item, fmt.Sprintf("%s[%d]", path, i), errs, depth+1)
		}
	default:
		s, ok := v.(string)
		if !ok {
			errs[path] = "must be text"
			return
		}
		if err := checkString(f, s); err != nil {
			errs[path] = err.Error()
		}
	}
}

// Shape copies only the fields the schema lists, renamed to their public names.
func (c *Contract) Shape(schema string, v any) (any, error) {
	return c.shape(schema, v, 0)
}

func (c *Contract) shape(schema string, v any, depth int) (any, error) {
	if depth > 16 {
		return nil, fmt.Errorf("response nested too deeply")
	}
	if v == nil {
		return nil, nil
	}
	obj, ok := v.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("schema %s expects an object", schema)
	}
	out := map[string]any{}
	for name, spec := range c.Schemas[schema] {
		val, present := obj[name]
		if !present {
			continue
		}
		public := spec.As
		if public == "" {
			public = name
		}
		switch {
		case spec.Raw:
			out[public] = val
		case spec.Schema != "":
			shaped, err := c.shape(spec.Schema, val, depth+1)
			if err != nil {
				return nil, fmt.Errorf("%s.%s: %w", schema, name, err)
			}
			out[public] = shaped
		case spec.List != "":
			items, ok := val.([]any)
			if !ok {
				if val == nil {
					out[public] = nil
					continue
				}
				return nil, fmt.Errorf("%s.%s: expects a list", schema, name)
			}
			list := make([]any, 0, len(items))
			for _, item := range items {
				shaped, err := c.shape(spec.List, item, depth+1)
				if err != nil {
					return nil, fmt.Errorf("%s.%s: %w", schema, name, err)
				}
				list = append(list, shaped)
			}
			out[public] = list
		default:
			if _, nested := val.(map[string]any); nested {
				return nil, fmt.Errorf("%s.%s: an object needs a schema to be exposed", schema, name)
			}
			if _, nested := val.([]any); nested {
				return nil, fmt.Errorf("%s.%s: a list needs a schema to be exposed", schema, name)
			}
			out[public] = val
		}
	}
	return out, nil
}
