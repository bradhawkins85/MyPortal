package healthcheck

import (
	"strings"
	"testing"
)

const sampleDiskInfo = `----------------------------------------------------------------------------
CrystalDiskInfo 9.4.4 (C) 2008-2024 hiyohiyo
                                Crystal Dew World: https://crystalmark.info/
----------------------------------------------------------------------------

    OS : Windows 11 Pro 24H2 [10.0 Build 26100] (x64)
  Date : 2026/10/26 9:00:00

-- Controller Map ----------------------------------------------------------
 + Standard SATA AHCI Controller [ATA]
   - Crucial_CT256MX100SSD1
 + Standard NVM Express Controller [SCSI]
   - Samsung SSD 980 PRO 1TB

-- Disk List ---------------------------------------------------------------
 (01) Crucial_CT256MX100SSD1 : 256.0 GB [0/0/0, pd1] - cr
 (02) Samsung SSD 980 PRO 1TB : 1000.2 GB [1/0/0, sq] - nv

----------------------------------------------------------------------------
 (01) Crucial_CT256MX100SSD1
----------------------------------------------------------------------------
           Model : Crucial_CT256MX100SSD1
        Firmware : MU03
   Serial Number : 14450E1C1A2B
       Disk Size : 256.0 GB (8.4/137.4/256.0/256.0)
     Buffer Size : Unknown
    # of Sectors : 500118192
   Rotation Rate : ---- (SSD)
       Interface : Serial ATA
   Transfer Mode : SATA/600 | SATA/600
  Power On Hours : 41234 hours
  Power On Count : 3021 count
     Host Writes : 48211 GB
     Temperature : 38 C (100 F)
   Health Status : Caution (12 %)
        Features : S.M.A.R.T., 48bit LBA, NCQ, TRIM, DevSleep
    Drive Letter : C: E:

-- S.M.A.R.T. --------------------------------------------------------------
ID Cur Wor Thr RawValues(6) Attribute Name
01 100 100 __0 000000000000 Raw Read Error Rate
05 100 100 _10 000000000004 Reallocated NAND Blocks
09 100 100 __0 00000000A112 Power On Hours
AD _12 _12 __0 000000000B3C Average Block-Erase Count
C5 100 100 __0 000000000002 Current Pending Sector Count
CA _12 _12 __1 000000000058 Percentage Lifetime Used

-- IDENTIFY_DEVICE ---------------------------------------------------------
        0    1    2    3    4    5    6    7    8    9
000: 0040 3FFF C837 0010 0000 0000 003F 0000 0000 0000

----------------------------------------------------------------------------
 (02) Samsung SSD 980 PRO 1TB
----------------------------------------------------------------------------
           Model : Samsung SSD 980 PRO 1TB
        Firmware : 5B2QGXA7
       Disk Size : 1000.2 GB
       Interface : NVM Express
  Power On Hours : 812 hours
     Temperature : 45 C (113 F)
   Health Status : Good (99 %)
    Drive Letter : D:

-- S.M.A.R.T. --------------------------------------------------------------
ID RawValues(6) Attribute Name
01 000000000000 Critical Warning
03 000000000064 Available Spare
04 00000000000A Available Spare Threshold
05 000000000001 Percentage Used
0E 000000000000 Media and Data Integrity Errors
`

func TestParseDiskInfoReadsDrivesAndAttributes(t *testing.T) {
	version, disks := parseDiskInfo(strings.ReplaceAll(sampleDiskInfo, "\n", "\r\n"))
	if version != "9.4.4" || len(disks) != 2 {
		t.Fatalf("version %q, %d disks: %+v", version, len(disks), disks)
	}
	sata := disks[0]
	if sata.Model != "Crucial_CT256MX100SSD1" || sata.MediaType != "SSD" || sata.NVMe || sata.Firmware != "MU03" ||
		sata.HealthStatus != "Caution" || *sata.LifePercent != 12 || *sata.PowerOnHours != 41234 ||
		*sata.TemperatureC != 38 || *sata.HostWritesGB != 48211 || *sata.SizeGB != 256 ||
		strings.Join(sata.DriveLetters, " ") != "C: E:" {
		t.Fatalf("sata %+v", sata)
	}
	if len(sata.Attributes) != 6 {
		t.Fatalf("sata attributes %+v", sata.Attributes)
	}
	realloc := sata.Attributes[1]
	if realloc.ID != "05" || realloc.Name != "Reallocated NAND Blocks" || *realloc.Current != 100 ||
		*realloc.Threshold != 10 || *realloc.RawValue != 4 {
		t.Fatalf("reallocated %+v", realloc)
	}
	nvme := disks[1]
	if !nvme.NVMe || nvme.MediaType != "SSD" || nvme.HealthStatus != "Good" || *nvme.LifePercent != 99 ||
		len(nvme.Attributes) != 5 {
		t.Fatalf("nvme %+v", nvme)
	}
	spare := nvme.Attributes[1]
	if spare.ID != "03" || spare.Name != "Available Spare" || spare.Current != nil || *spare.RawValue != 100 {
		t.Fatalf("spare %+v", spare)
	}
}

func TestSmartReport(t *testing.T) {
	report := smartReport(diskInfoOutput{Status: SMARTAvailable, Installed: true, Text: sampleDiskInfo})
	if report.Status != SMARTAvailable || !report.Installed || report.Source != smartSource || len(report.Disks) != 2 {
		t.Fatalf("report %+v", report)
	}
	empty := smartReport(diskInfoOutput{Status: SMARTAvailable, Text: "CrystalDiskInfo 9.4.4"})
	if empty.Status != SMARTUnavailable || len(empty.Disks) != 0 {
		t.Fatalf("empty %+v", empty)
	}
	failed := smartReport(diskInfoOutput{Status: SMARTError, Detail: "Chocolatey could not be installed.", Text: sampleDiskInfo})
	if failed.Status != SMARTError || len(failed.Disks) != 0 || failed.Detail == "" {
		t.Fatalf("failed %+v", failed)
	}
}
