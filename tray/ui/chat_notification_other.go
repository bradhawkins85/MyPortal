//go:build !windows

package main

func showChatSessionNotification(title, body, chatURL string) {
	showOSNotification(title, body)
}
