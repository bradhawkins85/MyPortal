//go:build windows

package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"syscall"
	"time"

	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/bradhawkins85/myportal-tray/internal/logger"
)

// ticketFormResult holds the core and dynamic fields collected by the dialog.
type ticketFormResult struct {
	Name        string                `json:"name"`
	Email       string                `json:"email"`
	Phone       string                `json:"phone"`
	Subject     string                `json:"subject"`
	Description string                `json:"description"`
	Answers     []ticketDynamicAnswer `json:"answers"`
}

// ticketDynamicAnswer represents one answer to a dynamic question.
type ticketDynamicAnswer struct {
	QuestionID int    `json:"question_id"`
	Value      string `json:"value"`
}

// UnmarshalJSON accepts the PowerShell ConvertTo-Json shapes used by the
// WinForms dialog. PowerShell 5 can collapse a single-item array into an
// object, so answers may arrive either as [] or as a single object.
func (r *ticketFormResult) UnmarshalJSON(data []byte) error {
	type rawTicketFormResult struct {
		Name        string          `json:"name"`
		Email       string          `json:"email"`
		Phone       string          `json:"phone"`
		Subject     string          `json:"subject"`
		Description string          `json:"description"`
		Answers     json.RawMessage `json:"answers"`
	}
	var raw rawTicketFormResult
	if err := json.Unmarshal(data, &raw); err != nil {
		return err
	}
	r.Name = raw.Name
	r.Email = raw.Email
	r.Phone = raw.Phone
	r.Subject = raw.Subject
	r.Description = raw.Description
	r.Answers = nil

	answerBytes := bytes.TrimSpace(raw.Answers)
	if len(answerBytes) == 0 || bytes.Equal(answerBytes, []byte("null")) {
		return nil
	}
	if bytes.Equal(answerBytes, []byte("[]")) {
		r.Answers = []ticketDynamicAnswer{}
		return nil
	}
	if answerBytes[0] == '[' {
		return json.Unmarshal(answerBytes, &r.Answers)
	}
	var single ticketDynamicAnswer
	if err := json.Unmarshal(answerBytes, &single); err != nil {
		return err
	}
	r.Answers = []ticketDynamicAnswer{single}
	return nil
}

