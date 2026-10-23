package httpapi

import (
	"net/http"

	"github.com/go-chi/chi/v5"
	"zhiyu.local/zhiyu-go/internal/app"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

func nativeRoutes(r chi.Router, s *app.Service) {
	nativeGoalTransferRoutes(r, s)
	nativePaymentRoutes(r, s)
	nativeAssetRoutes(r, s)
	historyRoutes(r, s)
	nativeGrantRoutes(r, s)
	nativeBillRoutes(r, s)
	forecastHistoryRoutes(r, s)
	principal := func(req *http.Request) app.Principal { return req.Context().Value(principalKey{}).(app.Principal) }
	r.Get("/api/v2/activities/{event_id}/original", func(w http.ResponseWriter, req *http.Request) {
		id, err := funds.ParseID(chi.URLParam(req, "event_id"))
		if err != nil {
			fail(w, "INVALID_ID")
			return
		}
		value, err := s.NativeActivityOriginal(req.Context(), principal(req), id)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 200, value)
	})
	r.Get("/api/v2/financial-options", func(w http.ResponseWriter, req *http.Request) {
		value, e := s.NativeOptions(req.Context(), principal(req))
		if e != nil {
			fail(w, app.Code(e))
			return
		}
		writeJSON(w, 200, value)
	})
	r.Post("/api/v2/income-sync", func(w http.ResponseWriter, req *http.Request) {
		var in struct{}
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		if s.NativeIncomeSources == nil {
			fail(w, "BANK_UNAVAILABLE")
			return
		}
		value, err := s.ImportNativeIncome(req.Context(), principal(req), *s.NativeIncomeSources)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 200, value)
	})
	r.Post("/api/v2/bridge-previews", func(w http.ResponseWriter, req *http.Request) {
		var in app.NativeBridgePreviewRequest
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		value, err := s.PreviewNativeBridge(req.Context(), principal(req), in)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 201, value)
	})
	r.Get("/api/v2/bridge-previews/{preview_id}", func(w http.ResponseWriter, req *http.Request) {
		id, err := funds.ParseID(chi.URLParam(req, "preview_id"))
		if err != nil {
			fail(w, "INVALID_ID")
			return
		}
		value, err := s.NativeBridgePreview(req.Context(), principal(req), id)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 200, value)
	})
	r.Post("/api/v2/bridge-operations", func(w http.ResponseWriter, req *http.Request) {
		var in app.NativeBridgeCommand
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		value, err := s.AcceptNativeBridge(req.Context(), principal(req), req.Header.Get("Idempotency-Key"), in)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 202, value)
	})
	r.Get("/api/v2/bridge-operations/{group_id}", func(w http.ResponseWriter, req *http.Request) {
		id, err := funds.ParseID(chi.URLParam(req, "group_id"))
		if err != nil {
			fail(w, "INVALID_ID")
			return
		}
		value, err := s.NativeBridgeGroup(req.Context(), principal(req), id)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 200, value)
	})
	r.Get("/api/v2/bridge-commands/{key}", func(w http.ResponseWriter, req *http.Request) {
		value, err := s.NativeBridgeGroupByKey(req.Context(), principal(req), chi.URLParam(req, "key"))
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 200, value)
	})
	r.Post("/api/v2/preference-analyses", func(w http.ResponseWriter, req *http.Request) {
		var in app.NativePriorityRequest
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		value, err := s.AnalyzeNativePriorities(req.Context(), principal(req), req.Header.Get("Idempotency-Key"), in)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 201, value)
	})
	r.Get("/api/v2/preference-analyses/{analysis_id}", func(w http.ResponseWriter, req *http.Request) {
		id, err := funds.ParseID(chi.URLParam(req, "analysis_id"))
		if err != nil {
			fail(w, "INVALID_ID")
			return
		}
		value, err := s.NativePriorityAnalysis(req.Context(), principal(req), id)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 200, value)
	})
	r.Post("/api/v2/preference-answers", func(w http.ResponseWriter, req *http.Request) {
		var in app.NativePriorityAnswerRequest
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		value, err := s.AnswerNativePriority(req.Context(), principal(req), req.Header.Get("Idempotency-Key"), in)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 201, value)
	})
	r.Post("/api/v2/joint-plans", func(w http.ResponseWriter, req *http.Request) {
		var in app.JointPlanRequest
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		plan, err := s.JointPreview(req.Context(), principal(req), in)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		hash, err := app.NativeJointReviewHash(plan)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 201, app.NativeJointView{Plan: plan, ReviewedBundleHash: hash})
	})
	r.Get("/api/v2/joint-plans/{plan_id}", func(w http.ResponseWriter, req *http.Request) {
		id, err := funds.ParseID(chi.URLParam(req, "plan_id"))
		if err != nil {
			fail(w, "INVALID_ID")
			return
		}
		plan, err := s.JointPlan(req.Context(), principal(req), id)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		hash, err := app.NativeJointReviewHash(plan)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 200, app.NativeJointView{Plan: plan, ReviewedBundleHash: hash})
	})
	r.Post("/api/v2/joint-operations", func(w http.ResponseWriter, req *http.Request) {
		var in app.NativeJointCommand
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		v, err := s.AcceptNativeJoint(req.Context(), principal(req), req.Header.Get("Idempotency-Key"), in)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 202, v)
	})
	r.Get("/api/v2/joint-commands/{key}", func(w http.ResponseWriter, req *http.Request) {
		v, err := s.NativeGroupByKey(req.Context(), principal(req), chi.URLParam(req, "key"))
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 200, v)
	})
	r.Get("/api/v2/joint-operations/{group_id}", func(w http.ResponseWriter, req *http.Request) {
		id, err := funds.ParseID(chi.URLParam(req, "group_id"))
		if err != nil {
			fail(w, "INVALID_ID")
			return
		}
		v, err := s.NativeGroup(req.Context(), principal(req), id)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 200, v)
	})
	r.Post("/api/v2/joint-operations/{group_id}/resume", func(w http.ResponseWriter, req *http.Request) {
		var empty struct{}
		if body(req, &empty) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		id, err := funds.ParseID(chi.URLParam(req, "group_id"))
		if err != nil {
			fail(w, "INVALID_ID")
			return
		}
		value, err := s.ResumeNativeGroup(req.Context(), principal(req), id)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 200, value)
	})
}
