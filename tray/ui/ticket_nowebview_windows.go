//go:build nowebview && windows

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

// powershellTicketScript is a PowerShell WinForms script that renders the
// New Ticket dialog.  Prefill values are injected via environment variables
// (MP_PREFILL_NAME, MP_PREFILL_EMAIL, MP_PREFILL_PHONE) to avoid any
// quoting / injection issues.  On submit the script writes a compact JSON
// object to stdout; on cancel it exits without writing anything.
const powershellTicketScript = `
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$prefillName  = $Env:MP_PREFILL_NAME
$prefillEmail = $Env:MP_PREFILL_EMAIL
$prefillPhone = $Env:MP_PREFILL_PHONE

$form = New-Object System.Windows.Forms.Form
$form.Text = "Submit New Ticket"
$form.Size = New-Object System.Drawing.Size(480, 420)
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.StartPosition = 'CenterScreen'

function New-Label($text, $x, $y) {
    $l = New-Object System.Windows.Forms.Label
    $l.Text = $text; $l.Location = New-Object System.Drawing.Point($x, $y)
    $l.Size = New-Object System.Drawing.Size(90, 20); $l.TextAlign = 'MiddleRight'
    return $l
}
function New-TextBox($x, $y, $w, $h, $val) {
    $t = New-Object System.Windows.Forms.TextBox
    $t.Location = New-Object System.Drawing.Point($x, $y)
    $t.Size = New-Object System.Drawing.Size($w, $h)
    $t.Text = $val
    return $t
}

$form.Controls.Add((New-Label "Name:"        10 14))
$txtName = New-TextBox 110 11 340 22 $prefillName; $form.Controls.Add($txtName)

$form.Controls.Add((New-Label "Email:"       10 44))
$txtEmail = New-TextBox 110 41 340 22 $prefillEmail; $form.Controls.Add($txtEmail)

$form.Controls.Add((New-Label "Phone:"       10 74))
$txtPhone = New-TextBox 110 71 340 22 $prefillPhone; $form.Controls.Add($txtPhone)

$form.Controls.Add((New-Label "Subject:"     10 104))
$txtSubject = New-TextBox 110 101 340 22 ""; $form.Controls.Add($txtSubject)

$form.Controls.Add((New-Label "Description:" 10 134))
$txtDesc = New-Object System.Windows.Forms.TextBox
$txtDesc.Location = New-Object System.Drawing.Point(110, 131)
$txtDesc.Size = New-Object System.Drawing.Size(340, 120)
$txtDesc.Multiline = $true; $txtDesc.ScrollBars = 'Vertical'
$form.Controls.Add($txtDesc)

$btnSubmit = New-Object System.Windows.Forms.Button
$btnSubmit.Text = "Submit Ticket"
$btnSubmit.Location = New-Object System.Drawing.Point(240, 265)
$btnSubmit.Size = New-Object System.Drawing.Size(110, 30)
$btnSubmit.DialogResult = [System.Windows.Forms.DialogResult]::OK
$form.AcceptButton = $btnSubmit; $form.Controls.Add($btnSubmit)

$btnCancel = New-Object System.Windows.Forms.Button
$btnCancel.Text = "Cancel"
$btnCancel.Location = New-Object System.Drawing.Point(360, 265)
$btnCancel.Size = New-Object System.Drawing.Size(90, 30)
$btnCancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
$form.CancelButton = $btnCancel; $form.Controls.Add($btnCancel)

$r = $form.ShowDialog()
if ($r -eq [System.Windows.Forms.DialogResult]::OK) {
    @{
        name        = $txtName.Text.Trim()
        email       = $txtEmail.Text.Trim()
        phone       = $txtPhone.Text.Trim()
        subject     = $txtSubject.Text.Trim()
        description = $txtDesc.Text.Trim()
    } | ConvertTo-Json -Compress | Write-Output
}
`

type ticketFormResult struct {
	Name        string `json:"name"`
	Email       string `json:"email"`
	Phone       string `json:"phone"`
	Subject     string `json:"subject"`
	Description string `json:"description"`
}

