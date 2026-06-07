//go:build nowebview && windows

package main

import (
	"strings"
	"testing"

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
	if !strings.Contains(script, "$dyn1") {
		t.Error("expected dynamic variable $dyn1 for question id=1")
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
	if !strings.Contains(script, "$dyn10") {
		t.Error("expected $dyn10 variable for parent question id=10")
	}
	if !strings.Contains(script, "$dyn11") {
		t.Error("expected $dyn11 variable for conditional question id=11")
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
