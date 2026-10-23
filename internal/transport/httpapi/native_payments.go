package httpapi

import (
	"github.com/go-chi/chi/v5"
	"net/http"
	"zhiyu.local/zhiyu-go/internal/app"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

func nativePaymentRoutes(r chi.Router, s *app.Service) {
	principal := func(req *http.Request) app.Principal { return req.Context().Value(principalKey{}).(app.Principal) }
	r.Post("/api/v2/payment-previews", func(w http.ResponseWriter, req *http.Request) {
		var in app.NativePaymentRequest
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		value, e := s.PreviewNativePayment(req.Context(), principal(req), in)
		if e != nil {
			fail(w, app.Code(e))
			return
		}
		writeJSON(w, 201, value)
	})
	r.Get("/api/v2/payment-previews/{preview_id}", func(w http.ResponseWriter, req *http.Request) {
		id, e := funds.ParseID(chi.URLParam(req, "preview_id"))
		if e != nil {
			fail(w, "INVALID_ID")
			return
		}
		value, e := s.NativePaymentPreview(req.Context(), principal(req), id)
		if e != nil {
			fail(w, app.Code(e))
			return
		}
		writeJSON(w, 200, value)
	})
	r.Post("/api/v2/payments", func(w http.ResponseWriter, req *http.Request) {
		var in app.NativePaymentCommand
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		value, e := s.AcceptNativePayment(req.Context(), principal(req), req.Header.Get("Idempotency-Key"), in)
		if e != nil {
			fail(w, app.Code(e))
			return
		}
		writeJSON(w, 202, value)
	})
	r.Get("/api/v2/payments/{operation_id}", func(w http.ResponseWriter, req *http.Request) {
		id, e := funds.ParseID(chi.URLParam(req, "operation_id"))
		if e != nil {
			fail(w, "INVALID_ID")
			return
		}
		value, e := s.NativePaymentOperation(req.Context(), principal(req), id)
		if e != nil {
			fail(w, app.Code(e))
			return
		}
		writeJSON(w, 200, value)
	})
	r.Get("/api/v2/payment-commands/{key}", func(w http.ResponseWriter, req *http.Request) {
		value, e := s.NativePaymentByKey(req.Context(), principal(req), chi.URLParam(req, "key"))
		if e != nil {
			fail(w, app.Code(e))
			return
		}
		writeJSON(w, 200, value)
	})
}
