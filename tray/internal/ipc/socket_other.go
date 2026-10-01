//go:build !darwin

package ipc

func configureSocket(_ string) error {
	return nil
}
