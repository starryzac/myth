package httpapi

import (
	"net/http"
	"strings"

	"github.com/go-chi/chi/v5"
	"zhiyu.local/zhiyu-go/internal/app"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

func auditRoutes(r chi.Router, s *app.Service) {
	principal := func(req *http.Request) app.Principal { return req.Context().Value(principalKey{}).(app.Principal) }
	r.Post("/api/v2/audits", func(w http.ResponseWriter, req *http.Request) {
		var in app.AuditRequest
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		v, err := s.StartAudit(req.Context(), principal(req), req.Header.Get("Idempotency-Key"), in)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 202, v)
	})
	r.Get("/api/v2/audits/{audit_id}", func(w http.ResponseWriter, req *http.Request) {
		id, err := funds.ParseID(chi.URLParam(req, "audit_id"))
		if err != nil {
			fail(w, "INVALID_ID")
			return
		}
		v, err := s.Audit(req.Context(), principal(req), id)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 200, v)
	})
	r.Get("/api/v2/audits/{audit_id}/artifacts/{kind}", func(w http.ResponseWriter, req *http.Request) {
		id, err := funds.ParseID(chi.URLParam(req, "audit_id"))
		if err != nil {
			fail(w, "INVALID_ID")
			return
		}
		kind := strings.ToUpper(chi.URLParam(req, "kind"))
		if kind != "CAPTURE" && kind != "RESULT" {
			fail(w, "INVALID_COMMAND")
			return
		}
		v, err := s.AuditArtifact(req.Context(), principal(req), id, kind)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		w.Header().Set("Content-Disposition", `attachment; filename="audit-`+id.String()+`-`+strings.ToLower(kind)+`.json"`)
		w.Header().Set("X-Artifact-SHA256", v.OriginalHash)
		w.Header().Set("X-Captured-Manifest-SHA256", v.ManifestDigest)
		w.WriteHeader(200)
		_, _ = w.Write(v.Original)
	})
	r.Post("/api/v2/audit-anchors", func(w http.ResponseWriter, req *http.Request) {
		var in app.AuditAnchorRequest
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		v, err := s.ImportAuditAnchor(req.Context(), principal(req), in)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 201, v)
	})
}
