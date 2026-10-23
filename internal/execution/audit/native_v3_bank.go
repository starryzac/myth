package executionaudit

import (
	"encoding/json"
	"fmt"
	"time"

	evidence "zhiyu.local/zhiyu-go/internal/audit"
	"zhiyu.local/zhiyu-go/internal/execution/bankcredit"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	execution "zhiyu.local/zhiyu-go/internal/execution/domain"
)

const NativeBankKindV3 = "INDEPENDENT_BANK_SNAPSHOT"
const NativeBankVersionV3 = "independent-bank-snapshot-v3"
const NativeBankProtocolV3 = "native-independent-bank-capture-v3"

// Sources are independently observed from read-only bank APIs, never copied
// from app receipts/local ledger rows. Real wall observation times are distinct
// from an explicitly injected contractual business clock.
type NativeIndependentBankV3 struct {
	Protocol              string                               `json:"protocol"`
	Complete              bool                                 `json:"complete"`
	Problems              []string                             `json:"problems"`
	ObservationStartedAt  time.Time                            `json:"observation_started_at"`
	ObservationFinishedAt time.Time                            `json:"observation_finished_at"`
	Credit                bankcredit.CurrentState              `json:"credit"`
	Legacy                []execution.BankResult               `json:"legacy_operations"`
	Native                []bankv2.Result                      `json:"native_operations"`
	Stock                 bankv2.CurrentState                  `json:"native_stock"`
	Liabilities           bankv2.LiabilitySnapshot             `json:"liabilities"`
	HistoryAuthorities    []NativeConfiguredHistoryAuthorityV3 `json:"configured_history_authorities,omitempty"`
}

type nativeBankDataV3 struct {
	Environment   string `json:"environment"`
	EnvironmentID string `json:"environment_id"`
	UserID        string `json:"user_id"`
	Epoch         string `json:"epoch"`
	RawHex        string `json:"raw_hex"`
	RawSHA256     string `json:"raw_sha256"`
	RawBytes      int64  `json:"raw_bytes"`
	RawEncoding   string `json:"raw_encoding"`
}

func SealNativeIndependentBankV3(id string, scope evidence.Scope, capture NativeIndependentBankV3) (evidence.Original, error) {
	raw, err := json.Marshal(capture)
	if err != nil {
		return evidence.Original{}, err
	}
	encoded, err := nativeEncodeBytesV3(raw)
	if err != nil {
		return evidence.Original{}, err
	}
	data := businessData(scope)
	data["raw_hex"], data["raw_sha256"], data["raw_bytes"], data["raw_encoding"] = encoded, nakedHash(raw), int64(len(raw)), "GZIP"
	doc := evidence.Document{SchemaVersion: evidence.SchemaVersion, CanonicalVersion: evidence.CanonicalVersion, HashAlgorithm: evidence.HashAlgorithm, Simulation: true, ID: id, Kind: NativeBankKindV3, SemanticVersion: NativeBankVersionV3, Outcome: "INDEPENDENT_READ", Data: data, References: []evidence.Reference{}}
	if _, err = readNativeBankHeaderV3(doc); err != nil {
		return evidence.Original{}, err
	}
	return evidence.Seal(doc)
}

func readNativeBankHeaderV3(doc evidence.Document) (nativeBankDataV3, error) {
	var data nativeBankDataV3
	if doc.Kind != NativeBankKindV3 || doc.SemanticVersion != NativeBankVersionV3 || doc.Outcome != "INDEPENDENT_READ" || len(doc.Data) != 8 || len(doc.References) != 0 || decodeBusinessData(doc.Data, &data) != nil {
		return data, businessInvalid("native independent bank envelope")
	}
	if data.RawEncoding != "GZIP" {
		return data, fmt.Errorf("%w: independent bank encoding", evidence.ErrUnsupportedVersion)
	}
	if data.RawBytes < 1 || data.RawBytes > NativeMaximumRawRowBytesV3 || len(data.RawHex) > evidence.MaxCanonicalBytes {
		return data, fmt.Errorf("%w: independent bank capture capacity", evidence.ErrIncomplete)
	}
	return data, nil
}

