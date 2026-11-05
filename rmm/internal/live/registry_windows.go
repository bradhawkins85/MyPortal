package live

import (
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"unicode/utf16"

	"golang.org/x/sys/windows/registry"
)

const (
	registrySupported = true
	maxRegistryItems  = 10000
	maxValueBytes     = 64 << 10
	maxDeleteDepth    = 64
)

// registryHives are the roots the editor opens. The agent runs as SYSTEM,
// so HKCU is SYSTEM's own profile; users' hives are under HKU.
var registryHives = []string{"HKLM", "HKU", "HKCR", "HKCC", "HKCU"}

var hiveKeys = map[string]registry.Key{
	"HKLM": registry.LOCAL_MACHINE, "HKEY_LOCAL_MACHINE": registry.LOCAL_MACHINE,
	"HKU": registry.USERS, "HKEY_USERS": registry.USERS,
	"HKCR": registry.CLASSES_ROOT, "HKEY_CLASSES_ROOT": registry.CLASSES_ROOT,
	"HKCC": registry.CURRENT_CONFIG, "HKEY_CURRENT_CONFIG": registry.CURRENT_CONFIG,
	"HKCU": registry.CURRENT_USER, "HKEY_CURRENT_USER": registry.CURRENT_USER,
}

var shortHive = map[string]string{
	"HKEY_LOCAL_MACHINE": "HKLM", "HKEY_USERS": "HKU", "HKEY_CLASSES_ROOT": "HKCR",
	"HKEY_CURRENT_CONFIG": "HKCC", "HKEY_CURRENT_USER": "HKCU",
}

var typeNames = map[uint32]string{
	registry.NONE: "REG_NONE", registry.SZ: "REG_SZ", registry.EXPAND_SZ: "REG_EXPAND_SZ",
	registry.BINARY: "REG_BINARY", registry.DWORD: "REG_DWORD", registry.DWORD_BIG_ENDIAN: "REG_DWORD_BIG_ENDIAN",
	registry.LINK: "REG_LINK", registry.MULTI_SZ: "REG_MULTI_SZ", registry.RESOURCE_LIST: "REG_RESOURCE_LIST",
	registry.FULL_RESOURCE_DESCRIPTOR:   "REG_FULL_RESOURCE_DESCRIPTOR",
	registry.RESOURCE_REQUIREMENTS_LIST: "REG_RESOURCE_REQUIREMENTS_LIST", registry.QWORD: "REG_QWORD",
}

type regPath struct {
	root registry.Key
	hive string
	sub  string
}

