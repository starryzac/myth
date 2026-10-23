package httpapi

import (
	"net/http"

	"github.com/go-chi/chi/v5"
	"zhiyu.local/zhiyu-go/internal/app"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

func nativeBillRoutes(r chi.Router, s *app.Service) {
	principal := func(req *http.Request) app.Principal { return req.Context().Value(principalKey{}).(app.Principal) }
	r.Get("/api/v2/bill-sources", func(w http.ResponseWriter, req *http.Request) {
		value, e := s.NativeBillSourceInventory(req.Context(), principal(req))
		if e != nil {
			fail(w, app.Code(e))
			return
		}
		writeJSON(w, 200, value)
	})
	r.Post("/api/v2/bill-imports", func(w http.ResponseWriter, req *http.Request) {
		var in app.NativeBillImportRequest
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		value, e := s.ImportConfiguredNativeBills(req.Context(), principal(req), req.Header.Get("Idempotency-Key"), in)
		if e != nil {
			fail(w, app.Code(e))
			return
		}
		writeJSON(w, 202, value)
	})
	r.Get("/api/v2/bill-import-commands/{key}", func(w http.ResponseWriter, req *http.Request) {
		value, e := s.NativeBillImportByKey(req.Context(), principal(req), chi.URLParam(req, "key"))
		if e != nil {
			fail(w, app.Code(e))
			return
		}
		writeJSON(w, 200, value)
	})
	r.Get("/api/v2/bill-imports/{capture_id}", func(w http.ResponseWriter, req *http.Request) {
		id, e := funds.ParseID(chi.URLParam(req, "capture_id"))
		if e != nil {
			fail(w, "INVALID_ID")
			return
		}
		value, e := s.NativeBillIntake(req.Context(), principal(req), id)
		if e != nil {
			fail(w, app.Code(e))
			return
		}
		writeJSON(w, 200, value)
	})
	r.Post("/api/v2/bill-imports/{capture_id}/resume", func(w http.ResponseWriter, req *http.Request) {
		id, e := funds.ParseID(chi.URLParam(req, "capture_id"))
		if e != nil {
			fail(w, "INVALID_ID")
			return
		}
		var in struct{}
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		value, e := s.ResumeNativeBillImport(req.Context(), principal(req), id)
		if e != nil {
			fail(w, app.Code(e))
			return
		}
		writeJSON(w, 202, value)
	})
}
