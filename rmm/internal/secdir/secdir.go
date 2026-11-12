// Package secdir keeps the agent's data folders private to the service.
//
// On Windows a folder created under %ProgramData% inherits ProgramData's
// permissions, which let every local user read files and create new ones.
// Go's Unix permission bits are ignored there, so the folders are given an
// explicit, protected access list instead: SYSTEM and Administrators get full
// control and, only where the per-user tray UI must read the folder, Users get
// read access. Nobody else can create, replace or plant files in them.
//
// On macOS and Linux the folders live under root-owned locations, so the
// usual permission bits are enough.
package secdir

// Access says who besides SYSTEM/root and Administrators may use a folder.
type Access int

const (
	// Private folders are for the service account and Administrators only.
	Private Access = iota
	// UsersRead folders can also be read (not written) by local users.
	UsersRead
)
