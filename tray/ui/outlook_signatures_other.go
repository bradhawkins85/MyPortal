//go:build !windows

package main

import "errors"

// Outlook for Mac stores signatures in its own database, so only Classic
// Outlook for Windows is managed.
type unsupportedOutlookPlatform struct{}

func newOutlookPlatform() outlookPlatform { return unsupportedOutlookPlatform{} }

var errOutlookUnsupported = errors.New("Classic Outlook signatures are only managed on Windows")

func (unsupportedOutlookPlatform) Supported() bool { return false }

func (unsupportedOutlookPlatform) Accounts() ([]outlookAccount, error) { return nil, nil }

func (unsupportedOutlookPlatform) SignaturesDir() (string, error) { return "", errOutlookUnsupported }

func (unsupportedOutlookPlatform) SetAccountSignature(outlookAccount, string) error {
	return errOutlookUnsupported
}

func (unsupportedOutlookPlatform) SetDefaultSignature(string) error { return errOutlookUnsupported }

func (unsupportedOutlookPlatform) DisableRoamingSignatures() error { return errOutlookUnsupported }

func (unsupportedOutlookPlatform) StateDir() (string, error) { return "", errOutlookUnsupported }