// buildTicketScript generates the PowerShell WinForms script for the Submit
// Ticket dialog.  Fixed fields (name, email, phone, subject, description) are
// always rendered first; dynamic questions follow in the order returned by the
// server.  Conditional visibility is evaluated client-side: a question is
// shown only when all of its parent answer conditions are satisfied.  Required
// questions are enforced before the form accepts a submission.
//
// The script writes a compact JSON object to stdout on submit, or exits
// without writing on cancel.
func buildTicketScript(questions []api.TicketQuestion) string {
	var sb strings.Builder

	sb.WriteString(`
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$prefillName  = $Env:MP_PREFILL_NAME
$prefillEmail = $Env:MP_PREFILL_EMAIL
$prefillPhone = $Env:MP_PREFILL_PHONE

function New-Label($text, $x, $y) {
    $l = New-Object System.Windows.Forms.Label
    $l.Text = $text; $l.Location = New-Object System.Drawing.Point($x, $y)
    $l.Size = New-Object System.Drawing.Size(110, 20); $l.TextAlign = 'MiddleRight'
    return $l
}
function New-TextBox($x, $y, $w, $h, $val) {
    $t = New-Object System.Windows.Forms.TextBox
    $t.Location = New-Object System.Drawing.Point($x, $y)
    $t.Size = New-Object System.Drawing.Size($w, $h)
    $t.Text = $val
    return $t
}
function New-ComboBox($x, $y, $w, $items) {
    $c = New-Object System.Windows.Forms.ComboBox
    $c.Location = New-Object System.Drawing.Point($x, $y)
    $c.Size = New-Object System.Drawing.Size($w, 24)
    $c.DropDownStyle = 'DropDownList'
    foreach ($i in $items) { [void]$c.Items.Add($i) }
    return $c
}
function New-CheckBox($text, $x, $y, $w) {
    $cb = New-Object System.Windows.Forms.CheckBox
    $cb.Text = $text; $cb.Location = New-Object System.Drawing.Point($x, $y)
    $cb.Size = New-Object System.Drawing.Size($w, 22)
    return $cb
}

$form = New-Object System.Windows.Forms.Form
$form.Text = "Submit New Ticket"
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.StartPosition = 'CenterScreen'

$y = 10

# ── Fixed fields ────────────────────────────────────────────────────
$form.Controls.Add((New-Label "Name:" 10 ($y+2)))
$txtName = New-TextBox 130 $y 330 22 $prefillName; $form.Controls.Add($txtName)
$y += 34

$form.Controls.Add((New-Label "Email:" 10 ($y+2)))
$txtEmail = New-TextBox 130 $y 330 22 $prefillEmail; $form.Controls.Add($txtEmail)
$y += 34

$form.Controls.Add((New-Label "Phone:" 10 ($y+2)))
$txtPhone = New-TextBox 130 $y 330 22 $prefillPhone; $form.Controls.Add($txtPhone)
$y += 34

$form.Controls.Add((New-Label "Subject:" 10 ($y+2)))
$txtSubject = New-TextBox 130 $y 330 22 ""; $form.Controls.Add($txtSubject)
$y += 34

$form.Controls.Add((New-Label "Description:" 10 ($y+2)))
$txtDesc = New-Object System.Windows.Forms.TextBox
$txtDesc.Location = New-Object System.Drawing.Point(130, $y)
$txtDesc.Size = New-Object System.Drawing.Size(330, 80)
$txtDesc.Multiline = $true; $txtDesc.ScrollBars = 'Vertical'
$form.Controls.Add($txtDesc)
$y += 90

# ── Dynamic questions ─────────────────────────────────────────────
`)

	// Emit PS code for each dynamic question.
	type qMeta struct {
		varName    string // e.g. $dynCtrl_3
		conditions []api.TicketQuestionCondition
		labelVar   string // e.g. $dynLbl_3
		qID        int
		required   bool
		fieldType  string
	}
	metas := make([]qMeta, 0, len(questions))

	for _, q := range questions {
		varName := fmt.Sprintf("$dynCtrl_%d", q.ID)
		lblVar := fmt.Sprintf("$dynLbl_%d", q.ID)
		labelText := q.Label
		if q.IsRequired {
			labelText += " *"
		}

		switch q.FieldType {
		case "select":
			optList := psStringList(q.Options)
			sb.WriteString(fmt.Sprintf("\n$form.Controls.Add((New-Label %q 10 ($y+2)))\n", labelText))
			sb.WriteString(fmt.Sprintf("%s = New-ComboBox 130 $y 330 @(%s)\n", varName, optList))
			sb.WriteString(fmt.Sprintf("$form.Controls.Add(%s)\n", varName))
		case "boolean":
			sb.WriteString(fmt.Sprintf("\n%s = New-CheckBox %q 130 $y 330\n", varName, labelText))
			sb.WriteString(fmt.Sprintf("$form.Controls.Add(%s)\n", varName))
			// For booleans the label IS the checkbox; still emit a hidden label var.
			sb.WriteString(fmt.Sprintf("%s = $null\n", lblVar))
		default: // text
			sb.WriteString(fmt.Sprintf("\n$form.Controls.Add((New-Label %q 10 ($y+2)))\n", labelText))
			placeholder := q.Placeholder
			sb.WriteString(fmt.Sprintf("%s = New-TextBox 130 $y 330 22 %q\n", varName, placeholder))
			sb.WriteString(fmt.Sprintf("$form.Controls.Add(%s)\n", varName))
		}

		// Store the label control reference so we can hide it along with the control.
		if q.FieldType != "boolean" {
			sb.WriteString(fmt.Sprintf("%s = $form.Controls | Where-Object { $_.Text -eq %q } | Select-Object -Last 1\n",
				lblVar, labelText))
		}

		sb.WriteString(fmt.Sprintf("$y += 34\n"))

		metas = append(metas, qMeta{
			varName:    varName,
			conditions: q.Conditions,
			labelVar:   lblVar,
			qID:        q.ID,
			required:   q.IsRequired,
			fieldType:  q.FieldType,
		})
	}

	// Resize form and place buttons after all controls.
	sb.WriteString(`
$y += 10
$form.ClientSize = New-Object System.Drawing.Size(480, ($y + 44))

$btnSubmit = New-Object System.Windows.Forms.Button
$btnSubmit.Text = "Submit Ticket"
$btnSubmit.Location = New-Object System.Drawing.Point(250, $y)
$btnSubmit.Size = New-Object System.Drawing.Size(110, 30)
$form.Controls.Add($btnSubmit)

$btnCancel = New-Object System.Windows.Forms.Button
$btnCancel.Text = "Cancel"
$btnCancel.Location = New-Object System.Drawing.Point(370, $y)
$btnCancel.Size = New-Object System.Drawing.Size(90, 30)
$btnCancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
$form.CancelButton = $btnCancel
$form.Controls.Add($btnCancel)

# ── Conditional visibility helper ─────────────────────────────────
`)

	if len(metas) > 0 {
		// Emit a hashtable mapping question_id -> current text value getter.
		sb.WriteString("function Get-DynValue($id) {\n  switch ($id) {\n")
		for _, m := range metas {
			switch m.fieldType {
			case "boolean":
				sb.WriteString(fmt.Sprintf("    %d { if (%s.Checked) { 'Yes' } else { 'No' } }\n", m.qID, m.varName))
			case "select":
				sb.WriteString(fmt.Sprintf("    %d { if (%s.SelectedItem) { [string]%s.SelectedItem } else { '' } }\n", m.qID, m.varName, m.varName))
			default:
				sb.WriteString(fmt.Sprintf("    %d { %s.Text.Trim() }\n", m.qID, m.varName))
			}
		}
		sb.WriteString("    default { '' }\n  }\n}\n")

		sb.WriteString("\nfunction Update-Visibility {\n")
		for _, m := range metas {
			if len(m.conditions) == 0 {
				continue // always visible — no code needed
			}
			// Build a combined boolean expression.
			parts := make([]string, 0, len(m.conditions))
			for _, c := range m.conditions {
				actual := fmt.Sprintf("(Get-DynValue %d)", c.ParentQuestionID)
				expected := fmt.Sprintf("%q", strings.ToLower(c.ExpectedValue))
				switch strings.ToLower(c.Operator) {
				case "not_equals":
					parts = append(parts, fmt.Sprintf("(%s.ToLower() -ne %s)", actual, expected))
				case "contains":
					parts = append(parts, fmt.Sprintf("(%s.ToLower() -like ('*'+%s+'*'))", actual, expected))
				default: // equals
					parts = append(parts, fmt.Sprintf("(%s.ToLower() -eq %s)", actual, expected))
				}
			}
			expr := strings.Join(parts, " -and ")
			sb.WriteString(fmt.Sprintf("  $vis_%d = %s\n", m.qID, expr))
			sb.WriteString(fmt.Sprintf("  %s.Visible = $vis_%d\n", m.varName, m.qID))
			if m.fieldType != "boolean" {
				sb.WriteString(fmt.Sprintf("  if (%s) { %s.Visible = $true } else { %s.Visible = $false }\n",
					m.varName+".Visible", m.labelVar, m.labelVar))
			}
		}
		sb.WriteString("}\nUpdate-Visibility\n")

		// Wire change events for controls whose answers may affect others.
		triggeredIDs := map[int]bool{}
		for _, m := range metas {
			for _, c := range m.conditions {
				triggeredIDs[c.ParentQuestionID] = true
			}
		}
		for _, m := range metas {
			if !triggeredIDs[m.qID] {
				continue
			}
			switch m.fieldType {
			case "boolean":
				sb.WriteString(fmt.Sprintf("%s.add_CheckedChanged({ Update-Visibility })\n", m.varName))
			case "select":
				sb.WriteString(fmt.Sprintf("%s.add_SelectedIndexChanged({ Update-Visibility })\n", m.varName))
			default:
				sb.WriteString(fmt.Sprintf("%s.add_TextChanged({ Update-Visibility })\n", m.varName))
			}
		}
	}

	// Validation + submission.
	sb.WriteString(`
$btnSubmit.add_Click({
    $errs = @()
    if ($txtName.Text.Trim() -eq '')    { $errs += 'Name is required.' }
    if ($txtEmail.Text.Trim() -eq '')   { $errs += 'Email is required.' }
    if ($txtSubject.Text.Trim() -eq '') { $errs += 'Subject is required.' }
`)

	for _, m := range metas {
		if !m.required {
			continue
		}
		var visCheck string
		if len(m.conditions) > 0 {
			visCheck = fmt.Sprintf("if (%s.Visible) { ", m.varName)
		}
		var valExpr string
		switch m.fieldType {
		case "boolean":
			// A boolean checkbox is always "answered" (true or false), so skip required check.
			continue
		case "select":
			valExpr = fmt.Sprintf("%s.SelectedItem -eq $null", m.varName)
		default:
			valExpr = fmt.Sprintf("%s.Text.Trim() -eq ''", m.varName)
		}
		labelQ := questions[indexOfQID(questions, m.qID)].Label
		if visCheck != "" {
			sb.WriteString(fmt.Sprintf("    %sif (%s) { $errs += '%s is required.' } }\n",
				visCheck, valExpr, strings.ReplaceAll(labelQ, "'", "''")))
		} else {
			sb.WriteString(fmt.Sprintf("    if (%s) { $errs += '%s is required.' }\n",
				valExpr, strings.ReplaceAll(labelQ, "'", "''")))
		}
	}

	// The backtick character cannot appear inside a Go raw string literal, so we
	// concatenate: the PowerShell newline escape is a literal backtick followed by n.
	sb.WriteString("\n    if ($errs.Count -gt 0) {\n        [System.Windows.Forms.MessageBox]::Show(($errs -join \"" + "`" + `n"), 'Validation', 'OK', 'Warning') | Out-Null
        return
    }
`)

	// Build answers array.
	if len(metas) > 0 {
		sb.WriteString("    $answers = @()\n")
		for _, m := range metas {
			var valExpr string
			switch m.fieldType {
			case "boolean":
				valExpr = fmt.Sprintf("if (%s.Checked) { 'Yes' } else { 'No' }", m.varName)
			case "select":
				valExpr = fmt.Sprintf("[string]%s.SelectedItem", m.varName)
			default:
				valExpr = fmt.Sprintf("%s.Text.Trim()", m.varName)
			}
			if len(m.conditions) > 0 {
				sb.WriteString(fmt.Sprintf("    if (%s.Visible) { $answers += @{ question_id = %d; value = (%s) } }\n",
					m.varName, m.qID, valExpr))
			} else {
				sb.WriteString(fmt.Sprintf("    $answers += @{ question_id = %d; value = (%s) }\n",
					m.qID, valExpr))
			}
		}
	} else {
		sb.WriteString("    $answers = @()\n")
	}

	sb.WriteString(`
    @{
        name        = $txtName.Text.Trim()
        email       = $txtEmail.Text.Trim()
        phone       = $txtPhone.Text.Trim()
        subject     = $txtSubject.Text.Trim()
        description = $txtDesc.Text.Trim()
        answers     = $answers
    } | ConvertTo-Json -Compress -Depth 5 | Write-Output
    $form.DialogResult = [System.Windows.Forms.DialogResult]::OK
    $form.Close()
})

$form.ShowDialog() | Out-Null
`)

	return sb.String()
}

