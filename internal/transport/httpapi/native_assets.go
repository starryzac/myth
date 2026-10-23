package httpapi

import (
	"github.com/go-chi/chi/v5"
	"net/http"
	"zhiyu.local/zhiyu-go/internal/app"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

func nativeAssetRoutes(r chi.Router, s *app.Service) {
	principal := func(req *http.Request) app.Principal { return req.Context().Value(principalKey{}).(app.Principal) }
	r.Post("/api/v2/asset-previews", func(w http.ResponseWriter, req *http.Request) {
		var in app.NativeAssetRequest
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		value, e := s.PreviewNativeAsset(req.Context(), principal(req), in)
		if e != nil {
			fail(w, app.Code(e))
			return
		}
		writeJSON(w, 201, value)
	})
	r.Get("/api/v2/asset-previews/{kind}/{preview_id}", func(w http.ResponseWriter, req *http.Request) {
		id, e := funds.ParseID(chi.URLParam(req, "preview_id"))
		if e != nil {
			fail(w, "INVALID_ID")
			return
		}
		value, e := s.NativeAssetPreview(req.Context(), principal(req), id, bankv2.Kind(chi.URLParam(req, "kind")))
		if e != nil {
			fail(w, app.Code(e))
			return
		}
		writeJSON(w, 200, value)
	})
	r.Post("/api/v2/assets", func(w http.ResponseWriter, req *http.Request) {
		var in app.NativeAssetCommand
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		value, e := s.AcceptNativeAsset(req.Context(), principal(req), req.Header.Get("Idempotency-Key"), in)
		if e != nil {
			fail(w, app.Code(e))
			return
		}
		writeJSON(w, 202, value)
	})
	r.Get("/api/v2/assets/{operation_id}", func(w http.ResponseWriter, req *http.Request) {
		id, e := funds.ParseID(chi.URLParam(req, "operation_id"))
		if e != nil {
			fail(w, "INVALID_ID")
			return
		}
		value, e := s.NativeAssetOperation(req.Context(), principal(req), id)
		if e != nil {
			fail(w, app.Code(e))
			return
		}
		writeJSON(w, 200, value)
	})
	r.Get("/api/v2/asset-commands/{kind}/{key}", func(w http.ResponseWriter, req *http.Request) {
		value, e := s.NativeAssetByKey(req.Context(), principal(req), bankv2.Kind(chi.URLParam(req, "kind")), chi.URLParam(req, "key"))
		if e != nil {
			fail(w, app.Code(e))
			return
		}
		writeJSON(w, 200, value)
	})
}