func readNativeBankV3(doc evidence.Document, scope evidence.Scope) (NativeIndependentBankV3, error) {
	var out NativeIndependentBankV3
	header, err := readNativeBankHeaderV3(doc)
	if err != nil {
		return out, err
	}
	raw, err := nativeDecodeBytesV3(header.RawHex, header.RawSHA256, header.RawEncoding, header.RawBytes)
	if err != nil {
		return out, err
	}
	declared, err := evidence.ParseRawObject(raw)
	if err != nil {
		return out, err
	}
	if declared["protocol"] != NativeBankProtocolV3 {
		return out, fmt.Errorf("%w: independent bank capture protocol", evidence.ErrUnsupportedVersion)
	}
	if err = nativeCompactOriginalV3(raw, &out); err != nil {
		return out, err
	}
	if out.ObservationStartedAt.IsZero() || out.ObservationFinishedAt.Before(out.ObservationStartedAt) || out.Problems == nil || out.Legacy == nil || out.Native == nil {
		return out, businessInvalid("independent bank observation range")
	}
	if !out.Complete || len(out.Problems) > 0 {
		return out, fmt.Errorf("%w: independent bank source missing or changed", evidence.ErrIncomplete)
	}
	c := out.Credit
	if c.Validate() != nil || c.Namespace != scope.Namespace || c.UserID != scope.UserID || len(out.Legacy) > 1000 || len(out.Native) > 1000 {
		return out, businessInvalid("independent credit scope or complete operation inventory")
	}
	stock := out.Stock
	if stock.Protocol != bankv2.Protocol || stock.Namespace != scope.Namespace || stock.UserID != scope.UserID || stock.CapturedAt.IsZero() || stock.OperationWatermark != c.V2OperationWatermark || len(stock.Accounts) != len(c.Accounts) || len(stock.Products) > 100 || len(stock.Positions) > 1000 || len(stock.Goals) > 100 {
		return out, businessInvalid("independent stock scope or version")
	}
	if out.Liabilities.Validate() != nil || out.Liabilities.Namespace != scope.Namespace || out.Liabilities.UserID != scope.UserID {
		return out, businessInvalid("independent liability scope or completeness")
	}
	var last int64
	keys := map[string]bool{}
	for _, result := range out.Legacy {
		if result.Status == execution.BankNotFound || result.Namespace != scope.Namespace || result.UserID != scope.UserID || result.Sequence <= last || result.Sequence > c.V1OperationWatermark || keys[result.Key] || execution.VerifyBankResult(result.Request, result) != nil {
			return out, businessInvalid("independent legacy operation inventory")
		}
		keys[result.Key] = true
		last = result.Sequence
	}
	if last != c.V1OperationWatermark {
		return out, businessInvalid("independent legacy terminal watermark")
	}
	last = 0
	keys = map[string]bool{}
	for _, result := range out.Native {
		if result.Request == nil || result.Status == bankv2.NotFound || result.Namespace != scope.Namespace || result.UserID != scope.UserID || result.Sequence <= last || result.Sequence > c.V2OperationWatermark || keys[result.Key] || bankv2.VerifyResult(*result.Request, result) != nil {
			return out, businessInvalid("independent native operation inventory")
		}
		keys[result.Key] = true
		last = result.Sequence
	}
	if last != c.V2OperationWatermark {
		return out, businessInvalid("independent native terminal watermark")
	}
	accounts := map[string]bankcredit.Account{}
	for _, a := range c.Accounts {
		accounts[a.ID] = a
	}
	for _, a := range stock.Accounts {
		b, ok := accounts[a.ID]
		if !ok || a.Balance != b.Balance || a.Reserved != b.Reserved {
			return out, businessInvalid("independent current source reads disagree")
		}
		delete(accounts, a.ID)
	}
	if len(accounts) != 0 {
		return out, businessInvalid("independent account inventory incomplete")
	}
	return out, nil
}
