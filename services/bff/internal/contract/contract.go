// Package contract is the public API, declared in one file (contract.json).
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

// FieldSpec says how one upstream response field appears publicly.
type FieldSpec struct {
	As     string `json:"as"`
	Schema string `json:"schema"`
	List   string `json:"list"`
	Raw    bool   `json:"raw"`
}

func (f *FieldSpec) UnmarshalJSON(b []byte) error {
	var as string
	if json.Unmarshal(b, &as) == nil {
		f.As = as
		return nil
	}
	type plain FieldSpec
	return json.Unmarshal(b, (*plain)(f))
}

// Schema maps upstream field names to their public form.
type Schema map[string]FieldSpec

// Field is one allowed request body field.
type Field struct {
	Type     string           `json:"type"` // string, int, bool, uuid, slug, sku, email, image_key, list, strings
	Min      *int64           `json:"min"`
	Max      *int64           `json:"max"`
	Required bool             `json:"required"`
	Enum     []string         `json:"enum"`
	Item     map[string]Field `json:"item"` // for list: the allowed object fields
	Of       *Field           `json:"of"`   // for strings: each element
}

// Degrade is what the BFF answers when an optional upstream is down.
type Degrade struct {
	Status int             `json:"status"`
	Body   json.RawMessage `json:"body"`
}

// Endpoint is one public route.
type Endpoint struct {
	Name            string           `json:"name"`
	Method          string           `json:"method"`
	Path            string           `json:"path"`
	Service         string           `json:"service"`
	Upstream        string           `json:"upstream"`
	Auth            string           `json:"auth"`
	Query           map[string]Field `json:"query"`
	Body            map[string]Field `json:"body"`
	Response        string           `json:"response"`
	RequestHeaders  []string         `json:"request_headers"`
	ResponseHeaders []string         `json:"response_headers"`
	Limit           string           `json:"limit"`
	TimeoutMS       int              `json:"timeout_ms"`
	Degrade         *Degrade         `json:"degrade"`

	segments []string
}

// Contract is the parsed, validated contract.
type Contract struct {
	Schemas   map[string]Schema `json:"schemas"`
	Endpoints []*Endpoint       `json:"endpoints"`
}

// Load parses and validates; any mistake is fatal at boot, never at request time.
func Load(raw []byte, services []string, limits []string) (*Contract, error) {
	var c Contract
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&c); err != nil {
		return nil, fmt.Errorf("contract: %w", err)
	}
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
		case !oneOf(e.Service, services...):
			return nil, fmt.Errorf("contract: %s: unknown service %q", e.Name, e.Service)
		case !oneOf(e.Auth, Public, Customer, ShopOwner):
			return nil, fmt.Errorf("contract: %s: auth must be public, customer or shop_owner", e.Name)
		case e.Response != "" && c.Schemas[e.Response] == nil:
			return nil, fmt.Errorf("contract: %s: unknown response schema %q", e.Name, e.Response)
		case e.Limit != "" && !oneOf(e.Limit, limits...):
			return nil, fmt.Errorf("contract: %s: unknown rate limit %q", e.Name, e.Limit)
		case e.Body != nil && e.Method == "GET":
			return nil, fmt.Errorf("contract: %s: GET cannot take a body", e.Name)
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
		e.segments = strings.Split(strings.Trim(e.Path, "/"), "/")
		key := e.Method + " " + e.Path
		if routes[key] {
			return nil, fmt.Errorf("contract: %s: duplicate route %s", e.Name, key)
		}
		seen[e.Name], routes[key] = true, true
	}
	return &c, nil
}

func checkFields(endpoint string, fields map[string]Field) error {
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

// Match finds the endpoint for a request. Path parameters ({id}) must be UUIDs.
// It returns the endpoint, the parameters, and whether the path exists with
// another method (so the caller can answer 405 rather than 404).
func (c *Contract) Match(method, path string) (*Endpoint, map[string]string, bool) {
	segs := strings.Split(strings.Trim(path, "/"), "/")
	otherMethod := false
	for _, e := range c.Endpoints {
		params, ok := matchSegments(e.segments, segs)
		if !ok {
			continue
		}
		if e.Method == method {
			return e, params, false
		}
		otherMethod = true
	}
	return nil, nil, otherMethod
}

func matchSegments(pattern, segs []string) (map[string]string, bool) {
	if len(pattern) != len(segs) {
		return nil, false
	}
	params := map[string]string{}
	for i, p := range pattern {
		if strings.HasPrefix(p, "{") && strings.HasSuffix(p, "}") {
			if !uuidRE.MatchString(segs[i]) {
				return nil, false
			}
			params[p[1:len(p)-1]] = strings.ToLower(segs[i])
			continue
		}
		if p != segs[i] {
			return nil, false
		}
	}
	return params, true
}

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

func validateObject(fields map[string]Field, obj map[string]any, prefix string, errs ValidationError, depth int) {
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
