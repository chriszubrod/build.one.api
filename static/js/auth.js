// Store original fetch before overriding
const originalFetch = window.fetch;

// Token refresh utility
async function refreshAccessToken() {
    const refreshToken = localStorage.getItem('token.refresh_token');
    
    if (!refreshToken) {
        // No refresh token, redirect to login
        window.location.href = '/auth/login';
        return null;
    }

    try {
        // Use originalFetch to avoid recursive interception
        const response = await originalFetch('/api/v1/auth/refresh', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ refresh_token: refreshToken })
        });

        if (response.ok) {
            const result = await response.json();
            
            // Update tokens
            localStorage.setItem('token.access_token', result.token.access_token);
            localStorage.setItem('token.expires_in', result.token.expires_in);
            localStorage.setItem('token.refresh_token', result.refresh_token.refresh_token);
            localStorage.setItem('token.refresh_expires_in', result.refresh_token.expires_in);
            localStorage.setItem('token.stored_at', Math.floor(Date.now() / 1000).toString());
            
            // Update cookie
            document.cookie = `token.access_token=${result.token.access_token}; path=/; max-age=${result.token.expires_in}; samesite=lax`;
            
            return result.token.access_token;
        } else {
            // Refresh token expired or invalid, redirect to login
            logout();
            return null;
        }
    } catch (error) {
        console.error('Token refresh failed:', error);
        logout();
        return null;
    }
}

// Check if token is expired or about to expire (within 60 seconds)
function isTokenExpiringSoon() {
    const expiresIn = parseInt(localStorage.getItem('token.expires_in') || '0');
    const storedAt = parseInt(localStorage.getItem('token.stored_at') || '0');
    
    if (!storedAt || !expiresIn) {
        return true; // Assume expired if we don't have the data
    }
    
    const now = Math.floor(Date.now() / 1000);
    const expiresAt = storedAt + expiresIn;
    const timeUntilExpiry = expiresAt - now;
    
    // Refresh if expires within 60 seconds
    return timeUntilExpiry < 60;
}

// Intercept fetch requests to add token and handle refresh
window.fetch = async function(...args) {
    const [url, options = {}] = args;
    
    // Only intercept API calls
    if (typeof url === 'string' && url.startsWith('/api/')) {
        // Check if token needs refresh
        if (isTokenExpiringSoon()) {
            await refreshAccessToken();
        }
        
        // Get current token
        const token = localStorage.getItem('token.access_token');
        
        // Add Authorization header if token exists
        if (token) {
            options.headers = {
                ...options.headers,
                'Authorization': `Bearer ${token}`
            };
        }
        
        // Make request
        const response = await originalFetch(url, options);
        
        // If 401, try refresh once
        if (response.status === 401) {
            const newToken = await refreshAccessToken();
            if (newToken) {
                // Retry with new token
                options.headers = {
                    ...options.headers,
                    'Authorization': `Bearer ${newToken}`
                };
                return originalFetch(url, options);
            }
        }
        
        return response;
    }
    
    return originalFetch(...args);
};

function logout() {
    // Clear all localStorage items
    localStorage.removeItem('token.access_token');
    localStorage.removeItem('token.token_type');
    localStorage.removeItem('token.expires_in');
    localStorage.removeItem('token.refresh_token');
    localStorage.removeItem('token.refresh_expires_in');
    localStorage.removeItem('token.stored_at');
    localStorage.removeItem('auth.username');
    localStorage.removeItem('auth.public_id');
    
    // Clear cookie with correct name
    document.cookie = 'token.access_token=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT; samesite=lax';
}