// psStringList formats a Go string slice as a PowerShell string array literal.
func psStringList(items []string) string {
	if len(items) == 0 {
		return ""
	}
	quoted := make([]string, len(items))
	for i, s := range items {
		quoted[i] = fmt.Sprintf("%q", s)
	}
	return strings.Join(quoted, ", ")
}

// indexOfQID returns the slice index of the question with the given ID, or -1.
func indexOfQID(qs []api.TicketQuestion, id int) int {
	for i, q := range qs {
		if q.ID == id {
			return i
		}
	}
	return -1
}

// parseTicketDialogOutput decodes the JSON object emitted by the PowerShell
// dialog. If PowerShell or the host emits incidental text before/after the
// JSON payload, the final object is extracted so contact details can still be
// saved and the ticket can be submitted.
func parseTicketDialogOutput(output string) (ticketFormResult, error) {
	var result ticketFormResult
	trimmed := strings.TrimSpace(output)
	if trimmed == "" {
		return result, fmt.Errorf("empty dialog output")
	}
	if err := json.Unmarshal([]byte(trimmed), &result); err == nil {
		return result, nil
	}
	start := strings.Index(trimmed, "{")
	end := strings.LastIndex(trimmed, "}")
	if start >= 0 && end > start {
		candidate := trimmed[start : end+1]
		if err := json.Unmarshal([]byte(candidate), &result); err == nil {
			return result, nil
		}
	}
	return result, fmt.Errorf("invalid dialog JSON")
}

