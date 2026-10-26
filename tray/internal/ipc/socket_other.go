//go:build !darwin && !linux

package ipc

func configureSocket(_ string) error {
	return nil
}
