package live

import (
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"regexp"
)

const (
	maxTerminals   = 4
	terminalBuffer = 32 << 10
)

// terminal is a shell attached to a pseudo console.
type terminal interface {
	io.ReadWriter
	Resize(cols, rows int) error
	Wait() (int, error)
	Close()
}

type terminalArgs struct {
	Term  string `json:"term"`
	Shell string `json:"shell"`
	Cols  int    `json:"cols"`
	Rows  int    `json:"rows"`
	Data  string `json:"data"`
}

var terminalID = regexp.MustCompile(`^[A-Za-z0-9_-]{1,32}$`)

func clampSize(cols, rows int) (int, int) {
	if cols < 20 {
		cols = 80
	}
	if rows < 5 {
		rows = 24
	}
	return min(cols, 500), min(rows, 200)
}

func (s *Session) terminal(id string) (terminal, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	term, ok := s.terminals[id]
	if !ok {
		return nil, errors.New("this terminal has closed")
	}
	return term, nil
}

func (s *Session) pumpTerminal(id string, term terminal) {
	buf := make([]byte, terminalBuffer)
	for {
		n, err := term.Read(buf)
		if n > 0 {
			if !s.event("terminal.output", map[string]string{
				"term": id, "data": base64.StdEncoding.EncodeToString(buf[:n]),
			}) {
				break
			}
		}
		if err != nil {
			break
		}
	}
	code, err := term.Wait()
	s.mu.Lock()
	if s.terminals[id] == term {
		delete(s.terminals, id)
	}
	s.mu.Unlock()
	term.Close()
	exit := map[string]interface{}{"term": id, "code": code}
	if err != nil {
		exit["error"] = err.Error()
	}
	s.event("terminal.exit", exit)
}

func init() {
	register("terminal.open", func(s *Session, req Request) (interface{}, error) {
		var args terminalArgs
		if err := decode(req, &args); err != nil {
			return nil, err
		}
		if !terminalID.MatchString(args.Term) {
			return nil, errors.New("invalid terminal id")
		}
		shell := args.Shell
		if shell == "" {
			shells := terminalShells()
			if len(shells) == 0 {
				return nil, errors.New("no shell is available on this device")
			}
			shell = shells[0]
		}
		allowed := false
		for _, item := range terminalShells() {
			allowed = allowed || item == shell
		}
		if !allowed {
			return nil, fmt.Errorf("%s is not available on this device", shell)
		}
		s.mu.Lock()
		if _, exists := s.terminals[args.Term]; exists {
			s.mu.Unlock()
			return nil, errors.New("that terminal is already open")
		}
		if len(s.terminals) >= maxTerminals {
			s.mu.Unlock()
			return nil, fmt.Errorf("close a terminal first; at most %d can be open", maxTerminals)
		}
		s.mu.Unlock()
		cols, rows := clampSize(args.Cols, args.Rows)
		term, err := startTerminal(shell, cols, rows)
		if err != nil {
			return nil, err
		}
		s.mu.Lock()
		if s.ctx.Err() != nil {
			s.mu.Unlock()
			term.Close()
			return nil, errors.New("the session has ended")
		}
		s.terminals[args.Term] = term
		s.mu.Unlock()
		go s.pumpTerminal(args.Term, term)
		return map[string]interface{}{"term": args.Term, "shell": shell, "cols": cols, "rows": rows}, nil
	})
	register("terminal.input", func(s *Session, req Request) (interface{}, error) {
		var args terminalArgs
		if err := decode(req, &args); err != nil {
			return nil, err
		}
		term, err := s.terminal(args.Term)
		if err != nil {
			return nil, err
		}
		data, err := base64.StdEncoding.DecodeString(args.Data)
		if err != nil {
			return nil, errors.New("input was not valid base64")
		}
		if _, err := term.Write(data); err != nil {
			return nil, err
		}
		return nil, errNoReply
	})
	register("terminal.resize", func(s *Session, req Request) (interface{}, error) {
		var args terminalArgs
		if err := decode(req, &args); err != nil {
			return nil, err
		}
		term, err := s.terminal(args.Term)
		if err != nil {
			return nil, err
		}
		cols, rows := clampSize(args.Cols, args.Rows)
		if err := term.Resize(cols, rows); err != nil {
			return nil, err
		}
		return nil, errNoReply
	})
	register("terminal.close", func(s *Session, req Request) (interface{}, error) {
		var args terminalArgs
		if err := decode(req, &args); err != nil {
			return nil, err
		}
		s.mu.Lock()
		term, ok := s.terminals[args.Term]
		delete(s.terminals, args.Term)
		s.mu.Unlock()
		if ok {
			term.Close()
		}
		return nil, nil
	})
}
