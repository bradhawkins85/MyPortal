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

Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class NativeMethods {
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern IntPtr SendMessage(IntPtr hWnd, int msg, IntPtr wParam, string lParam);
}
"@

$prefillName  = $Env:MP_PREFILL_NAME
$prefillEmail = $Env:MP_PREFILL_EMAIL
$prefillPhone = $Env:MP_PREFILL_PHONE
$brandIconPath = $Env:MP_BRAND_ICON_PATH

$colorInk = [System.Drawing.ColorTranslator]::FromHtml('#1f2937')
$colorMuted = [System.Drawing.ColorTranslator]::FromHtml('#64748b')
$colorPrimary = [System.Drawing.ColorTranslator]::FromHtml('#0f8f8f')
$fontBase = New-Object System.Drawing.Font('Segoe UI', 10)
$fontLabel = New-Object System.Drawing.Font('Segoe UI', 10, [System.Drawing.FontStyle]::Bold)
$fontTitle = New-Object System.Drawing.Font('Segoe UI', 18, [System.Drawing.FontStyle]::Bold)
$fontIntro = New-Object System.Drawing.Font('Segoe UI', 10.5)

function Set-CueBanner($control, $text) {
    if ([string]::IsNullOrWhiteSpace($text)) { return }
    if ($control.IsHandleCreated -eq $false) { [void]$control.CreateControl() }
    [void][NativeMethods]::SendMessage($control.Handle, 0x1501, [IntPtr]1, $text)
}
function New-Label($text, $x, $y) {
    $l = New-Object System.Windows.Forms.Label
    $l.Text = $text
    $l.Location = New-Object System.Drawing.Point($x, $y)
    $l.Size = New-Object System.Drawing.Size(125, 24)
    $l.TextAlign = 'MiddleLeft'
    $l.ForeColor = $colorInk
    $l.Font = $fontLabel
    return $l
}
function New-TextBox($x, $y, $w, $h, $val, $placeholder) {
    $t = New-Object System.Windows.Forms.TextBox
    $t.Location = New-Object System.Drawing.Point($x, $y)
    $t.Size = New-Object System.Drawing.Size($w, $h)
    $t.Text = $val
    $t.Font = $fontBase
    $t.ForeColor = $colorInk
    $t.BorderStyle = 'FixedSingle'
    Set-CueBanner $t $placeholder
    return $t
}
function New-ComboBox($x, $y, $w, $items) {
    $c = New-Object System.Windows.Forms.ComboBox
    $c.Location = New-Object System.Drawing.Point($x, $y)
    $c.Size = New-Object System.Drawing.Size($w, 28)
    $c.DropDownStyle = 'DropDownList'
    $c.Font = $fontBase
    $c.ForeColor = $colorInk
    foreach ($i in $items) { [void]$c.Items.Add($i) }
    return $c
}
function New-CheckBox($text, $x, $y, $w) {
    $cb = New-Object System.Windows.Forms.CheckBox
    $cb.Text = $text
    $cb.Location = New-Object System.Drawing.Point($x, $y)
    $cb.Size = New-Object System.Drawing.Size($w, 26)
    $cb.Font = $fontBase
    $cb.ForeColor = $colorInk
    return $cb
}
function New-RoundedRectPath($x, $y, $w, $h, $r) {
    $path = New-Object System.Drawing.Drawing2D.GraphicsPath
    $d = $r * 2
    $path.AddArc($x, $y, $d, $d, 180, 90)
    $path.AddArc(($x + $w - $d), $y, $d, $d, 270, 90)
    $path.AddArc(($x + $w - $d), ($y + $h - $d), $d, $d, 0, 90)
    $path.AddArc($x, ($y + $h - $d), $d, $d, 90, 90)
    $path.CloseFigure()
    return $path
}
function New-BrandBitmap($size) {
    if (-not [string]::IsNullOrWhiteSpace($brandIconPath) -and [System.IO.File]::Exists($brandIconPath)) {
        try {
            $ico = New-Object System.Drawing.Icon($brandIconPath, $size, $size)
            return $ico.ToBitmap()
        } catch {
            # Fall back to the generated MyPortal mark if the downloaded icon
            # cannot be decoded by System.Drawing on this Windows version.
        }
    }
    # Mirrors app/static/logo.svg using GDI+ so the tray dialog has a branded
    # image and window icon without shipping generated binary assets.
    $bmp = New-Object System.Drawing.Bitmap($size, $size)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $scale = $size / 120.0
    $g.ScaleTransform($scale, $scale)
    $rectPath = New-RoundedRectPath 0 0 120 120 28
    $g.FillPath((New-Object System.Drawing.SolidBrush([System.Drawing.ColorTranslator]::FromHtml('#0f172a'))), $rectPath)
    $gradRect = New-Object System.Drawing.Rectangle(18, 18, 84, 84)
    $grad = New-Object System.Drawing.Drawing2D.LinearGradientBrush($gradRect, [System.Drawing.ColorTranslator]::FromHtml('#38bdf8'), [System.Drawing.ColorTranslator]::FromHtml('#6366f1'), 45)
    $g.FillEllipse($grad, 18, 18, 84, 84)
    $g.FillEllipse((New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(242, 15, 23, 42))), 36, 36, 48, 48)
    $pen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(217, 248, 250, 252), 6)
    $pen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $pen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    $pen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
    $g.DrawArc($pen, 28, 28, 64, 64, 180, 180)
    $g.DrawArc($pen, 28, 28, 64, 64, 0, 180)
    $g.FillEllipse((New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(217, 248, 250, 252))), 50, 50, 20, 20)
    $g.Dispose()
    return $bmp
}
function Set-ButtonStyle($button, $isPrimary) {
    $button.Font = New-Object System.Drawing.Font('Segoe UI', 10)
    $button.FlatStyle = 'Flat'
    $button.FlatAppearance.BorderSize = 1
    if ($isPrimary) {
        $button.BackColor = $colorPrimary
        $button.ForeColor = [System.Drawing.Color]::White
        $button.FlatAppearance.BorderColor = $colorPrimary
    } else {
        $button.BackColor = [System.Drawing.Color]::White
        $button.ForeColor = $colorInk
        $button.FlatAppearance.BorderColor = [System.Drawing.ColorTranslator]::FromHtml('#9ca3af')
    }
}

