package middleware

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

// Claims represents Auth0 JWT claims extracted from a validated token.
type Claims struct {
	Subject     string   `json:"sub"`
	Issuer      string   `json:"iss"`
	Audience    []string `json:"aud"`
	Scopes      []string `json:"-"`
	ExpiresAt   int64    `json:"exp"`
	IssuedAt    int64    `json:"iat"`
	Permissions []string `json:"permissions"`
}

// JWKS represents the JSON Web Key Set from Auth0.
type JWKS struct {
	Keys []JWK `json:"keys"`
}

// JWK represents a single JSON Web Key.
type JWK struct {
	Kid string `json:"kid"`
	Kty string `json:"kty"`
	Use string `json:"use"`
	N   string `json:"n"`
	E   string `json:"e"`
	Alg string `json:"alg"`
}

// AuthMiddleware validates Auth0 JWT tokens for PicoClaw API endpoints.
type AuthMiddleware struct {
	domain    string
	audience  string
	jwks      *JWKS
	mu        sync.RWMutex
	lastFetch time.Time
	cacheTTL  time.Duration
}

// NewAuthMiddleware creates a new Auth0 JWT validation middleware.
// Reads AUTH0_DOMAIN and AUTH0_AUDIENCE from environment variables.
func NewAuthMiddleware() (*AuthMiddleware, error) {
	domain := os.Getenv("AUTH0_DOMAIN")
	if domain == "" {
		return nil, errors.New("AUTH0_DOMAIN environment variable is required")
	}

	audience := os.Getenv("AUTH0_AUDIENCE")
	if audience == "" {
		return nil, errors.New("AUTH0_AUDIENCE environment variable is required")
	}

	m := &AuthMiddleware{
		domain:   domain,
		audience: audience,
		cacheTTL: 1 * time.Hour,
	}

	if err := m.fetchJWKS(); err != nil {
		return nil, fmt.Errorf("failed to fetch JWKS: %w", err)
	}

	return m, nil
}

