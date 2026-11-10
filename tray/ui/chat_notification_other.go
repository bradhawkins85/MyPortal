//go:build !windows

package main

// showChatSessionNotification reports false: these notifications have no
// "Open chat" button, so the chat window opens as well.
func showChatSessionNotification(title, body, chatURL string) bool {
	showOSNotification(title, body)
	return false
}
