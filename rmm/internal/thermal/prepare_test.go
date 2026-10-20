package thermal

import (
	"context"
	"errors"
	"testing"
	"time"
)

func TestUninstallArgs(t *testing.T) {
	for _, tc := range []struct{ command, program, args string }{
		{`"C:\Program Files\PawnIO\PawnIO_setup.exe" -uninstall`, `C:\Program Files\PawnIO\PawnIO_setup.exe`, "-uninstall -silent"},
		{`"C:\Program Files\PawnIO\PawnIO_setup.exe"`, `C:\Program Files\PawnIO\PawnIO_setup.exe`, "-uninstall -silent"},
		{`"C:\Program Files\PawnIO\PawnIO_setup.exe" -uninstall -silent`, `C:\Program Files\PawnIO\PawnIO_setup.exe`, "-uninstall -silent"},
		{`C:\PawnIO\PawnIO_setup.EXE -Uninstall`, `C:\PawnIO\PawnIO_setup.EXE`, "-Uninstall -silent"},
		{`  C:\PawnIO\setup.exe  `, `C:\PawnIO\setup.exe`, "-uninstall -silent"},
	} {
		program, args, err := uninstallArgs(tc.command)
		if err != nil || program != tc.program || args != tc.args {
			t.Errorf("%q: got %q %q %v", tc.command, program, args, err)
		}
	}
	for _, command := range []string{"", `"C:\PawnIO\setup.exe -uninstall`, `""`, "msiexec /x {GUID}"} {
		if _, _, err := uninstallArgs(command); err == nil {
			t.Errorf("%q accepted", command)
		}
	}
}

func TestRemovePawnIO(t *testing.T) {
	const command = `"C:\Program Files\PawnIO\PawnIO_setup.exe" -uninstall`
	t.Run("not installed", func(t *testing.T) {
		err := removePawnIO(context.Background(), func() (string, error) { return "", nil },
			func(context.Context, string, string) error {
				t.Fatal("uninstaller ran without PawnIO installed")
				return nil
			})
		if err != nil {
			t.Fatal(err)
		}
	})
	t.Run("installed", func(t *testing.T) {
		registered := command
		calls := 0
		err := removePawnIO(context.Background(), func() (string, error) { return registered, nil },
			func(ctx context.Context, program, args string) error {
				calls++
				deadline, ok := ctx.Deadline()
				if !ok || time.Until(deadline) > 2*time.Minute {
					t.Fatal("uninstaller is not bounded to two minutes")
				}
				if program != `C:\Program Files\PawnIO\PawnIO_setup.exe` || args != "-uninstall -silent" {
					t.Fatalf("ran %q %q", program, args)
				}
				registered = ""
				return nil
			})
		if err != nil || calls != 1 {
			t.Fatalf("calls=%d error=%v", calls, err)
		}
	})
	t.Run("still registered", func(t *testing.T) {
		err := removePawnIO(context.Background(), func() (string, error) { return command, nil },
			func(context.Context, string, string) error { return nil })
		if err == nil {
			t.Fatal("removal accepted while PawnIO is still registered")
		}
	})
	t.Run("failures", func(t *testing.T) {
		readError := errors.New("registry access denied")
		if err := removePawnIO(context.Background(), func() (string, error) { return "", readError },
			nil); !errors.Is(err, readError) {
			t.Fatalf("registry error lost: %v", err)
		}
		runError := errors.New("uninstaller failed")
		if err := removePawnIO(context.Background(), func() (string, error) { return command, nil },
			func(context.Context, string, string) error { return runError }); !errors.Is(err, runError) {
			t.Fatalf("uninstaller error lost: %v", err)
		}
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		if err := removePawnIO(ctx, nil, nil); !errors.Is(err, context.Canceled) {
			t.Fatalf("cancellation lost: %v", err)
		}
	})
}
