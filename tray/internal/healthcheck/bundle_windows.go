//go:build windows

package healthcheck

import "embed"

// Release builds populate sensorbundle/ with the pinned LibreHardwareMonitor
// library, its dependencies, the PawnIO installer and their notices before
// compiling (tray/sensor-bundle/build.py). Development builds embed only the
// notice, and health checks then report that hardware sensors are not part of
// the build.
//
//go:embed sensorbundle
var sensorBundle embed.FS

const sensorBundleRoot = "sensorbundle"
