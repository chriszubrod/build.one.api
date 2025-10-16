function logout() {

    // Clear local storage
    localStorage.removeItem('auth.access_token');
    localStorage.removeItem('auth.token_type');
    localStorage.removeItem('auth.expires_in');
    localStorage.removeItem('auth.username');
    localStorage.removeItem('auth.public_id');
    
    // Clear cookie
    document.cookie = 'auth_token=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT';

}