// openNewTicketDialog shows the New Ticket WinForms dialog, persists
// prefill values to HKCU, and submits the ticket to the portal.
func openNewTicketDialog(_ *api.ConfigResponse) {
	if gPortalURL == "" {
		showOSNotification("Submit Ticket", "Portal connection not yet established. Please try again shortly.")
		return
	}

	// Write the script to a temp file so we can invoke it cleanly.
	sf, err := os.CreateTemp("", "mp-ticket-*.ps1")
	if err != nil {
		logger.Warn("openNewTicketDialog: cannot create temp script: %v", err)
		return
	}
	scriptPath := sf.Name()
	defer os.Remove(scriptPath)
	if _, err := sf.WriteString(powershellTicketScript); err != nil {
		sf.Close()
		logger.Warn("openNewTicketDialog: write script: %v", err)
		return
	}
	sf.Close()

	prefillName, prefillEmail, prefillPhone := loadTicketPrefill()

	cmd := exec.Command(
		"powershell", "-NonInteractive", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
		"-File", scriptPath,
	)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000 /* CREATE_NO_WINDOW */}
	cmd.Env = append(os.Environ(),
		"MP_PREFILL_NAME="+prefillName,
		"MP_PREFILL_EMAIL="+prefillEmail,
		"MP_PREFILL_PHONE="+prefillPhone,
	)

	out, err := cmd.Output()
	if err != nil {
		// Non-zero exit means cancel or error — both are silent.
		logger.Debug("openNewTicketDialog: dialog exited: %v", err)
		return
	}
	outStr := strings.TrimSpace(string(out))
	if outStr == "" {
		// User cancelled.
		return
	}

	var result ticketFormResult
	if jsonErr := json.Unmarshal([]byte(outStr), &result); jsonErr != nil {
		logger.Warn("openNewTicketDialog: parse dialog output: %v", jsonErr)
		return
	}

	// Trim all fields once so subsequent checks are consistent.
	result.Name = strings.TrimSpace(result.Name)
	result.Email = strings.TrimSpace(result.Email)
	result.Phone = strings.TrimSpace(result.Phone)
	result.Subject = strings.TrimSpace(result.Subject)
	result.Description = strings.TrimSpace(result.Description)

	if result.Name == "" || result.Email == "" || result.Subject == "" {
		showOSNotification("Submit Ticket", "Name, email and subject are required.")
		return
	}

	// Persist prefill for next time.
	saveTicketPrefill(result.Name, result.Email, result.Phone)

	if err := submitTicketToPortal(result); err != nil {
		logger.Warn("openNewTicketDialog: submit: %v", err)
		showOSNotification("Submit Ticket", fmt.Sprintf("Could not submit ticket: %v", err))
		return
	}

	showOSNotification("Submit Ticket", "Your ticket has been submitted. We will be in touch soon.")
}

// ticketHTTPClient is reused across submissions to benefit from connection
// pooling.  A 15-second timeout is sufficient for a simple JSON POST.
var ticketHTTPClient = &http.Client{Timeout: 15 * time.Second}

// submitTicketToPortal posts the ticket to POST /api/tray/submit-ticket.
func submitTicketToPortal(result ticketFormResult) error {
	type submitRequest struct {
		DeviceUID   string `json:"device_uid"`
		Name        string `json:"name"`
		Email       string `json:"email"`
		Phone       string `json:"phone,omitempty"`
		Subject     string `json:"subject"`
		Description string `json:"description,omitempty"`
	}

	body, err := json.Marshal(submitRequest{
		DeviceUID:   gDeviceUID,
		Name:        result.Name,
		Email:       result.Email,
		Phone:       result.Phone,
		Subject:     result.Subject,
		Description: result.Description,
	})
	if err != nil {
		return fmt.Errorf("marshal: %w", err)
	}

	url := strings.TrimRight(gPortalURL, "/") + "/api/tray/submit-ticket"
	resp, err := ticketHTTPClient.Post(url, "application/json", bytes.NewReader(body))
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