$form = New-Object System.Windows.Forms.Form
$form.Text = "Support Request"
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $true
$form.StartPosition = 'CenterScreen'
$form.BackColor = [System.Drawing.Color]::White
$form.Font = $fontBase
$form.AutoScroll = $true
$form.ClientSize = New-Object System.Drawing.Size(780, 760)
if (-not [string]::IsNullOrWhiteSpace($brandIconPath) -and [System.IO.File]::Exists($brandIconPath)) {
    try {
        $form.Icon = New-Object System.Drawing.Icon($brandIconPath)
    } catch {
        $brandIcon = New-BrandBitmap 32
        $form.Icon = [System.Drawing.Icon]::FromHandle($brandIcon.GetHicon())
    }
} else {
    $brandIcon = New-BrandBitmap 32
    $form.Icon = [System.Drawing.Icon]::FromHandle($brandIcon.GetHicon())
}

$logo = New-Object System.Windows.Forms.PictureBox
$logo.Image = New-BrandBitmap 116
$logo.SizeMode = 'Zoom'
$logo.Location = New-Object System.Drawing.Point(332, 34)
$logo.Size = New-Object System.Drawing.Size(116, 116)
$form.Controls.Add($logo)

$lblTitle = New-Object System.Windows.Forms.Label
$lblTitle.Text = "Support Request"
$lblTitle.Location = New-Object System.Drawing.Point(70, 188)
$lblTitle.Size = New-Object System.Drawing.Size(640, 36)
$lblTitle.Font = $fontTitle
$lblTitle.ForeColor = $colorInk
$form.Controls.Add($lblTitle)

$lblIntro = New-Object System.Windows.Forms.Label
$lblIntro.Text = "Please fill out the form to submit your support request."
$lblIntro.Location = New-Object System.Drawing.Point(70, 228)
$lblIntro.Size = New-Object System.Drawing.Size(640, 24)
$lblIntro.Font = $fontIntro
$lblIntro.ForeColor = $colorMuted
$form.Controls.Add($lblIntro)

$labelX = 70
$fieldX = 205
$fieldW = 505
$y = 284
$rowGap = 46

# ── Fixed fields ────────────────────────────────────────────────────
$form.Controls.Add((New-Label "Name *" $labelX ($y+2)))
$txtName = New-TextBox $fieldX $y $fieldW 30 $prefillName "e.g. John Smith"; $form.Controls.Add($txtName)
$y += $rowGap

$form.Controls.Add((New-Label "Email *" $labelX ($y+2)))
$txtEmail = New-TextBox $fieldX $y $fieldW 30 $prefillEmail "e.g. john@smith.com"; $form.Controls.Add($txtEmail)
$y += $rowGap

$form.Controls.Add((New-Label "Phone" $labelX ($y+2)))
$txtPhone = New-TextBox $fieldX $y $fieldW 30 $prefillPhone "The best number to reach you at"; $form.Controls.Add($txtPhone)
$y += $rowGap

