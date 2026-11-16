package config

import (
	"crypto/rand"
	"encoding/base64"
	"errors"
	"fmt"
	"io/fs"
	"strings"

	"github.com/hankthebldr/cortexsim/installer/setup/internal/host"
)

const (
	secretBytes = 48
	secretLen   = 64 // base64.RawURLEncoding of 48 bytes
)

// GenerateSecret returns 48 bytes from crypto/rand as 64 URL-safe characters.
func GenerateSecret() (string, error) {
	b := make([]byte, secretBytes)
	if _, err := rand.Read(b); err != nil {
		return "", fmt.Errorf("generate master secret: %w", err)
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}

func validSecret(s string) bool {
	if len(s) != secretLen {
		return false
	}
	for i := 0; i < len(s); i++ {
		c := s[i]
		switch {
		case c >= 'a' && c <= 'z', c >= 'A' && c <= 'Z', c >= '0' && c <= '9', c == '-', c == '_':
		default:
			return false
		}
	}
	return true
}

// EnsureSecret returns the master secret in p.SecretFile, creating it when the
// file does not exist. created reports whether it was just written.
//
// An existing file is reused only when its trimmed content is a valid secret;
// anything else is an error and the file is left alone, because replacing a
// secret would make every stored credential undecryptable
// (MASTER_KEY_MISMATCH). The error never contains the file's content.
//
// The file is written 0600 through the host adapter. On Windows the caller
// must have created p.ConfigDir with an admin-only ACL: the mode bits do not
// protect it there.
func EnsureSecret(h host.Host, p Paths) (secret string, created bool, err error) {
	data, err := h.ReadFile(p.SecretFile)
	switch {
	case err == nil:
		s := strings.TrimSpace(string(data))
		if !validSecret(s) {
			return "", false, fmt.Errorf("master secret file %s exists but is not a valid secret (want %d URL-safe characters); not replacing it", p.SecretFile, secretLen)
		}
		return s, false, nil
	case errors.Is(err, fs.ErrNotExist):
	default:
		return "", false, fmt.Errorf("read master secret file %s: %w", p.SecretFile, err)
	}
	s, err := GenerateSecret()
	if err != nil {
		return "", false, err
	}
	if err := h.MkdirAll(p.ConfigDir, 0o700); err != nil {
		return "", false, fmt.Errorf("create %s: %w", p.ConfigDir, err)
	}
	if err := h.WriteFile(p.SecretFile, []byte(s+"\n"), 0o600); err != nil {
		return "", false, fmt.Errorf("write master secret file %s: %w", p.SecretFile, err)
	}
	return s, true, nil
}