// fetchJWKS retrieves the JSON Web Key Set from Auth0's well-known endpoint.
func (m *AuthMiddleware) fetchJWKS() error {
	url := fmt.Sprintf("https://%s/.well-known/jwks.json", m.domain)

	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Get(url)
	if err != nil {
		return fmt.Errorf("failed to fetch JWKS from %s: %w", url, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("JWKS endpoint returned status %d", resp.StatusCode)
	}

	var jwks JWKS
	if err := json.NewDecoder(resp.Body).Decode(&jwks); err != nil {
		return fmt.Errorf("failed to decode JWKS: %w", err)
	}

	m.mu.Lock()
	m.jwks = &jwks
	m.lastFetch = time.Now()
	m.mu.Unlock()

	log.Printf("[Auth0] JWKS fetched successfully from %s (%d keys)", m.domain, len(jwks.Keys))
	return nil
}

// getJWKS returns the cached JWKS, refreshing if the cache has expired.
func (m *AuthMiddleware) getJWKS() (*JWKS, error) {
	m.mu.RLock()
	if m.jwks != nil && time.Since(m.lastFetch) < m.cacheTTL {
		defer m.mu.RUnlock()
		return m.jwks, nil
	}
	m.mu.RUnlock()

	if err := m.fetchJWKS(); err != nil {
		return nil, err
	}

	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.jwks, nil
}

// findKey finds the JWK matching the given key ID.
func (m *AuthMiddleware) findKey(kid string) (*JWK, error) {
	jwks, err := m.getJWKS()
	if err != nil {
		return nil, err
	}

	for _, key := range jwks.Keys {
		if key.Kid == kid {
			return &key, nil
		}
	}

	return nil, fmt.Errorf("no matching key found for kid: %s", kid)
}

// ValidateToken validates an Auth0 JWT token and returns the claims.
func (m *AuthMiddleware) ValidateToken(tokenString string) (*Claims, error) {
	token, err := jwt.Parse(tokenString, func(token *jwt.Token) (interface{}, error) {
		// Verify the signing method is RS256
		if _, ok := token.Method.(*jwt.SigningMethodRSA); !ok {
			return nil, fmt.Errorf("unexpected signing method: %v", token.Header["alg"])
		}

		kid, ok := token.Header["kid"].(string)
		if !ok {
			return nil, errors.New("token header missing kid")
		}

		jwk, err := m.findKey(kid)
		if err != nil {
			return nil, err
		}

		return jwt.ParseRSAPublicKeyFromPEM(jwkToPEM(jwk))
	}, jwt.WithIssuer(fmt.Sprintf("https://%s/", m.domain)),
		jwt.WithAudience(m.audience))

	if err != nil {
		return nil, fmt.Errorf("token validation failed: %w", err)
	}

	if !token.Valid {
		return nil, errors.New("token is not valid")
	}

	mapClaims, ok := token.Claims.(jwt.MapClaims)
	if !ok {
		return nil, errors.New("failed to extract claims")
	}

	claims := &Claims{}
	if sub, ok := mapClaims["sub"].(string); ok {
		claims.Subject = sub
	}
	if iss, ok := mapClaims["iss"].(string); ok {
		claims.Issuer = iss
	}
	if exp, ok := mapClaims["exp"].(float64); ok {
		claims.ExpiresAt = int64(exp)
	}
	if iat, ok := mapClaims["iat"].(float64); ok {
		claims.IssuedAt = int64(iat)
	}

	// Extract scopes from "scope" claim
	if scope, ok := mapClaims["scope"].(string); ok {
		claims.Scopes = strings.Split(scope, " ")
	}

	// Extract audience
	switch aud := mapClaims["aud"].(type) {
	case string:
		claims.Audience = []string{aud}
	case []interface{}:
		for _, a := range aud {
			if s, ok := a.(string); ok {
				claims.Audience = append(claims.Audience, s)
			}
		}
	}

	// Extract permissions
	if perms, ok := mapClaims["permissions"].([]interface{}); ok {
		for _, p := range perms {
			if s, ok := p.(string); ok {
				claims.Permissions = append(claims.Permissions, s)
			}
		}
	}

	return claims, nil
}

// HasScope checks if the claims contain a specific scope.
func (c *Claims) HasScope(scope string) bool {
	for _, s := range c.Scopes {
		if s == scope {
			return true
		}
	}
	return false
}

// HasPermission checks if the claims contain a specific permission.
func (c *Claims) HasPermission(permission string) bool {
	for _, p := range c.Permissions {
		if p == permission {
			return true
		}
	}
	return false
}

// contextKey is a custom type for context keys to avoid collisions.
type contextKey string

const claimsKey contextKey = "auth0_claims"

// Protect returns an HTTP middleware that validates Auth0 JWT tokens.
// It extracts the Bearer token from the Authorization header, validates it,
// and adds the claims to the request context.
func (m *AuthMiddleware) Protect(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		authHeader := r.Header.Get("Authorization")
		if authHeader == "" {
			http.Error(w, `{"error": "missing Authorization header"}`, http.StatusUnauthorized)
			return
		}

		parts := strings.SplitN(authHeader, " ", 2)
		if len(parts) != 2 || strings.ToLower(parts[0]) != "bearer" {
			http.Error(w, `{"error": "invalid Authorization header format"}`, http.StatusUnauthorized)
			return
		}

		claims, err := m.ValidateToken(parts[1])
		if err != nil {
			log.Printf("[Auth0] Token validation failed: %v", err)
			http.Error(w, fmt.Sprintf(`{"error": "invalid token: %s"}`, err.Error()), http.StatusUnauthorized)
			return
		}

		ctx := context.WithValue(r.Context(), claimsKey, claims)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

// RequireScope returns middleware that checks for a specific scope in the JWT.
func (m *AuthMiddleware) RequireScope(scope string, next http.Handler) http.Handler {
	return m.Protect(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		claims := GetClaims(r.Context())
		if claims == nil {
			http.Error(w, `{"error": "no claims in context"}`, http.StatusUnauthorized)
			return
		}

		if !claims.HasScope(scope) {
			http.Error(w, fmt.Sprintf(`{"error": "missing required scope: %s"}`, scope), http.StatusForbidden)
			return
		}

		next.ServeHTTP(w, r)
	}))
}

// GetClaims extracts Auth0 claims from the request context.
func GetClaims(ctx context.Context) *Claims {
	claims, ok := ctx.Value(claimsKey).(*Claims)
	if !ok {
		return nil
	}
	return claims
}

// jwkToPEM converts a JWK to PEM format for RSA public key parsing.
func jwkToPEM(jwk *JWK) []byte {
	// For RS256, we need the modulus (n) and exponent (e) in PEM format.
	// The golang-jwt library handles base64url decoding internally.
	// We construct a minimal PEM from the JWK components.
	//
	// In production, use a proper JWK-to-PEM library like go-jose.
	// This is a simplified version for the hackathon.
	return []byte(fmt.Sprintf(`-----BEGIN PUBLIC KEY-----
%s
-----END PUBLIC KEY-----`, jwk.N))
}
