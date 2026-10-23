package deepseek

import (
	"encoding/json"
	"sort"
	"strings"

	"zhiyu.local/zhiyu-go/internal/agent/domain"
)

// ResponseRejection exposes only finite diagnostic labels. It deliberately
// retains no response body, message content, header values or configuration.
// errors.Is(err, ErrResponse) remains the stable caller contract.
type ResponseRejection struct {
	reason        string
	status        int
	shape         ReplyShape
	input, output int64
}

func (e *ResponseRejection) Error() string           { return "DEEPSEEK_INVALID_RESPONSE:" + e.reason }
func (e *ResponseRejection) Unwrap() error           { return ErrResponse }
func (e *ResponseRejection) RejectionReason() string { return e.reason }
func (e *ResponseRejection) HTTPStatus() int         { return e.status }
func (e *ResponseRejection) SafeReplyShape() ReplyShape {
	v := e.shape
	v.TopLevelKeys = append([]string(nil), v.TopLevelKeys...)
	v.ToolKeys = append([]string(nil), v.ToolKeys...)
	v.ArgumentKeys = append([]string(nil), v.ArgumentKeys...)
	v.ConfigurationKeys = append([]string(nil), v.ConfigurationKeys...)
	return v
}
func (e *ResponseRejection) ValidatedUsage() (int64, int64) { return e.input, e.output }

type ReplyShape struct {
	Tool              string   `json:"tool"`
	TopLevelKeys      []string `json:"top_level_keys"`
	ToolKeys          []string `json:"tool_keys"`
	ArgumentKeys      []string `json:"argument_keys"`
	ConfigurationKeys []string `json:"configuration_keys"`
}

func rejectResponse(reason string, status int) error {
	return &ResponseRejection{reason: reason, status: status}
}
func replySchemaRejection(raw []byte, input, output int64) error {
	return &ResponseRejection{reason: "REPLY_SCHEMA", status: 200, shape: safeReplyShape(raw), input: input, output: output}
}
func safeReplyShape(raw []byte) ReplyShape {
	out := ReplyShape{Tool: "NO_REGISTERED_TOOL"}
	var top map[string]json.RawMessage
	if len(raw) > domain.MaxJSONBytes || json.Unmarshal(raw, &top) != nil || top == nil {
		return out
	}
	out.TopLevelKeys = safeKeys(top)
	var tool struct {
		Name      string          `json:"name"`
		Arguments json.RawMessage `json:"arguments"`
	}
	var object map[string]json.RawMessage
	if json.Unmarshal(top["tool"], &tool) == nil {
		for _, registered := range domain.Registry() {
			if tool.Name == registered.Name {
				out.Tool = registered.Name
			}
		}
		if json.Unmarshal(top["tool"], &object) == nil {
			out.ToolKeys = safeKeys(object)
		}
		var args map[string]json.RawMessage
		if json.Unmarshal(tool.Arguments, &args) == nil {
			out.ArgumentKeys = safeKeys(args)
			var config map[string]json.RawMessage
			if json.Unmarshal(args["configuration"], &config) == nil {
				out.ConfigurationKeys = safeKeys(config)
			}
		}
	}
	return out
}
func safeKeys(object map[string]json.RawMessage) []string {
	keys := []string{}
	for key := range object {
		label := "UNREGISTERED_FIELD"
		switch key {
		case "tool", "done", "name", "arguments", "mode", "text", "explanation", "candidate", "template_name", "configuration", "goal_id", "policy_id", "operation_id", "reference", "kind", "id", "digest", "timezone", "cost", "cost_consent", "fee_cents", "loss_cents", "amount_cents", "type", "confirmed", "confirmation", "confirmation_id", "grant_id", "bank_authority", "authority", "user_id", "environment_id", "complete", "safe_cents", "source_hash", "version", "valid_from", "valid_until":
			label = strings.ToUpper(key)
		}
		keys = append(keys, label)
	}
	sort.Strings(keys)
	return keys
}