// openNewTicketDialog loads dynamic questions from the portal, shows the New
// Ticket WinForms dialog, persists prefill values to HKCU, and submits the
// ticket to the portal.
func openNewTicketDialog(cfg *api.ConfigResponse) {
	if gPortalURL == "" {
		showOSNotification("Submit Ticket", "Portal connection not yet established. Please try again shortly.")
		return
	}

	// Fetch dynamic questions using the device auth token (non-fatal on error).
	questions := fetchTicketQuestions()

	script := buildTicketScript(questions)

	sf, err := os.CreateTemp("", "mp-ticket-*.ps1")
	if err != nil {
		logger.Warn("openNewTicketDialog: cannot create temp script: %v", err)
		return
	}
	scriptPath := sf.Name()
	defer os.Remove(scriptPath)
	if _, err := sf.WriteString(script); err != nil {
		sf.Close()
		logger.Warn("openNewTicketDialog: write script: %v", err)
		return
	}
	sf.Close()

	prefillName, prefillEmail, prefillPhone := loadTicketPrefill()

	cmd := newTicketDialogCommand(scriptPath)
	cmd.Env = append(os.Environ(),
		"MP_PREFILL_NAME="+prefillName,
		"MP_PREFILL_EMAIL="+prefillEmail,
		"MP_PREFILL_PHONE="+prefillPhone,
	)

	out, err := cmd.Output()
	if err != nil {
		logger.Debug("openNewTicketDialog: dialog exited: %v", err)
		return
	}
	outStr := strings.TrimSpace(string(out))
	if outStr == "" {
		return
	}

	result, jsonErr := parseTicketDialogOutput(outStr)
	if jsonErr != nil {
		logger.Warn("openNewTicketDialog: parse dialog output: %v", jsonErr)
		return
	}

	result.Name = strings.TrimSpace(result.Name)
	result.Email = strings.TrimSpace(result.Email)
	result.Phone = strings.TrimSpace(result.Phone)
	result.Subject = strings.TrimSpace(result.Subject)
	result.Description = strings.TrimSpace(result.Description)

	if result.Name != "" || result.Email != "" || result.Phone != "" {
		saveTicketPrefill(result.Name, result.Email, result.Phone)
	}

	if result.Name == "" || result.Email == "" || result.Subject == "" {
		showOSNotification("Submit Ticket", "Name, email and subject are required.")
		return
	}

	if err := submitTicketToPortal(result); err != nil {
		logger.Warn("openNewTicketDialog: submit: %v", err)
		showOSNotification("Submit Ticket", fmt.Sprintf("Could not submit ticket: %v", err))
		return
	}

	showOSNotification("Submit Ticket", "Your ticket has been submitted. We will be in touch soon.")
}

