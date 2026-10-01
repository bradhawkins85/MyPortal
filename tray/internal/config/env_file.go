package config

import (
	"bufio"
	"io"
	"strings"
)

func parseEnvConfig(r io.Reader) (*Config, error) {
	vals := make(map[string]string)
	scanner := bufio.NewScanner(r)
	for scanner.Scan() {
		line := strings.TrimSpace(strings.TrimSuffix(scanner.Text(), "\r"))
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		line = strings.TrimSpace(strings.TrimPrefix(line, "export "))
		parts := strings.SplitN(line, "=", 2)
		if len(parts) != 2 {
			continue
		}
		key := strings.TrimSpace(parts[0])
		value := strings.TrimSpace(parts[1])
		value = strings.Trim(value, `"'`)
		vals[key] = value
	}
	if err := scanner.Err(); err != nil {
		return nil, err
	}

	au := true
	if strings.ToLower(vals["AUTO_UPDATE"]) == "false" {
		au = false
	}
	return &Config{
		PortalURL:  vals["MYPORTAL_URL"],
		EnrolToken: vals["ENROL_TOKEN"],
		AutoUpdate: au,
	}, nil
}