$form.Controls.Add((New-Label "Subject *" $labelX ($y+2)))
$txtSubject = New-TextBox $fieldX $y $fieldW 30 "" "e.g. I'm having networking issues"; $form.Controls.Add($txtSubject)
$y += $rowGap

$form.Controls.Add((New-Label "Description" $labelX ($y+2)))
$txtDesc = New-Object System.Windows.Forms.TextBox
$txtDesc.Location = New-Object System.Drawing.Point($fieldX, $y)
$txtDesc.Size = New-Object System.Drawing.Size($fieldW, 112)
$txtDesc.Multiline = $true
$txtDesc.ScrollBars = 'Vertical'
$txtDesc.Font = $fontBase
$txtDesc.ForeColor = $colorInk
$txtDesc.BorderStyle = 'FixedSingle'
Set-CueBanner $txtDesc "Please provide the details about the issue you are experiencing"
$form.Controls.Add($txtDesc)
$y += 132

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
			sb.WriteString(fmt.Sprintf("\n$form.Controls.Add((New-Label %q $labelX ($y+2)))\n", labelText))
			sb.WriteString(fmt.Sprintf("%s = New-ComboBox $fieldX $y $fieldW @(%s)\n", varName, optList))
			sb.WriteString(fmt.Sprintf("$form.Controls.Add(%s)\n", varName))
		case "boolean":
			sb.WriteString(fmt.Sprintf("\n%s = New-CheckBox %q $fieldX $y $fieldW\n", varName, labelText))
			sb.WriteString(fmt.Sprintf("$form.Controls.Add(%s)\n", varName))
			// For booleans the label IS the checkbox; still emit a hidden label var.
			sb.WriteString(fmt.Sprintf("%s = $null\n", lblVar))
		default: // text
			sb.WriteString(fmt.Sprintf("\n$form.Controls.Add((New-Label %q $labelX ($y+2)))\n", labelText))
			placeholder := q.Placeholder
			sb.WriteString(fmt.Sprintf("%s = New-TextBox $fieldX $y $fieldW 30 \"\" %q\n", varName, placeholder))
			sb.WriteString(fmt.Sprintf("$form.Controls.Add(%s)\n", varName))
		}

		// Store the label control reference so we can hide it along with the control.
		if q.FieldType != "boolean" {
			sb.WriteString(fmt.Sprintf("%s = $form.Controls | Where-Object { $_.Text -eq %q } | Select-Object -Last 1\n",
				lblVar, labelText))
		}

		sb.WriteString("$y += $rowGap\n")

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
$y += 12
$form.ClientSize = New-Object System.Drawing.Size(780, [Math]::Min(($y + 92), [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea.Height - 80))

$btnCancel = New-Object System.Windows.Forms.Button
$btnCancel.Text = "Cancel"
$btnCancel.Location = New-Object System.Drawing.Point($labelX, ($y + 18))
$btnCancel.Size = New-Object System.Drawing.Size(112, 40)
$btnCancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
Set-ButtonStyle $btnCancel $false
$form.CancelButton = $btnCancel
$form.Controls.Add($btnCancel)

$btnSubmit = New-Object System.Windows.Forms.Button
$btnSubmit.Text = "Send Request"
$btnSubmit.Location = New-Object System.Drawing.Point(($fieldX + $fieldW - 190), ($y + 18))
$btnSubmit.Size = New-Object System.Drawing.Size(190, 40)
Set-ButtonStyle $btnSubmit $true
$form.AcceptButton = $btnSubmit
$form.Controls.Add($btnSubmit)

# WinForms event handlers do not reliably write pipeline output back to the
# host process. Store the accepted payload in script scope and emit it only
# after ShowDialog returns so the Go tray process can capture stdout and submit
# the ticket to MyPortal.
$script:TicketDialogResult = $null

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
    $script:TicketDialogResult = @{
        name        = $txtName.Text.Trim()
        email       = $txtEmail.Text.Trim()
        phone       = $txtPhone.Text.Trim()
        subject     = $txtSubject.Text.Trim()
        description = $txtDesc.Text.Trim()
        answers     = $answers
    }
    $form.DialogResult = [System.Windows.Forms.DialogResult]::OK
    $form.Close()
})

$dialogResult = $form.ShowDialog()
if ($dialogResult -eq [System.Windows.Forms.DialogResult]::OK -and $script:TicketDialogResult -ne $null) {
    $script:TicketDialogResult | ConvertTo-Json -Compress -Depth 5 | Write-Output
}
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

