package app

import (
	"encoding/hex"
	"encoding/json"
	"strings"

	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	execution "zhiyu.local/zhiyu-go/internal/execution/domain"
)

// Recovery runs are environment-wide operator originals. A USER export may
// retain the exact run only when every declared subject and nested bank object
// belongs to that USER. Foreign runs are omitted and make the capture INCOMPLETE;
// the collector never redacts/re-encodes a run and calls it an original.
func nativeAuditRecoveryRunOwnedV3(row []byte, p Principal, namespace string) bool {
	var fields struct {
		Original string `json:"original"`
	}
	if json.Unmarshal(row, &fields) != nil || !strings.HasPrefix(fields.Original, `\x`) {
		return false
	}
	raw, err := hex.DecodeString(fields.Original[2:])
	var run RecoveryRun
	if err != nil || auditJSON(raw, &run) != nil || run.Protocol != RecoveryProtocol+"/run" || run.EnvironmentID != p.EnvironmentID || run.Namespace != namespace {
		return false
	}
	for _, user := range run.Users {
		if user.ID != p.UserID {
			return false
		}
	}
	for _, id := range run.Subjects.UserIDs {
		if id != p.UserID.String() {
			return false
		}
	}
	for _, state := range run.CurrentStates {
		if state.Namespace != namespace || state.UserID != p.UserID.String() {
			return false
		}
	}
	for _, scan := range run.Scans {
		if scan.UserID != p.UserID {
			return false
		}
		for _, observation := range scan.Results {
			switch scan.Family {
			case "LEGACY":
				var result execution.BankResult
				if auditJSON(observation.Original, &result) != nil || result.Namespace != namespace || result.UserID != p.UserID.String() || result.Request.Namespace != namespace || result.Request.UserID != p.UserID.String() {
					return false
				}
			case "NATIVE":
				var result bankv2.Result
				if auditJSON(observation.Original, &result) != nil || result.Request == nil || result.Namespace != namespace || result.UserID != p.UserID.String() || result.Request.Namespace != namespace || result.Request.UserID != p.UserID.String() {
					return false
				}
			default:
				return false
			}
		}
	}
	return true
}