func (p regPath) String() string {
	if p.sub == "" {
		return p.hive
	}
	return p.hive + `\` + p.sub
}

func (p regPath) depth() int {
	if p.sub == "" {
		return 0
	}
	return len(strings.Split(p.sub, `\`))
}

func parseRegPath(raw string) (regPath, error) {
	raw = strings.Trim(strings.ReplaceAll(strings.TrimSpace(raw), "/", `\`), `\`)
	hive, sub, _ := strings.Cut(raw, `\`)
	hive = strings.ToUpper(hive)
	root, ok := hiveKeys[hive]
	if !ok {
		return regPath{}, fmt.Errorf("%q is not a registry hive (use HKLM, HKU, HKCR, HKCC or HKCU)", hive)
	}
	if short, ok := shortHive[hive]; ok {
		hive = short
	}
	var parts []string
	for _, part := range strings.Split(sub, `\`) {
		if part != "" {
			parts = append(parts, part)
		}
	}
	return regPath{root: root, hive: hive, sub: strings.Join(parts, `\`)}, nil
}

func (p regPath) open(access uint32) (registry.Key, error) {
	return registry.OpenKey(p.root, p.sub, access|registry.WOW64_64KEY)
}

// RegValue is one value of a key.
type RegValue struct {
	Name      string      `json:"name"`
	Type      string      `json:"type"`
	Data      interface{} `json:"data"`
	Size      int         `json:"size"`
	Truncated bool        `json:"truncated,omitempty"`
}

func readValue(key registry.Key, name string) RegValue {
	value := RegValue{Name: name, Type: "REG_NONE"}
	size, kind, err := key.GetValue(name, nil)
	if err != nil {
		value.Data = "(unreadable: " + err.Error() + ")"
		return value
	}
	value.Size = size
	if typeName, ok := typeNames[kind]; ok {
		value.Type = typeName
	}
	buf := make([]byte, size)
	if size > 0 {
		if _, _, err := key.GetValue(name, buf); err != nil {
			value.Data = "(unreadable: " + err.Error() + ")"
			return value
		}
	}
	switch kind {
	case registry.SZ, registry.EXPAND_SZ:
		value.Data = utf16Bytes(buf)
	case registry.MULTI_SZ:
		var items []string
		for _, item := range strings.Split(utf16Bytes(buf), "\x00") {
			if item != "" {
				items = append(items, item)
			}
		}
		if items == nil {
			items = []string{}
		}
		value.Data = items
	case registry.DWORD:
		if len(buf) >= 4 {
			value.Data = strconv.FormatUint(uint64(binary.LittleEndian.Uint32(buf)), 10)
		}
	case registry.DWORD_BIG_ENDIAN:
		if len(buf) >= 4 {
			value.Data = strconv.FormatUint(uint64(binary.BigEndian.Uint32(buf)), 10)
		}
	case registry.QWORD:
		if len(buf) >= 8 {
			value.Data = strconv.FormatUint(binary.LittleEndian.Uint64(buf), 10)
		}
	default:
		if len(buf) > maxValueBytes {
			buf = buf[:maxValueBytes]
			value.Truncated = true
		}
		value.Data = hex.EncodeToString(buf)
	}
	return value
}

func utf16Bytes(buf []byte) string {
	words := make([]uint16, len(buf)/2)
	for i := range words {
		words[i] = binary.LittleEndian.Uint16(buf[i*2:])
	}
	text := string(utf16.Decode(words))
	return strings.TrimRight(text, "\x00")
}

type registryArgs struct {
	Path      string          `json:"path"`
	Name      string          `json:"name"`
	Type      string          `json:"type"`
	Data      json.RawMessage `json:"data"`
	Recursive bool            `json:"recursive"`
}

func parseNumber(raw json.RawMessage, bits int) (uint64, error) {
	var text string
	if err := json.Unmarshal(raw, &text); err != nil {
		var number json.Number
		if err := json.Unmarshal(raw, &number); err != nil {
			return 0, errors.New("enter a number")
		}
		text = number.String()
	}
	text = strings.TrimSpace(text)
	base := 10
	if lower := strings.ToLower(text); strings.HasPrefix(lower, "0x") {
		text, base = text[2:], 16
	}
	value, err := strconv.ParseUint(text, base, bits)
	if err != nil {
		return 0, fmt.Errorf("enter a whole number that fits in %d bits (decimal or 0x hexadecimal)", bits)
	}
	return value, nil
}

func setValue(key registry.Key, args registryArgs) error {
	switch args.Type {
	case "REG_SZ", "REG_EXPAND_SZ":
		var text string
		if err := json.Unmarshal(args.Data, &text); err != nil {
			return errors.New("enter text for a string value")
		}
		if args.Type == "REG_SZ" {
			return key.SetStringValue(args.Name, text)
		}
		return key.SetExpandStringValue(args.Name, text)
	case "REG_MULTI_SZ":
		var items []string
		if err := json.Unmarshal(args.Data, &items); err != nil {
			return errors.New("enter a list of strings")
		}
		for _, item := range items {
			if item == "" {
				return errors.New("multi-string values cannot contain empty lines")
			}
		}
		return key.SetStringsValue(args.Name, items)
	case "REG_DWORD":
		value, err := parseNumber(args.Data, 32)
		if err != nil {
			return err
		}
		return key.SetDWordValue(args.Name, uint32(value))
	case "REG_QWORD":
		value, err := parseNumber(args.Data, 64)
		if err != nil {
			return err
		}
		return key.SetQWordValue(args.Name, value)
	case "REG_BINARY":
		var text string
		if err := json.Unmarshal(args.Data, &text); err != nil {
			return errors.New("enter binary data as hexadecimal")
		}
		data, err := hex.DecodeString(strings.NewReplacer(" ", "", ",", "", "\n", "").Replace(text))
		if err != nil {
			return errors.New("enter binary data as hexadecimal, such as 01 ff 2a")
		}
		return key.SetBinaryValue(args.Name, data)
	}
	return fmt.Errorf("%s values cannot be edited here", args.Type)
}

func deleteKeyTree(parent registry.Key, name string, depth int) error {
	if depth > maxDeleteDepth {
		return errors.New("the key is nested too deeply to delete here")
	}
	key, err := registry.OpenKey(parent, name, registry.ENUMERATE_SUB_KEYS|registry.WOW64_64KEY)
	if err != nil {
		return err
	}
	children, err := key.ReadSubKeyNames(-1)
	if err == nil {
		for _, child := range children {
			if err := deleteKeyTree(key, child, depth+1); err != nil {
				key.Close()
				return err
			}
		}
	}
	key.Close()
	return registry.DeleteKey(parent, name)
}

func init() {
	register("registry.list", func(s *Session, req Request) (interface{}, error) {
		var args registryArgs
		if err := decode(req, &args); err != nil {
			return nil, err
		}
		if strings.TrimSpace(args.Path) == "" {
			return map[string]interface{}{"path": "", "keys": registryHives, "values": []RegValue{}}, nil
		}
		path, err := parseRegPath(args.Path)
		if err != nil {
			return nil, err
		}
		key, err := path.open(registry.ENUMERATE_SUB_KEYS | registry.QUERY_VALUE)
		if err != nil {
			return nil, fmt.Errorf("open %s: %w", path, err)
		}
		defer key.Close()
		keys, err := key.ReadSubKeyNames(-1)
		if err != nil {
			keys = []string{}
		}
		truncated := false
		if len(keys) > maxRegistryItems {
			keys, truncated = keys[:maxRegistryItems], true
		}
		names, err := key.ReadValueNames(-1)
		if err != nil {
			names = nil
		}
		if len(names) > maxRegistryItems {
			names, truncated = names[:maxRegistryItems], true
		}
		values := make([]RegValue, 0, len(names))
		for _, name := range names {
			values = append(values, readValue(key, name))
		}
		parent := ""
		if path.sub != "" {
			if i := strings.LastIndex(path.sub, `\`); i >= 0 {
				parent = path.hive + `\` + path.sub[:i]
			} else {
				parent = path.hive
			}
		}
		return map[string]interface{}{
			"path": path.String(), "parent": parent, "keys": keys, "values": values, "truncated": truncated,
		}, nil
	})
	register("registry.set", func(s *Session, req Request) (interface{}, error) {
		var args registryArgs
		if err := decode(req, &args); err != nil {
			return nil, err
		}
		path, err := parseRegPath(args.Path)
		if err != nil {
			return nil, err
		}
		if path.sub == "" {
			return nil, errors.New("values cannot be set on a hive root")
		}
		key, err := path.open(registry.SET_VALUE | registry.QUERY_VALUE)
		if err != nil {
			return nil, fmt.Errorf("open %s: %w", path, err)
		}
		defer key.Close()
		if err := setValue(key, args); err != nil {
			return nil, err
		}
		return readValue(key, args.Name), nil
	})
	register("registry.delete_value", func(s *Session, req Request) (interface{}, error) {
		var args registryArgs
		if err := decode(req, &args); err != nil {
			return nil, err
		}
		path, err := parseRegPath(args.Path)
		if err != nil {
			return nil, err
		}
		key, err := path.open(registry.SET_VALUE)
		if err != nil {
			return nil, fmt.Errorf("open %s: %w", path, err)
		}
		defer key.Close()
		return nil, key.DeleteValue(args.Name)
	})
	register("registry.create_key", func(s *Session, req Request) (interface{}, error) {
		var args registryArgs
		if err := decode(req, &args); err != nil {
			return nil, err
		}
		path, err := parseRegPath(args.Path)
		if err != nil {
			return nil, err
		}
		if path.sub == "" {
			return nil, errors.New("enter the key to create under a hive")
		}
		key, existed, err := registry.CreateKey(path.root, path.sub, registry.QUERY_VALUE|registry.WOW64_64KEY)
		if err != nil {
			return nil, err
		}
		key.Close()
		if existed {
			return nil, fmt.Errorf("%s already exists", path)
		}
		return map[string]string{"path": path.String()}, nil
	})
	register("registry.delete_key", func(s *Session, req Request) (interface{}, error) {
		var args registryArgs
		if err := decode(req, &args); err != nil {
			return nil, err
		}
		path, err := parseRegPath(args.Path)
		if err != nil {
			return nil, err
		}
		// Top-level keys such as HKLM\SOFTWARE hold the whole system.
		if path.depth() < 2 {
			return nil, fmt.Errorf("%s is too close to the hive root to delete here", path)
		}
		parentPath, name := path.sub[:strings.LastIndex(path.sub, `\`)], path.sub[strings.LastIndex(path.sub, `\`)+1:]
		parent, err := registry.OpenKey(path.root, parentPath, registry.ENUMERATE_SUB_KEYS|registry.WOW64_64KEY)
		if err != nil {
			return nil, err
		}
		defer parent.Close()
		if args.Recursive {
			err = deleteKeyTree(parent, name, 0)
		} else {
			err = registry.DeleteKey(parent, name)
		}
		if err != nil {
			return nil, err
		}
		return map[string]string{"path": path.String()}, nil
	})
}
