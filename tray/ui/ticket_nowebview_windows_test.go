//go:build nowebview && windows

package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

// TestBuildTicketScriptNoQuestions verifies that the baseline script
// is generated correctly when no dynamic questions are defined.
func TestBuildTicketScriptNoQuestions(t *testing.T) {
	script := buildTicketScript(nil)

	mustContain := []string{
		"System.Windows.Forms",
		"$txtName",
		"$txtEmail",
		"$txtSubject",
		"$txtDesc",
		"$btnSubmit",
		// With no questions there should be no dynamic variable declarations.
	}
	for _, want := range mustContain {
		if !strings.Contains(script, want) {
			t.Errorf("expected script to contain %q", want)
		}
	}
	// No Update-Visibility call needed when there are no conditions.
	if strings.Contains(script, "Update-Visibility") {
		t.Error("expected no Update-Visibility function for scripts with no questions")
	}
}

// TestBuildTicketScriptTextQuestion checks that a text-type dynamic question
// produces a label and a TextBox control variable in the script.
func TestBuildTicketScriptTextQuestion(t *testing.T) {
	questions := []api.TicketQuestion{
		{
			ID:         1,
			FieldType:  "text",
			Label:      "Site address",
			IsRequired: true,
			Options:    nil,
			Conditions: nil,
		},
	}
	script := buildTicketScript(questions)

	if !strings.Contains(script, "Site address") {
		t.Error("expected label 'Site address' in script")
	}
	if !strings.Contains(script, "$dynCtrl_1") {
		t.Error("expected dynamic variable $dynCtrl_1 for question id=1")
	}
	// Required questions need a non-empty validation branch in submit handler.
	if !strings.Contains(script, "required") && !strings.Contains(script, "Required") && !strings.Contains(script, "is required") {
		t.Error("expected required-field validation message for required question")
	}
}

// TestBuildTicketScriptSelectQuestion checks that a select question emits a
// ComboBox and pre-populates options.
func TestBuildTicketScriptSelectQuestion(t *testing.T) {
	questions := []api.TicketQuestion{
		{
			ID:         2,
			FieldType:  "select",
			Label:      "Issue type",
			IsRequired: true,
			Options:    []string{"Hardware", "Software", "Network"},
			Conditions: nil,
		},
	}
	script := buildTicketScript(questions)

	if !strings.Contains(script, "Issue type") {
		t.Error("expected label 'Issue type' in script")
	}
	for _, opt := range []string{"Hardware", "Software", "Network"} {
		if !strings.Contains(script, opt) {
			t.Errorf("expected option %q in script", opt)
		}
	}
	if !strings.Contains(script, "ComboBox") {
		t.Error("expected ComboBox control for select question")
	}
}

// TestBuildTicketScriptBooleanQuestion checks that a boolean question emits a
// CheckBox rather than a TextBox or ComboBox.
func TestBuildTicketScriptBooleanQuestion(t *testing.T) {
	questions := []api.TicketQuestion{
		{
			ID:        3,
			FieldType: "boolean",
			Label:     "Have you restarted?",
			Options:   nil,
		},
	}
	script := buildTicketScript(questions)

	if !strings.Contains(script, "Have you restarted?") {
		t.Error("expected label text in CheckBox for boolean question")
	}
	if !strings.Contains(script, "CheckBox") {
		t.Error("expected CheckBox for boolean field type")
	}
}

// TestBuildTicketScriptConditionalEmitsUpdateVisibility verifies that when a
// question has conditions an Update-Visibility function is generated and wired
// to the parent control's change event.
func TestBuildTicketScriptConditionalEmitsUpdateVisibility(t *testing.T) {
	questions := []api.TicketQuestion{
		{
			ID:        10,
			FieldType: "select",
			Label:     "Issue category",
			Options:   []string{"Hardware", "Software"},
		},
		{
			ID:        11,
			FieldType: "text",
			Label:     "App name",
			Conditions: []api.TicketQuestionCondition{
				{ParentQuestionID: 10, Operator: "equals", ExpectedValue: "Software"},
			},
		},
	}
	script := buildTicketScript(questions)

	if !strings.Contains(script, "Update-Visibility") {
		t.Error("expected Update-Visibility function when conditions present")
	}
	// The parent question (id=10) must wire up a change event.
	if !strings.Contains(script, "$dynCtrl_10") {
		t.Error("expected $dynCtrl_10 variable for parent question id=10")
	}
	if !strings.Contains(script, "$dynCtrl_11") {
		t.Error("expected $dynCtrl_11 variable for conditional question id=11")
	}
}

