package healthcheck

import (
	"regexp"
	"strconv"
	"strings"
)

// smartSource names the tool the drive SMART data comes from.
const smartSource = "CrystalDiskInfo"

// Drive SMART statuses; the server shows a sentence for each.
const (
	SMARTAvailable      = "available"
	SMARTError          = "error"
	SMARTUnavailable    = "unavailable"
	SMARTSkippedVirtual = "skipped_virtual"
)

// Caps on what is sent to the server.
const (
	maxSMARTDisks      = 32
	maxSMARTAttributes = 64
)

// DiskSMARTReport holds each drive's SMART data as CrystalDiskInfo reads it.
// Windows' reliability counters miss the wear and health figures many drives
// keep in vendor-specific attributes, so the server prefers these.
type DiskSMARTReport struct {
	Status string `json:"status"`
	Source string `json:"source"`
	// Version is CrystalDiskInfo's version.
	Version string `json:"version,omitempty"`
	// Installed is true when this check installed CrystalDiskInfo (it stays
	// installed).
	Installed bool        `json:"installed,omitempty"`
	Detail    string      `json:"detail,omitempty"`
	Disks     []SMARTDisk `json:"disks"`
}

// SMARTDisk is one drive in CrystalDiskInfo's report.
type SMARTDisk struct {
	Model        string   `json:"model"`
	Firmware     string   `json:"firmware,omitempty"`
	Interface    string   `json:"interface,omitempty"`
	MediaType    string   `json:"media_type,omitempty"` // SSD or HDD
	NVMe         bool     `json:"nvme,omitempty"`
	SizeGB       *float64 `json:"size_gb,omitempty"`
	DriveLetters []string `json:"drive_letters,omitempty"`
	// HealthStatus is CrystalDiskInfo's verdict: Good, Caution, Bad or Unknown.
	HealthStatus string `json:"health_status,omitempty"`
	// LifePercent is the remaining life CrystalDiskInfo reads from the
	// drive's own wear attributes (100 is new).
	LifePercent  *float64         `json:"life_percent,omitempty"`
	TemperatureC *float64         `json:"temperature_c,omitempty"`
	PowerOnHours *float64         `json:"power_on_hours,omitempty"`
	PowerOnCount *float64         `json:"power_on_count,omitempty"`
	HostWritesGB *float64         `json:"host_writes_gb,omitempty"`
	HostReadsGB  *float64         `json:"host_reads_gb,omitempty"`
	Attributes   []SMARTAttribute `json:"attributes"`
}

// SMARTAttribute is one row of a drive's SMART table. NVMe drives have no
// current, worst or threshold values.
type SMARTAttribute struct {
	ID        string  `json:"id"` // two hex digits, as CrystalDiskInfo prints it
	Name      string  `json:"name"`
	Current   *int    `json:"current,omitempty"`
	Worst     *int    `json:"worst,omitempty"`
	Threshold *int    `json:"threshold,omitempty"`
	Raw       string  `json:"raw"`
	RawValue  *uint64 `json:"raw_value,omitempty"`
}

var (
	diskInfoVersionRe = regexp.MustCompile(`CrystalDiskInfo\s+(\d[\w.\-]*)`)
	diskHeaderRe      = regexp.MustCompile(`^\s*\((\d+)\)\s+(\S.*?)\s*$`)
	diskFieldRe       = regexp.MustCompile(`^\s*([A-Za-z#][A-Za-z#. ]*?)\s+:\s?(.*?)\s*$`)
	leadingNumberRe   = regexp.MustCompile(`[-+]?\d+(?:\.\d+)?`)
	healthRe          = regexp.MustCompile(`^([A-Za-z]+)(?:\s*\(\s*(\d+(?:\.\d+)?)\s*%\s*\))?`)
	hexRe             = regexp.MustCompile(`^[0-9A-Fa-f]{2,}$`)
)

func isRule(line string) bool {
	trimmed := strings.TrimSpace(line)
	return len(trimmed) >= 10 && strings.Trim(trimmed, "-") == ""
}

// parseDiskInfo reads CrystalDiskInfo's DiskInfo.txt. Drives start with a
// "(NN) Model" heading between two rules; each has "Key : Value" lines and a
// "-- S.M.A.R.T." table.
func parseDiskInfo(text string) (string, []SMARTDisk) {
	version := ""
	if m := diskInfoVersionRe.FindStringSubmatch(text); m != nil {
		version = truncate(m[1], 40)
	}
	lines := strings.Split(strings.ReplaceAll(text, "\r\n", "\n"), "\n")
	disks := []SMARTDisk{}
	var current *SMARTDisk
	inSMART, ataTable := false, false
	for i := 0; i < len(lines); i++ {
		line := strings.TrimRight(lines[i], " \t\r")
		if i+2 < len(lines) && isRule(line) && isRule(lines[i+2]) {
			if m := diskHeaderRe.FindStringSubmatch(lines[i+1]); m != nil {
				if current != nil {
					disks = append(disks, *current)
				}
				if len(disks) == maxSMARTDisks {
					current = nil
					break
				}
				current = &SMARTDisk{Model: truncate(m[2], 120), Attributes: []SMARTAttribute{}}
				inSMART = false
				i += 2
				continue
			}
		}
		if current == nil {
			continue
		}
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "--") {
			inSMART = strings.HasPrefix(trimmed, "-- S.M.A.R.T.")
			ataTable = false
			continue
		}
		if inSMART {
			if trimmed == "" {
				continue
			}
			fields := strings.Fields(trimmed)
			if fields[0] == "ID" {
				ataTable = len(fields) > 1 && fields[1] == "Cur"
				current.NVMe = !ataTable
				continue
			}
			if attr, ok := parseSMARTRow(fields, ataTable); ok && len(current.Attributes) < maxSMARTAttributes {
				current.Attributes = append(current.Attributes, attr)
			}
			continue
		}
		if m := diskFieldRe.FindStringSubmatch(line); m != nil {
			applyDiskField(current, m[1], m[2])
		}
	}
	if current != nil {
		disks = append(disks, *current)
	}
	for i := range disks {
		disk := &disks[i]
		if disk.NVMe || strings.Contains(strings.ToUpper(disk.Interface), "NVM") {
			disk.NVMe = true
			disk.MediaType = "SSD"
		}
	}
	return version, disks
}

