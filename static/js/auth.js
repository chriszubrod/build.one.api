// Store original fetch before overriding
const originalFetch = window.fetch;

const LEGACY_TOKEN_KEYS = [
    'token.access_token',
    'token.token_type',
    'token.expires_in',
    'token.refresh_token',
    'token.refresh_expires_in',
    'token.stored_at'
];
const CSRF_COOKIE_NAME = 'token.csrf';
const CSRF_HEADER_NAME = 'X-CSRF-Token';
const UNSAFE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

function clearLegacyTokens() {
    try {
        LEGACY_TOKEN_KEYS.forEach((key) => localStorage.removeItem(key));
    } catch (error) {
        // Ignore storage errors (private mode, disabled storage, etc.)
    }
}

clearLegacyTokens();

function getCookieValue(name) {
    const cookies = document.cookie ? document.cookie.split('; ') : [];
    for (const cookie of cookies) {
        const [key, ...rest] = cookie.split('=');
        if (key === name) {
            return decodeURIComponent(rest.join('='));
        }
    }
    return '';
}

// Token refresh utility (cookie-based)
async function refreshAccessToken() {
    try {
        const csrfToken = getCookieValue(CSRF_COOKIE_NAME);
        const response = await originalFetch('/api/v1/auth/refresh', {
            method: 'POST',
            credentials: 'same-origin',
            headers: csrfToken ? { [CSRF_HEADER_NAME]: csrfToken } : undefined
        });

        if (response.ok) {
            return true;
        }

        logout();
        return false;
    } catch (error) {
        console.error('Token refresh failed:', error);
        logout();
        return false;
    }
}

// Intercept fetch requests to rely on HttpOnly cookies and handle refresh
window.fetch = async function(...args) {
    const [url, options = {}] = args;

    if (typeof url === 'string' && url.startsWith('/api/')) {
        const nextOptions = { ...options };
        const headers = new Headers(nextOptions.headers || {});
        headers.delete('Authorization');

        const method = (nextOptions.method || 'GET').toUpperCase();
        if (UNSAFE_METHODS.has(method)) {
            const csrfToken = getCookieValue(CSRF_COOKIE_NAME);
            if (csrfToken) {
                headers.set(CSRF_HEADER_NAME, csrfToken);
            }
        }

        nextOptions.headers = headers;
        nextOptions.credentials = nextOptions.credentials || 'same-origin';

        const response = await originalFetch(url, nextOptions);

        if (response.status === 401 && !url.startsWith('/api/v1/auth/')) {
            const refreshed = await refreshAccessToken();
            if (refreshed) {
                return originalFetch(url, nextOptions);
            }
        }

        return response;
    }

    return originalFetch(...args);
};

function logout() {
    clearLegacyTokens();
    window.location.href = '/auth/logout';
}
