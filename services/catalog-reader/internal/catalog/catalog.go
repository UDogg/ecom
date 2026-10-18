// Package catalog serves the public catalogue from two read-only views:
// commerce.catalog_public (active products only) and
// commerce.catalog_category_public. The catalog_reader database role can
// SELECT those views and nothing else, so even a compromised catalog-reader
// cannot see orders, customers or stock history, let alone change them
// (README section 12, scenario F).
//
// The JSON matches commerce-core's public catalogue exactly, so the BFF
// contract does not care which service answers.
package catalog

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"math"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

var (
	uuidRE      = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`)
	slugRE      = regexp.MustCompile(`^[a-z0-9]+(?:-[a-z0-9]+)*$`)
	likeEscaper = strings.NewReplacer(`\`, `\\`, `%`, `\%`, `_`, `\_`)
)

const columns = `id::text, sku, name, description, price_minor, currency, category_slug, category_name,
	available_quantity, image_keys::text`

type Handler struct {
	DB           *pgxpool.Pool
	AssetBaseURL string
	PageSize     int
	Log          *slog.Logger
}

func (h *Handler) Routes(mux *http.ServeMux) {
	mux.HandleFunc("GET /api/catalog/products", h.list)
	mux.HandleFunc("GET /api/catalog/products/{id}", h.get)
	mux.HandleFunc("GET /api/catalog/categories", h.categories)
}

type image struct {
	Key      string `json:"key"`
	URL      string `json:"url"`
	Position int    `json:"position"`
}

type category struct {
	Slug string `json:"slug"`
	Name string `json:"name"`
}

type product struct {
	ID          string   `json:"id"`
	SKU         string   `json:"sku"`
	Name        string   `json:"name"`
	Description string   `json:"description"`
	PriceMinor  int64    `json:"price_minor"`
	Currency    string   `json:"currency"`
	Category    category `json:"category"`
	InStock     bool     `json:"in_stock"`
	Images      []image  `json:"images"`
}

// scan reads one row of `columns`, then any extra selected columns into extra.
func (h *Handler) scan(row pgx.Row, extra ...any) (product, error) {
	var p product
	var available int64
	var keys string
	err := row.Scan(append([]any{&p.ID, &p.SKU, &p.Name, &p.Description, &p.PriceMinor, &p.Currency, &p.Category.Slug, &p.Category.Name,
		&available, &keys}, extra...)...)
	if err != nil {
		return p, err
	}
	p.InStock = available > 0
	var list []string
	json.Unmarshal([]byte(keys), &list)
	p.Images = make([]image, 0, len(list))
	for i, k := range list {
		p.Images = append(p.Images, image{Key: k, URL: strings.TrimRight(h.AssetBaseURL, "/") + "/" + k, Position: i})
	}
	return p, nil
}

func problem(w http.ResponseWriter, status int, slug, title, detail string, extra map[string]any) {
	body := map[string]any{"type": "https://shop.local/errors/" + slug, "title": title, "status": status, "detail": detail}
	for k, v := range extra {
		body[k] = v
	}
	w.Header().Set("Content-Type", "application/problem+json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(body)
}

func writeJSON(w http.ResponseWriter, body any) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(body)
}

func intParam(q map[string][]string, name string, min, max int64) (*int64, error) {
	v, ok := q[name]
	if !ok {
		return nil, nil
	}
	n, err := strconv.ParseInt(v[0], 10, 64)
	if err != nil || n < min || n > max {
		return nil, errors.New(name)
	}
	return &n, nil
}

// orders are the only sort orders: fixed SQL, chosen by name, never built from input.
var orders = map[string]string{
	"relevance":  `ts_rank(to_tsvector('english', name || ' ' || description || ' ' || category_name), websearch_to_tsquery('english', $4)) DESC, name, id`,
	"newest":     `created_at DESC, id`,
	"price_asc":  `price_minor ASC, name, id`,
	"price_desc": `price_minor DESC, name, id`,
	"name":       `name, id`,
}

func (h *Handler) list(w http.ResponseWriter, r *http.Request) {
	q, err := url.ParseQuery(r.URL.RawQuery) // URL.Query() would silently drop malformed pairs
	if err != nil {
		problem(w, http.StatusBadRequest, "validation", "Invalid request", "Malformed query string.", nil)
		return
	}
	errs := map[string]string{}
	category := q.Get("category")
	if category != "" && !slugRE.MatchString(category) {
		errs["category"] = "must be a slug"
	}
	text := strings.TrimSpace(q.Get("q"))
	if len([]rune(text)) > 100 {
		errs["q"] = "at most 100 characters"
	}
	sort := q.Get("sort")
	if _, ok := orders[sort]; !ok && sort != "" {
		errs["sort"] = "must be one of relevance, newest, price_asc, price_desc, name"
	}
	low, err1 := intParam(q, "min_price", 0, math.MaxInt64)
	high, err2 := intParam(q, "max_price", 0, math.MaxInt64)
	page, err3 := intParam(q, "page", 1, 10000)
	size, err4 := intParam(q, "page_size", 1, 100)
	for _, err := range []error{err1, err2, err3, err4} {
		if err != nil {
			errs[err.Error()] = "out of range"
		}
	}
	if len(errs) > 0 {
		problem(w, http.StatusBadRequest, "validation", "Invalid request", "One or more fields are invalid.", map[string]any{"errors": errs})
		return
	}
	p, s := int64(1), int64(h.PageSize)
	if page != nil {
		p = *page
	}
	if size != nil {
		s = *size
	}

	// Every value is a bound parameter; the SQL text is fixed. The text search terms match the
	// expression indexes on product and category (commerce-core migration catalog/0004), so a
	// search is an index lookup rather than a scan of the catalog.
	where := `($1 = '' OR category_slug = $1)
		AND ($2::bigint IS NULL OR price_minor >= $2)
		AND ($3::bigint IS NULL OR price_minor <= $3)
		AND ($4 = '' OR to_tsvector('english', name || ' ' || description) @@ websearch_to_tsquery('english', $4)
		     OR to_tsvector('english', category_name) @@ websearch_to_tsquery('english', $4)
		     OR name ILIKE '%' || $5 || '%' OR lower(sku) = lower($4))`
	args := []any{category, low, high, text, likeEscaper.Replace(text)}
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()

	order := orders["newest"]
	switch {
	case sort != "" && sort != "relevance":
		order = orders[sort]
	case text != "":
		order = orders["relevance"]
	}
	// One round trip: the page and the total together (a window count over the filtered rows).
	rows, err := h.DB.Query(ctx, `SELECT `+columns+`, count(*) OVER () FROM catalog_public WHERE `+where+` ORDER BY `+order+` LIMIT $6 OFFSET $7`,
		append(args, s, (p-1)*s)...)
	if err != nil {
		h.fail(w, err)
		return
	}
	defer rows.Close()
	items := []product{}
	total := int64(-1)
	for rows.Next() {
		prod, err := h.scan(rows, &total)
		if err != nil {
			h.fail(w, err)
			return
		}
		items = append(items, prod)
	}
	if rows.Err() != nil {
		h.fail(w, rows.Err())
		return
	}
	if total < 0 { // a page past the end has no rows to carry the count: ask for it
		if err := h.DB.QueryRow(ctx, `SELECT count(*) FROM catalog_public WHERE `+where, args...).Scan(&total); err != nil {
			h.fail(w, err)
			return
		}
	}
	writeJSON(w, map[string]any{"items": items, "total": total, "page": p, "page_size": s, "pages": (total + s - 1) / s})
}

func (h *Handler) get(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if !uuidRE.MatchString(id) {
		problem(w, http.StatusNotFound, "not-found", "Not found", "Product not found.", nil)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()
	p, err := h.scan(h.DB.QueryRow(ctx, `SELECT `+columns+` FROM catalog_public WHERE id = $1::uuid`, strings.ToLower(id)))
	if errors.Is(err, pgx.ErrNoRows) {
		problem(w, http.StatusNotFound, "not-found", "Not found", "Product not found.", nil)
		return
	}
	if err != nil {
		h.fail(w, err)
		return
	}
	writeJSON(w, p)
}

type node struct {
	Slug     string  `json:"slug"`
	Name     string  `json:"name"`
	Children []*node `json:"children"`
}

func (h *Handler) categories(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()
	rows, err := h.DB.Query(ctx, `SELECT slug, name, coalesce(parent_slug, '') FROM catalog_category_public ORDER BY name`)
	if err != nil {
		h.fail(w, err)
		return
	}
	defer rows.Close()
	nodes, parents, order := map[string]*node{}, map[string]string{}, []string{}
	for rows.Next() {
		n := &node{Children: []*node{}}
		var parent string
		if err := rows.Scan(&n.Slug, &n.Name, &parent); err != nil {
			h.fail(w, err)
			return
		}
		nodes[n.Slug], parents[n.Slug] = n, parent
		order = append(order, n.Slug)
	}
	roots := []*node{}
	for _, slug := range order {
		if p, ok := nodes[parents[slug]]; ok && parents[slug] != slug {
			p.Children = append(p.Children, nodes[slug])
		} else {
			roots = append(roots, nodes[slug])
		}
	}
	writeJSON(w, map[string]any{"items": roots})
}

func (h *Handler) fail(w http.ResponseWriter, err error) {
	h.Log.Error("catalog query failed", "err", err)
	problem(w, http.StatusServiceUnavailable, "unavailable", "Catalogue unavailable", "Try again shortly.", nil)
}
