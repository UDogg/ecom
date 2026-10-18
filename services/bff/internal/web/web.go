// Package web serves the storefront: the built single-page app, from memory.
//
// Every file is read once at start, with its precompressed .br and .gz
// siblings (made at build time, so no CPU is spent compressing per request),
// a strong ETag and a content type. Fingerprinted files under /assets/ are
// cached by browsers for a year; index.html is revalidated every time, so a
// deploy is visible on the next navigation. Paths that are not files get
// index.html (client-side routing), except anything that looks like a file.
package web

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io/fs"
	"mime"
	"net/http"
	"path"
	"strings"
)

// CSP for the app: its own scripts, styles, images and API, nothing else, no inline code.
const CSP = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; " +
	"connect-src 'self'; manifest-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"

const maxFiles = 2000

type variant struct {
	body     []byte
	encoding string // "", "br", "gzip"
}

type file struct {
	variants     []variant // best first
	contentType  string
	etag         string
	cacheControl string
}

// Site is the loaded app.
type Site struct {
	files map[string]*file
	index *file
}

// Load reads the app from root (a directory with index.html).
func Load(root fs.FS) (*Site, error) {
	s := &Site{files: map[string]*file{}}
	err := fs.WalkDir(root, ".", func(p string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() || strings.HasSuffix(p, ".br") || strings.HasSuffix(p, ".gz") {
			return err
		}
		if len(s.files) >= maxFiles {
			return fmt.Errorf("web: more than %d files", maxFiles)
		}
		raw, err := fs.ReadFile(root, p)
		if err != nil {
			return err
		}
		sum := sha256.Sum256(raw)
		f := &file{
			contentType:  contentType(p),
			etag:         `"` + hex.EncodeToString(sum[:12]) + `"`,
			cacheControl: "no-cache",
		}
		if strings.HasPrefix(p, "assets/") {
			f.cacheControl = "public, max-age=31536000, immutable" // the name changes with the content
		}
		for _, enc := range []struct{ ext, name string }{{".br", "br"}, {".gz", "gzip"}} {
			if b, err := fs.ReadFile(root, p+enc.ext); err == nil && len(b) < len(raw) {
				f.variants = append(f.variants, variant{b, enc.name})
			}
		}
		f.variants = append(f.variants, variant{raw, ""})
		s.files["/"+p] = f
		return nil
	})
	if err != nil {
		return nil, err
	}
	if s.index = s.files["/index.html"]; s.index == nil {
		return nil, fmt.Errorf("web: no index.html")
	}
	return s, nil
}

func contentType(p string) string {
	switch path.Ext(p) {
	case ".js", ".mjs":
		return "text/javascript; charset=utf-8"
	case ".webmanifest":
		return "application/manifest+json"
	case ".html":
		return "text/html; charset=utf-8"
	}
	if t := mime.TypeByExtension(path.Ext(p)); t != "" {
		return t
	}
	return "application/octet-stream"
}

// Files is the number of files served.
func (s *Site) Files() int { return len(s.files) }

// ServeHTTP answers GET and HEAD.
func (s *Site) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		w.Header().Set("Allow", "GET, HEAD")
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	f := s.files[r.URL.Path]
	if f == nil {
		if path.Ext(r.URL.Path) != "" || strings.HasPrefix(r.URL.Path, "/assets/") {
			http.NotFound(w, r) // a missing file, not a page: never answer with HTML
			return
		}
		f = s.index
	}
	h := w.Header()
	h.Set("Content-Type", f.contentType)
	h.Set("Cache-Control", f.cacheControl)
	h.Set("ETag", f.etag)
	h.Add("Vary", "Accept-Encoding")
	if f.contentType == "text/html; charset=utf-8" {
		h.Set("Content-Security-Policy", CSP)
	}
	if match := r.Header.Get("If-None-Match"); match != "" && strings.Contains(match, f.etag) {
		w.WriteHeader(http.StatusNotModified)
		return
	}
	v := pick(f.variants, r.Header.Get("Accept-Encoding"))
	if v.encoding != "" {
		h.Set("Content-Encoding", v.encoding)
	}
	h.Set("Content-Length", fmt.Sprint(len(v.body)))
	if r.Method == http.MethodHead {
		return
	}
	w.Write(v.body)
}

func pick(variants []variant, accept string) variant {
	for _, v := range variants {
		if v.encoding == "" || accepts(accept, v.encoding) {
			return v
		}
	}
	return variants[len(variants)-1]
}

// accepts reports whether an Accept-Encoding header allows enc (q=0 refuses it).
func accepts(header, enc string) bool {
	for _, part := range strings.Split(header, ",") {
		name, params, _ := strings.Cut(strings.TrimSpace(part), ";")
		if strings.EqualFold(strings.TrimSpace(name), enc) {
			q := strings.ReplaceAll(params, " ", "")
			return q != "q=0" && q != "q=0.0" && q != "q=0.00" && q != "q=0.000"
		}
	}
	return false
}