func newTicketDialogCommand(scriptPath string) *exec.Cmd {
	cmd := exec.Command(
		"powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
		"-File", scriptPath,
	)
	// CREATE_NO_WINDOW suppresses the transient PowerShell console while still
	// allowing the WinForms ticket dialog to become visible. Do not pass
	// -WindowStyle Hidden or STARTF_USESHOWWINDOW/HideWindow here: those startup
	// hints can hide the form itself, making the tray action appear to do nothing.
	cmd.SysProcAttr = &syscall.SysProcAttr{CreationFlags: 0x08000000 /* CREATE_NO_WINDOW */}
	return cmd
}

// fetchTicketQuestions calls GET /api/tray/ticket-questions using the device
// auth token.  Returns nil on any error; callers treat nil as "no questions".
func fetchTicketQuestions() []api.TicketQuestion {
	if gPortalURL == "" || gAuthToken == "" {
		logger.Debug("fetchTicketQuestions: portal URL or auth token not available")
		return nil
	}
	url := strings.TrimRight(gPortalURL, "/") + "/api/tray/ticket-questions"
	req, err := http.NewRequest(http.MethodGet, url, nil)
	if err != nil {
		logger.Warn("fetchTicketQuestions: build request: %v", err)
		return nil
	}
	req.Header.Set("Authorization", "Bearer "+gAuthToken)
	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		logger.Warn("fetchTicketQuestions: HTTP error: %v", err)
		return nil
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		logger.Debug("fetchTicketQuestions: server returned %d", resp.StatusCode)
		return nil
	}
	var result api.TicketQuestionsResponse
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		logger.Warn("fetchTicketQuestions: decode error: %v", err)
		return nil
	}
	logger.Debug("fetchTicketQuestions: loaded %d questions", len(result.Questions))
	return result.Questions
}