// TestBuildTicketScriptAnswersJSON verifies the JSON output block references
// dynamic question answer variables.
func TestBuildTicketScriptAnswersJSON(t *testing.T) {
	questions := []api.TicketQuestion{
		{ID: 5, FieldType: "text", Label: "Room number"},
	}
	script := buildTicketScript(questions)

	// The answers array in the JSON output must include the question id.
	if !strings.Contains(script, `"question_id"`) && !strings.Contains(script, "question_id") {
		t.Error("expected question_id in JSON output block")
	}
	if !strings.Contains(script, "5") {
		t.Error("expected question id 5 in JSON output")
	}
}

func TestNewTicketDialogCommandAllowsWinFormsWindow(t *testing.T) {
	cmd := newTicketDialogCommand(`C:\Temp\mp-ticket.ps1`)
	for _, arg := range cmd.Args {
		if arg == "-WindowStyle" || arg == "Hidden" {
			t.Fatalf("ticket dialog command must not hide the PowerShell process: %v", cmd.Args)
		}
	}
	if cmd.SysProcAttr == nil {
		t.Fatal("expected Windows process attributes")
	}
	if cmd.SysProcAttr.HideWindow {
		t.Fatal("HideWindow hides the WinForms ticket dialog")
	}
}

func TestTicketFormResultUnmarshalAcceptsSingleAnswerObject(t *testing.T) {
	payload := `{"name":"Jane","email":"jane@example.com","phone":"123","subject":"Help","description":"Broken","answers":{"question_id":5,"value":"Room 4"}}`
	var result ticketFormResult
	if err := json.Unmarshal([]byte(payload), &result); err != nil {
		t.Fatalf("unmarshal single answer object: %v", err)
	}
	if len(result.Answers) != 1 || result.Answers[0].QuestionID != 5 || result.Answers[0].Value != "Room 4" {
		t.Fatalf("unexpected answers: %#v", result.Answers)
	}
}

func TestParseTicketDialogOutputAcceptsIncidentalText(t *testing.T) {
	output := "noise before\n" + `{"name":"Jane","email":"jane@example.com","phone":"123","subject":"Help","description":"Broken","answers":[{"question_id":5,"value":"Room 4"}]}` + "\nnoise after"
	result, err := parseTicketDialogOutput(output)
	if err != nil {
		t.Fatalf("parse dialog output: %v", err)
	}
	if result.Name != "Jane" || len(result.Answers) != 1 || result.Answers[0].QuestionID != 5 {
		t.Fatalf("unexpected result: %#v", result)
	}
}

func TestSubmitTicketToPortalPostsJSONWithAuth(t *testing.T) {
	oldPortalURL, oldDeviceUID, oldAuthToken, oldClient := gPortalURL, gDeviceUID, gAuthToken, ticketHTTPClient
	defer func() {
		gPortalURL = oldPortalURL
		gDeviceUID = oldDeviceUID
		gAuthToken = oldAuthToken
		ticketHTTPClient = oldClient
	}()

	gDeviceUID = "device-123"
	gAuthToken = "token-abc"

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/tray/submit-ticket" {
			t.Fatalf("unexpected path: %s", r.URL.Path)
		}
		if r.Method != http.MethodPost {
			t.Fatalf("unexpected method: %s", r.Method)
		}
		if got := r.Header.Get("Authorization"); got != "Bearer token-abc" {
			t.Fatalf("unexpected Authorization header: %q", got)
		}
		var body map[string]any
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Fatalf("decode body: %v", err)
		}
		if got := body["device_uid"]; got != "device-123" {
			t.Fatalf("unexpected device_uid: %#v", got)
		}
		if got := body["name"]; got != "Jane" {
			t.Fatalf("unexpected name: %#v", got)
		}
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"ticket_id":42}`))
	}))
	defer server.Close()

	gPortalURL = server.URL
	ticketHTTPClient = server.Client()
	ticketHTTPClient.Timeout = 5 * time.Second

	if err := submitTicketToPortal(ticketFormResult{Name: "Jane", Email: "jane@example.com", Subject: "Help"}); err != nil {
		t.Fatalf("submit ticket: %v", err)
	}
}

func TestSubmitTicketToPortalRequiresDeviceUID(t *testing.T) {
	oldPortalURL, oldDeviceUID := gPortalURL, gDeviceUID
	defer func() {
		gPortalURL = oldPortalURL
		gDeviceUID = oldDeviceUID
	}()
	gPortalURL = "https://portal.example.test"
	gDeviceUID = ""

	err := submitTicketToPortal(ticketFormResult{Name: "Jane", Email: "jane@example.com", Subject: "Help"})
	if err == nil || !strings.Contains(err.Error(), "device UID") {
		t.Fatalf("expected device UID error, got %v", err)
	}
}
