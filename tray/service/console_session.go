package main

// consoleSession describes one logon session seen on the device.
type consoleSession struct {
	ID     uint32
	Active bool // the user is attached to it now (not disconnected)
	User   string
}

// pickConsoleUser returns the user of the session in use now. A session left
// behind by fast user switching or a dropped remote desktop connection is
// disconnected and never chosen. The physical console wins when it is active;
// otherwise the active remote session with the lowest ID is used. It returns
// "" when nobody is signed in.
func pickConsoleUser(sessions []consoleSession, consoleID uint32) string {
	best := ""
	var bestID uint32
	for _, s := range sessions {
		if !s.Active || s.User == "" {
			continue
		}
		if s.ID == consoleID {
			return s.User
		}
		if best == "" || s.ID < bestID {
			best, bestID = s.User, s.ID
		}
	}
	return best
}