// ticketHTTPClient is reused across submissions to benefit from connection
// pooling.  A 15-second timeout is sufficient for a simple JSON POST.
var ticketHTTPClient = &http.Client{Timeout: 15 * time.Second}

// submitTicketToPortal posts the ticket to POST /api/tray/submit-ticket.
func submitTicketToPortal(result ticketFormResult) error {
	type submitAnswer struct {
		QuestionID int    `json:"question_id"`
		Value      string `json:"value"`
	}
	type submitRequest struct {
		DeviceUID   string         `json:"device_uid"`
		Name        string         `json:"name"`
		Email       string         `json:"email"`
		Phone       string         `json:"phone,omitempty"`
		Subject     string         `json:"subject"`
		Description string         `json:"description,omitempty"`
		Answers     []submitAnswer `json:"answers,omitempty"`
	}

	answers := make([]submitAnswer, 0, len(result.Answers))
	for _, a := range result.Answers {
		answers = append(answers, submitAnswer{QuestionID: a.QuestionID, Value: a.Value})
	}

	deviceUID := strings.TrimSpace(gDeviceUID)
	if deviceUID == "" {
		refreshDeviceUID()
		deviceUID = strings.TrimSpace(gDeviceUID)
	}
	if deviceUID == "" {
		return fmt.Errorf("device UID is not available yet")
	}

	body, err := json.Marshal(submitRequest{
		DeviceUID:   deviceUID,
		Name:        result.Name,
		Email:       result.Email,
		Phone:       result.Phone,
		Subject:     result.Subject,
		Description: result.Description,
		Answers:     answers,
	})
	if err != nil {
		return fmt.Errorf("marshal: %w", err)
	}

	url := strings.TrimRight(gPortalURL, "/") + "/api/tray/submit-ticket"
	req, err := http.NewRequest(http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("build request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	if gAuthToken != "" {
		req.Header.Set("Authorization", "Bearer "+gAuthToken)
	}
	resp, err := ticketHTTPClient.Do(req)
	if err != nil {
		return fmt.Errorf("post: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		msg := strings.TrimSpace(string(b))
		if len(b) == 512 {
			msg += "…"
		}
		return fmt.Errorf("HTTP %d: %s", resp.StatusCode, msg)
	}
	logger.Info("Tray ticket submitted (HTTP %d)", resp.StatusCode)
	return nil
}
