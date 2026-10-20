package runner

import "errors"

// Who a job runs as. MyPortal sends RunAsSystem (the agent's own account:
// LocalSystem on Windows, root on macOS and Linux) unless a technician chose
// the logged-in user.
const (
	RunAsSystem = "system"
	RunAsUser   = "user"
)

// ErrNoLoggedInUser means nobody is signed in to the device, so a job that
// must run as the logged-in user cannot start.
var ErrNoLoggedInUser = errors.New("no user is logged in")

func userError(err error) string {
	if errors.Is(err, ErrNoLoggedInUser) {
		return "No user is logged in on this device, so the script was not run as the logged-in user."
	}
	return "Could not run the script as the logged-in user: " + err.Error()
}