// prepareTicketBrandIcon downloads the same branded .ico used by the running
// system tray icon so the Submit Ticket dialog matches /admin/tray/branding.
// The returned cleanup function removes the temporary icon file after the
// PowerShell dialog process has exited.
func prepareTicketBrandIcon(cfg *api.ConfigResponse) (string, func()) {
	if gPortalURL == "" {
		return "", nil
	}
	iconURL := strings.TrimRight(gPortalURL, "/") + "/tray/icon.ico"
	if cfg != nil && strings.TrimSpace(cfg.BrandingIconURL) != "" {
		configuredURL := strings.TrimSpace(cfg.BrandingIconURL)
		if strings.HasPrefix(configuredURL, "http://") || strings.HasPrefix(configuredURL, "https://") {
			iconURL = configuredURL
		} else if strings.HasPrefix(configuredURL, "/") {
			iconURL = strings.TrimRight(gPortalURL, "/") + configuredURL
		}
	}

	req, err := newHTTPRequest("GET", iconURL, nil)
	if err != nil {
		logger.Debug("prepareTicketBrandIcon: build request: %v", err)
		return "", nil
	}
	resp, err := ticketHTTPClient.Do(req)
	if err != nil {
		logger.Debug("prepareTicketBrandIcon: fetch %s: %v", iconURL, err)
		return "", nil
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		logger.Debug("prepareTicketBrandIcon: fetch %s returned HTTP %d", iconURL, resp.StatusCode)
		return "", nil
	}
	data, err := io.ReadAll(io.LimitReader(resp.Body, 1024*1024))
	if err != nil {
		logger.Debug("prepareTicketBrandIcon: read response: %v", err)
		return "", nil
	}
	if len(data) < 4 || data[0] != 0x00 || data[1] != 0x00 || data[2] != 0x01 || data[3] != 0x00 {
		logger.Debug("prepareTicketBrandIcon: response was not valid ICO data")
		return "", nil
	}
	f, err := os.CreateTemp("", "mp-ticket-brand-*.ico")
	if err != nil {
		logger.Debug("prepareTicketBrandIcon: create temp icon: %v", err)
		return "", nil
	}
	path := f.Name()
	if _, err := f.Write(data); err != nil {
		f.Close()
		os.Remove(path)
		logger.Debug("prepareTicketBrandIcon: write temp icon: %v", err)
		return "", nil
	}
	if err := f.Close(); err != nil {
		os.Remove(path)
		logger.Debug("prepareTicketBrandIcon: close temp icon: %v", err)
		return "", nil
	}
	return path, func() { _ = os.Remove(path) }
}

// openNewTicketDialog loads dynamic questions from the portal, shows the New
// Ticket WinForms dialog, persists prefill values to HKCU, and submits the
// ticket to the portal.
func openNewTicketDialog(cfg *api.ConfigResponse) {
	openTicketDialogWithEndpoint(cfg, "/api/tray/submit-ticket", "Submit Ticket")
}

func openSyncroTicketDialog(cfg *api.ConfigResponse) {
	openTicketDialogWithEndpoint(cfg, "/api/tray/submit-syncro-ticket", "Create Syncro Ticket")
}

