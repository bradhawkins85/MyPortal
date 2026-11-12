// Command myportal-verify-release checks that a release tag's commit was
// signed with the MyPortal release key before the build agent builds it.
//
// The Sign release workflow uploads myportal-source.sig to each release: the
// base64 Ed25519 signature of
//
//	myportal-source-v1
//	tag=<release tag>
//	commit=<the commit the tag points at>
//
// Install-MyPortalBuildAgent.ps1 builds this tool and copies the public key
// next to it from the checkout it was run from, so a release whose tag was
// moved to other code cannot be built into company installers.
//
// Usage: myportal-verify-release -key release-public-key.pem -tag v1.2.3 -commit <sha> -sig myportal-source.sig
// It exits 0 when the signature is valid and 1 (with the reason) otherwise.
package main

import (
	"crypto/ed25519"
	"crypto/x509"
	"encoding/base64"
	"encoding/pem"
	"errors"
	"flag"
	"fmt"
	"os"
	"regexp"
	"strings"
)

var commitPattern = regexp.MustCompile(`^[0-9a-f]{40}$`)

func sourceManifest(tag, commit string) []byte {
	return []byte("myportal-source-v1\ntag=" + tag + "\ncommit=" + commit + "\n")
}

func parseKey(pemText []byte) (ed25519.PublicKey, error) {
	block, _ := pem.Decode(pemText)
	if block == nil {
		return nil, errors.New("no PEM public key")
	}
	parsed, err := x509.ParsePKIXPublicKey(block.Bytes)
	if err != nil {
		return nil, err
	}
	key, ok := parsed.(ed25519.PublicKey)
	if !ok {
		return nil, errors.New("the release key is not Ed25519")
	}
	return key, nil
}

func verify(key ed25519.PublicKey, tag, commit, signature string) error {
	commit = strings.ToLower(strings.TrimSpace(commit))
	if tag == "" || !commitPattern.MatchString(commit) {
		return errors.New("a release tag and a full commit SHA are required")
	}
	sig, err := base64.StdEncoding.DecodeString(strings.TrimSpace(signature))
	if err != nil || len(sig) != ed25519.SignatureSize {
		return errors.New("the release source signature is malformed")
	}
	if !ed25519.Verify(key, sourceManifest(tag, commit), sig) {
		return fmt.Errorf("release %s is not signed for commit %s", tag, commit)
	}
	return nil
}

func run(args []string) error {
	flags := flag.NewFlagSet("myportal-verify-release", flag.ContinueOnError)
	keyPath := flags.String("key", "", "release public key (PEM)")
	tag := flags.String("tag", "", "release tag")
	commit := flags.String("commit", "", "commit the cloned tag points at")
	sigPath := flags.String("sig", "", "myportal-source.sig from the release")
	if err := flags.Parse(args); err != nil {
		return err
	}
	pemText, err := os.ReadFile(*keyPath)
	if err != nil {
		return fmt.Errorf("read the release key: %w", err)
	}
	key, err := parseKey(pemText)
	if err != nil {
		return fmt.Errorf("release key: %w", err)
	}
	signature, err := os.ReadFile(*sigPath)
	if err != nil {
		return fmt.Errorf("read the release signature: %w", err)
	}
	return verify(key, *tag, *commit, string(signature))
}

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	fmt.Println("Release source signature verified.")
}
