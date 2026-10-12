//go:build windows

package main

import (
	"syscall"
	"unsafe"

	"github.com/bradhawkins85/myportal-tray/internal/logger"
)

const (
	messageBoxYesNo        = 0x00000004
	messageBoxIconQuestion = 0x00000020
	messageBoxTopMost      = 0x00040000
	messageBoxIDYes        = 6
)

// confirmAction asks the user a yes/no question and reports whether they
// chose Yes.
func confirmAction(title, question string) bool {
	titlePtr, err := syscall.UTF16PtrFromString(title)
	if err != nil {
		logger.Warn("confirmAction: invalid title: %v", err)
		return false
	}
	questionPtr, err := syscall.UTF16PtrFromString(question)
	if err != nil {
		logger.Warn("confirmAction: invalid question: %v", err)
		return false
	}
	ret, _, _ := procMessageBox.Call(
		0,
		uintptr(unsafe.Pointer(questionPtr)),
		uintptr(unsafe.Pointer(titlePtr)),
		uintptr(messageBoxYesNo|messageBoxIconQuestion|messageBoxSetForeground|messageBoxTopMost),
	)
	return ret == messageBoxIDYes
}