func applyDiskField(disk *SMARTDisk, key, value string) {
	value = strings.TrimSpace(value)
	switch key {
	case "Model":
		if value != "" {
			disk.Model = truncate(value, 120)
		}
	case "Firmware":
		disk.Firmware = truncate(value, 40)
	case "Interface":
		disk.Interface = truncate(value, 60)
	case "Disk Size":
		disk.SizeGB = leadingNumber(value)
	case "Rotation Rate":
		switch {
		case strings.Contains(value, "SSD"):
			disk.MediaType = "SSD"
		case strings.Contains(strings.ToUpper(value), "RPM"):
			disk.MediaType = "HDD"
		}
	case "Power On Hours":
		disk.PowerOnHours = leadingNumber(value)
	case "Power On Count":
		disk.PowerOnCount = leadingNumber(value)
	case "Host Writes":
		disk.HostWritesGB = leadingNumber(value)
	case "Host Reads":
		disk.HostReadsGB = leadingNumber(value)
	case "Temperature":
		disk.TemperatureC = leadingNumber(value)
	case "Health Status":
		if m := healthRe.FindStringSubmatch(value); m != nil {
			disk.HealthStatus = truncate(m[1], 20)
			if m[2] != "" {
				disk.LifePercent = leadingNumber(m[2])
			}
		}
	case "Drive Letter":
		letters := []string{}
		for _, letter := range strings.Fields(value) {
			if len(letter) <= 3 && strings.HasSuffix(letter, ":") {
				letters = append(letters, letter)
			}
		}
		if len(letters) > 0 {
			disk.DriveLetters = letters
		}
	}
}

// parseSMARTRow reads "05 100 100 _10 000000000000 Reallocated Sectors Count"
// (ATA) or "01 000000000000 Critical Warning" (NVMe). CrystalDiskInfo pads
// numbers with underscores to keep its columns.
func parseSMARTRow(fields []string, ata bool) (SMARTAttribute, bool) {
	rawAt := 1
	if ata {
		rawAt = 4
	}
	if len(fields) <= rawAt+1 || len(fields[0]) != 2 || !hexRe.MatchString(fields[0]) || !hexRe.MatchString(fields[rawAt]) {
		return SMARTAttribute{}, false
	}
	attr := SMARTAttribute{
		ID:   strings.ToUpper(fields[0]),
		Name: truncate(strings.Join(fields[rawAt+1:], " "), 80),
		Raw:  truncate(strings.ToUpper(fields[rawAt]), 32),
	}
	if ata {
		attr.Current, attr.Worst, attr.Threshold = paddedInt(fields[1]), paddedInt(fields[2]), paddedInt(fields[3])
	}
	if value, err := strconv.ParseUint(fields[rawAt], 16, 64); err == nil {
		attr.RawValue = &value
	}
	return attr, true
}

func paddedInt(field string) *int {
	value, err := strconv.Atoi(strings.TrimLeft(field, "_"))
	if err != nil {
		return nil
	}
	return &value
}

func leadingNumber(value string) *float64 {
	match := leadingNumberRe.FindString(value)
	if match == "" {
		return nil
	}
	number, err := strconv.ParseFloat(match, 64)
	if err != nil {
		return nil
	}
	return &number
}

// diskInfoOutput is what diskinfo_windows.ps1 prints.
type diskInfoOutput struct {
	Status    string `json:"status"`
	Installed bool   `json:"installed"`
	Detail    string `json:"detail"`
	Text      string `json:"text"`
}

// smartReport turns the script's output into the report sent to the server.
func smartReport(out diskInfoOutput) DiskSMARTReport {
	report := DiskSMARTReport{
		Status:    out.Status,
		Source:    smartSource,
		Installed: out.Installed,
		Detail:    truncate(out.Detail, 300),
		Disks:     []SMARTDisk{},
	}
	if report.Status == "" {
		report.Status = SMARTError
	}
	if out.Status != SMARTAvailable {
		return report
	}
	report.Version, report.Disks = parseDiskInfo(out.Text)
	if len(report.Disks) == 0 {
		report.Status = SMARTUnavailable
		report.Detail = "CrystalDiskInfo found no drives with SMART data."
	}
	return report
}
