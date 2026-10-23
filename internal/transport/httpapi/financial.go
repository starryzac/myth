package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"mime"
	"net/http"
	"strconv"
	"strings"

	"github.com/go-chi/chi/v5"
	"zhiyu.local/zhiyu-go/internal/app"
	"zhiyu.local/zhiyu-go/internal/audit"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

type principalKey struct{}

func body(req *http.Request, target any) error {
	media, _, err := mime.ParseMediaType(req.Header.Get("Content-Type"))
	if err != nil || media != "application/json" {
		return audit.ErrInvalid
	}
	raw, err := io.ReadAll(req.Body)
	if err != nil {
		return err
	}
	if _, err = audit.ParseRawObject(raw); err != nil {
		return err
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if err = d.Decode(target); err != nil {
		return err
	}
	return nil
}
func fail(w http.ResponseWriter, code string) {
	status, retry, message := http.StatusConflict, "AFTER_REFRESH", "当前条件发生变化，请刷新后重新核对"
	switch code {
	case "UNAUTHENTICATED":
		status, retry, message = 401, "AFTER_LOGIN", "请重新登录"
	case "FORBIDDEN":
		status, retry, message = 403, "NEVER", "当前会话无权执行此操作"
	case "INVALID_JSON", "INVALID_ID", "INVALID_COMMAND", "INVALID_IDEMPOTENCY_KEY", "INVALID_POLICY", "INVALID_GRANT", "INVALID_CURSOR":
		status, retry, message = 400, "NEVER", "请求格式或配置不符合约定"
	case "NOT_FOUND":
		status, retry, message = 404, "NEVER", "未找到当前用户的记录"
	case "IDEMPOTENCY_CONFLICT":
		message = "原操作键已绑定其他请求，请继续核对原操作"
	case "GOAL_OPERATION_PENDING":
		message = "这个目标有尚未核对完的原操作，请先等待或查询原操作"
	case "PAYMENT_OPERATION_PENDING":
		message = "这笔应付有尚未核对完的原付款，请先查询原付款结果"
	case "PAYMENT_ALREADY_PAID":
		message = "这笔应付已付清，请重新读取银行与债务余额"
	case "PAYMENT_BANK_ORIGINAL_REQUIRED", "PAYMENT_BILL_ORIGINAL_REQUIRED":
		message = "尚未取得这笔应付的完整银行原件，请补齐资料后再预览"
	case "PAYMENT_PREPARATION_NOT_OPEN":
		message = "这笔付款尚未进入规则约定的准备时间"
	case "PAYMENT_AMOUNT_OUTSIDE_RULE", "PAYMENT_FINAL_TOTAL_OUTSIDE_RULE":
		message = "付款金额超出现行规则或实际未付余额，请重新核对"
	case "PAYMENT_BANK_SOURCE_CHANGED", "PAYMENT_BANK_RESERVATION_PENDING":
		message = "银行的应付余额或预留已变化，请先核对原付款"
	case "ASSET_POLICY_NOT_ACTIVE":
		message = "这项资产规则当前未生效，请先核对规则状态"
	case "ASSET_NO_SAFE_PURCHASE":
		message = "当前没有符合保护要求的可购金额"
	case "ASSET_POSITION_UNAVAILABLE":
		message = "这笔持仓当前无法用于新操作，请先核对原操作与持仓状态"
	case "BANK_UNAVAILABLE", "OPERATION_UNKNOWN":
		status, retry, message = 503, "ORIGINAL_KEY_ONLY", "结果尚未核实，请保留并查询原操作"
	case "INTERNAL_ERROR":
		status, retry, message = 503, "ORIGINAL_KEY_ONLY", "暂未完成请求，请保留原操作键并稍后核对"
	case "EVIDENCE_INVALID":
		status, retry, message = 503, "AFTER_REPAIR", "原件核验未通过，当前操作已停止"
	case "CAPACITY_EXCEEDED":
		status, retry, message = 422, "NEVER", "请求超出当前已核验的容量范围"
	case "AUDIT_NOT_CONFIGURED":
		status, retry, message = 503, "AFTER_CONFIGURATION", "审计来源与信任配置尚未完成"
	case "AUDIT_CAPTURE_LIMIT":
		status, retry, message = 422, "NEVER", "当前审计范围超过已核验容量，未生成完整审计结论"
	case "UNSUPPORTED_AUDIT_SCOPE":
		status, retry, message = 422, "AFTER_UPGRADE", "当前资料包含尚未纳入此版本完整核验的记录，未生成完整审计结论"
	case "AUDIT_ARTIFACT_INVALID", "AUDIT_ANCHOR_REJECTED":
		status, retry, message = 409, "AFTER_REPAIR", "审计原件或可信锚核验未通过"
	case "INVALID_GOAL", "INVALID_TEMPLATE_CONFIGURATION", "INVALID_TIMEZONE", "TEMPLATE_REVIEW_REQUIRED":
		status, retry, message = 400, "NEVER", "目标或规则配置不符合约定，请核对填写内容"
	case "GOAL_TARGET_BELOW_OWNED":
		message = "目标金额不能小于已经归属的本金"
	case "GOAL_TARGET_CHANGE_REQUIRES_POLICY_VERSION":
		message = "请在这个目标的现行规则中预览并确认新的目标金额"
	case "NATIVE_GOAL_AUTHORIZATION_REQUIRED":
		message = "这个目标已使用新的目标规则，请按现行规则重新预览并确认安排"
	case "GOAL_ACCOUNT_CHANGE_REQUIRES_TRANSFER":
		message = "目标账户变更需要独立的资金调拨安排"
	case "TEMPLATE_FACTS_INCOMPLETE", "TEMPLATE_FACTS_INVALID":
		status, retry, message = 422, "AFTER_REPAIR", "当前事实尚不足以完整计算这项规则，请先补齐资料"
	case "INVALID_QUESTION_DOMAIN":
		status, retry, message = 400, "NEVER", "请只比较本人目标的明确金额或优先级选项，并保留现行规则的上下限"
	case "ORIGINAL_QUESTION_STALE":
		status, retry, message = 409, "AFTER_REFRESH", "原问题已回答、过期或事实已变化，请重新读取当前方案并比较"
	case "QUESTION_COMPUTATION_INCOMPLETE":
		status, retry, message = 422, "AFTER_REFRESH", "尚未完成声明范围内的全部比较，未生成稳定性或必要追问结论"
	case "PLAN_EXPIRED", "REVIEW_MISMATCH", "JOINT_NOT_EXECUTABLE", "JOINT_BRIDGE_REQUIRED":
		status, retry, message = 409, "AFTER_REFRESH", "当前方案需要重新预览或完成明确的到账步骤，请重新核对完整安排"
	}
	writeJSON(w, status, Error{Code: code, Message: message, RequestID: w.Header().Get("X-Request-ID"), RetryClass: retry, Details: map[string]string{}})
}
func financialRoutes(r chi.Router, s *app.Service, origin string, options *AgentOptions) {
	r.Group(func(r chi.Router) {
		r.Use(func(next http.Handler) http.Handler {
			return http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
				given := req.Header.Get("Origin")
				// Browser writes, including login, require this exact configured
				// local origin. Tokens never derive from request user/environment IDs.
				if (given != "" && given != origin) || (req.Method != "GET" && given != origin) || origin == "" {
					fail(w, "FORBIDDEN")
					return
				}
				if given == origin {
					w.Header().Set("Access-Control-Allow-Origin", origin)
					w.Header().Set("Vary", "Origin")
					w.Header().Set("Access-Control-Allow-Headers", "Authorization, Content-Type, X-CSRF-Token, Idempotency-Key")
					w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
				}
				if req.Method == "OPTIONS" {
					w.WriteHeader(http.StatusNoContent)
					return
				}
				next.ServeHTTP(w, req)
			})
		})
		r.Options("/api/v2/*", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) })
		r.Post("/api/v2/session", func(w http.ResponseWriter, req *http.Request) {
			var in struct {
				Secret string `json:"secret"`
			}
			if body(req, &in) != nil {
				fail(w, "INVALID_JSON")
				return
			}
			v, err := s.Login(req.Context(), in.Secret)
			if err != nil {
				fail(w, app.Code(err))
				return
			}
			writeJSON(w, 200, v)
		})
		r.Group(func(r chi.Router) {
			r.Use(func(next http.Handler) http.Handler {
				return http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
					auth := req.Header.Get("Authorization")
					if !strings.HasPrefix(auth, "Bearer ") || strings.Count(auth, " ") != 1 {
						fail(w, "UNAUTHENTICATED")
						return
					}
					p, err := s.Authenticate(req.Context(), strings.TrimPrefix(auth, "Bearer "), req.Header.Get("X-CSRF-Token"), req.Method != "GET")
					if err != nil {
						fail(w, app.Code(err))
						return
					}
					next.ServeHTTP(w, req.WithContext(context.WithValue(req.Context(), principalKey{}, p)))
				})
			})
			principal := func(req *http.Request) app.Principal { return req.Context().Value(principalKey{}).(app.Principal) }
			agentRoutes(r, s, options)
			auditRoutes(r, s)
			templateRoutes(r, s)
			nativeRoutes(r, s)
			r.Post("/api/v2/projections/rebuild", func(w http.ResponseWriter, req *http.Request) {
				var empty struct{}
				if body(req, &empty) != nil {
					fail(w, "INVALID_JSON")
					return
				}
				v, err := s.RebuildProjection(req.Context(), principal(req))
				if err != nil {
					fail(w, app.Code(err))
					return
				}
				writeJSON(w, 200, v)
			})
			r.Get("/api/v2/activities", func(w http.ResponseWriter, req *http.Request) {
				query := req.URL.Query()
				for key, values := range query {
					if (key != "limit" && key != "cursor") || len(values) != 1 {
						fail(w, "INVALID_CURSOR")
						return
					}
				}
				limit := 20
				if raw := query.Get("limit"); raw != "" {
					var err error
					limit, err = strconv.Atoi(raw)
					if err != nil {
						fail(w, "INVALID_CURSOR")
						return
					}
				}
				v, err := s.Activities(req.Context(), principal(req), limit, query.Get("cursor"))
				if err != nil {
					fail(w, app.Code(err))
					return
				}
				writeJSON(w, 200, v)
			})
			r.Get("/api/v2/dashboard", func(w http.ResponseWriter, req *http.Request) {
				v, err := s.Dashboard(req.Context(), principal(req))
				if err != nil {
					fail(w, app.Code(err))
					return
				}
				writeJSON(w, 200, v)
			})
			r.Post("/api/v2/policies", func(w http.ResponseWriter, req *http.Request) {
				var in app.PolicyRequest
				if body(req, &in) != nil {
					fail(w, "INVALID_JSON")
					return
				}
				v, err := s.ConfirmPolicy(req.Context(), principal(req), in)
				if err != nil {
					fail(w, app.Code(err))
					return
				}
				writeJSON(w, 201, v)
			})
			r.Post("/api/v2/grants", func(w http.ResponseWriter, req *http.Request) {
				var in app.GrantRequest
				if body(req, &in) != nil {
					fail(w, "INVALID_JSON")
					return
				}
				v, err := s.ConfirmGrant(req.Context(), principal(req), in)
				if err != nil {
					fail(w, app.Code(err))
					return
				}
				writeJSON(w, 201, v)
			})
			r.Post("/api/v2/grants/{id}/revoke", func(w http.ResponseWriter, req *http.Request) {
				id, err := funds.ParseID(chi.URLParam(req, "id"))
				if err != nil {
					fail(w, "INVALID_ID")
					return
				}
				var in struct{}
				if body(req, &in) != nil {
					fail(w, "INVALID_JSON")
					return
				}
				if err = s.RevokeGrant(req.Context(), principal(req), id); err != nil {
					fail(w, app.Code(err))
					return
				}
				w.WriteHeader(204)
			})
			r.Post("/api/v2/plans", func(w http.ResponseWriter, req *http.Request) {
				var in app.PlanRequest
				if body(req, &in) != nil {
					fail(w, "INVALID_JSON")
					return
				}
				v, err := s.Plan(req.Context(), principal(req), in)
				if err != nil {
					fail(w, app.Code(err))
					return
				}
				writeJSON(w, 201, v)
			})
			r.Post("/api/v2/operations", func(w http.ResponseWriter, req *http.Request) {
				var in app.OperationRequest
				if body(req, &in) != nil {
					fail(w, "INVALID_JSON")
					return
				}
				v, err := s.Accept(req.Context(), principal(req), req.Header.Get("Idempotency-Key"), in)
				if err != nil {
					fail(w, app.Code(err))
					return
				}
				writeJSON(w, 202, v)
			})
			r.Get("/api/v2/operations/{id}", func(w http.ResponseWriter, req *http.Request) {
				id, err := funds.ParseID(chi.URLParam(req, "id"))
				if err != nil {
					fail(w, "INVALID_ID")
					return
				}
				v, err := s.Operation(req.Context(), principal(req), id)
				if err != nil {
					fail(w, app.Code(err))
					return
				}
				writeJSON(w, 200, v)
			})
			r.Get("/api/v2/command-results/{key}", func(w http.ResponseWriter, req *http.Request) {
				v, err := s.OperationByKey(req.Context(), principal(req), chi.URLParam(req, "key"))
				if err != nil {
					fail(w, app.Code(err))
					return
				}
				writeJSON(w, 200, v)
			})
		})
	})
}