func openTicketDialogWithEndpoint(cfg *api.ConfigResponse, submitPath string, notificationTitle string) {
	if gPortalURL == "" {
		showOSNotification(notificationTitle, "Portal connection not yet established. Please try again shortly.")
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
	brandIconPath, brandIconCleanup := prepareTicketBrandIcon(cfg)
	if brandIconCleanup != nil {
		defer brandIconCleanup()
	}

	cmd := newTicketDialogCommand(scriptPath)
	cmd.Env = append(os.Environ(),
		"MP_PREFILL_NAME="+prefillName,
		"MP_PREFILL_EMAIL="+prefillEmail,
		"MP_PREFILL_PHONE="+prefillPhone,
		"MP_BRAND_ICON_PATH="+brandIconPath,
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
		showOSNotification(notificationTitle, "Name, email and subject are required.")
		return
	}

	submission, err := submitTicketToPortal(result, submitPath)
	if err != nil {
		logger.Warn("openNewTicketDialog: submit: %v", err)
		showOSNotification(notificationTitle, err.Error())
		return
	}

	showOSNotification(notificationTitle, submission.SuccessMessage())
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

type ticketSubmissionResponse struct {
	TicketID     int    `json:"ticket_id"`
	TicketNumber string `json:"ticket_number"`
}

func (r ticketSubmissionResponse) SuccessMessage() string {
	ticketNumber := strings.TrimSpace(r.TicketNumber)
	if ticketNumber != "" {
		return fmt.Sprintf("Your ticket %s has been submitted. We will be in touch soon.", ticketNumber)
	}
	if r.TicketID > 0 {
		return fmt.Sprintf("Your ticket #%d has been submitted. We will be in touch soon.", r.TicketID)
	}
	return "Your ticket has been submitted. We will be in touch soon."
}

type apiErrorResponse struct {
	Detail any `json:"detail"`
	Error  any `json:"error"`
}

func friendlyTicketSubmitError(statusCode int, body []byte) error {
	message := extractAPIErrorMessage(body)
	if message != "" {
		return fmt.Errorf("Ticket submission failed: %s", message)
	}
	switch statusCode {
	case http.StatusUnauthorized, http.StatusForbidden:
		return fmt.Errorf("Ticket submission failed because this device is not authorised. Please contact support.")
	case http.StatusNotFound:
		return fmt.Errorf("Ticket submission failed because this device is not registered. Please contact support.")
	case http.StatusUnprocessableEntity, http.StatusBadRequest:
		return fmt.Errorf("Ticket submission failed because some required information is missing or invalid. Please review the form and try again.")
	case http.StatusBadGateway, http.StatusServiceUnavailable, http.StatusGatewayTimeout:
		return fmt.Errorf("Ticket submission failed because the ticket system is temporarily unavailable. Please try again shortly.")
	default:
		return fmt.Errorf("Ticket submission failed. Please try again shortly or contact support.")
	}
}

func extractAPIErrorMessage(body []byte) string {
	trimmed := strings.TrimSpace(string(body))
	if trimmed == "" {
		return ""
	}
	var payload apiErrorResponse
	if err := json.Unmarshal(body, &payload); err == nil {
		for _, candidate := range []any{payload.Detail, payload.Error} {
			if msg := stringifyAPIErrorValue(candidate); msg != "" {
				return msg
			}
		}
	}
	return ""
}

func stringifyAPIErrorValue(value any) string {
	switch v := value.(type) {
	case string:
		return strings.TrimSpace(v)
	case []any:
		parts := make([]string, 0, len(v))
		for _, item := range v {
			if msg := stringifyAPIErrorValue(item); msg != "" {
				parts = append(parts, msg)
			}
		}
		return strings.Join(parts, "; ")
	case map[string]any:
		for _, key := range []string{"msg", "message", "detail", "error"} {
			if msg := stringifyAPIErrorValue(v[key]); msg != "" {
				return msg
			}
		}
	}
	return ""
}

// submitTicketToPortal posts the ticket to the requested tray ticket endpoint.
func submitTicketToPortal(result ticketFormResult, submitPaths ...string) (ticketSubmissionResponse, error) {
	submitPath := "/api/tray/submit-ticket"
	if len(submitPaths) > 0 {
		submitPath = submitPaths[0]
	}
	type submitAnswer struct {
		QuestionID int    `json:"question_id"`
		Value      string `json:"value"`
	}
	type submitRequest struct {
		DeviceUID   string         `json:"device_uid,omitempty"`
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
	if deviceUID == "" && strings.TrimSpace(gAuthToken) == "" {
		return ticketSubmissionResponse{}, fmt.Errorf("Ticket submission failed because the device is not ready yet. Please try again shortly.")
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
		return ticketSubmissionResponse{}, fmt.Errorf("Ticket submission failed before it could be sent. Please try again.")
	}

	if strings.TrimSpace(submitPath) == "" {
		submitPath = "/api/tray/submit-ticket"
	}
	if !strings.HasPrefix(submitPath, "/") {
		submitPath = "/" + submitPath
	}
	url := strings.TrimRight(gPortalURL, "/") + submitPath
	req, err := http.NewRequest(http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return ticketSubmissionResponse{}, fmt.Errorf("Ticket submission failed before it could be sent. Please try again.")
	}
	req.Header.Set("Content-Type", "application/json")
	if gAuthToken != "" {
		req.Header.Set("Authorization", "Bearer "+gAuthToken)
	}
	resp, err := ticketHTTPClient.Do(req)
	if err != nil {
		return ticketSubmissionResponse{}, fmt.Errorf("Ticket submission failed because the portal could not be reached. Please check your connection and try again.")
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return ticketSubmissionResponse{}, friendlyTicketSubmitError(resp.StatusCode, b)
	}
	var submission ticketSubmissionResponse
	if err := json.NewDecoder(resp.Body).Decode(&submission); err != nil && err != io.EOF {
		logger.Debug("submitTicketToPortal: could not decode success response: %v", err)
	}
	logger.Info("Tray ticket submitted (HTTP %d)", resp.StatusCode)
	return submission, nil
}
