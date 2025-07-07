class FormHandler {

    static async makeRequest(endpoint, jsonData, options = {}) {
        console.log(`Endpoint: ${endpoint}`)
        console.log(`Json Data: ${jsonData.name}`)
        console.log(`Options: ${options.redirectUrl}`)

        // Get CSRF token from meta tag
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content');

        try {
            const response = await fetch(endpoint, {
                method: options.method || 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken, // Add CSRF token
                    ...options.headers
                },
                body: JSON.stringify(jsonData)
            });

            const result = await response.json();

            console.log(`Result: ${result}`)

            if (response.ok && result.success) {
                // Success callback
                if (options.onSuccess) {
                    options.onSuccess(result);
                } else {
                    // Default success behavior - redirect
                    if (options.redirectUrl) {
                        window.location.href = options.redirectUrl;
                    } else if (options.reload) {
                        window.location.reload();
                    } else {
                        // Default: redirect to current page (refresh)
                        window.location.href = window.location.href;
                    }
                }
            } else {
                // Error callback
                if (options.onError) {
                    options.onError(result);
                } else {
                    // Default error behavior
                    alert('Error: ' + (result.message || 'Unknown error'));
                }
            }
        } catch (error) {
            // Network or other error
            if (options.onError) {
                options.onError({ message: error.message });
            } else {
                alert('Error: ' + error.message);
            }
        }
    }

    static async submitForm(formId, endpoint, options = {}) {

        const form = document.getElementById(formId);

        if (!form) {
            console.error(`Form with ID ${formId} not found`);
            return;
        }

        const formData = new FormData(form);

        const jsonData = {};

        // convert form data to json object
        for (let [key, value] of formData.entries()) {
            jsonData[key] = value;
        }

        // merge with any additional data
        Object.assign(jsonData, options.additionalData || {});

        console.log(jsonData.name);

        // Call the separate request function
        return this.makeRequest(endpoint, jsonData, options);

    }

    // Convenience method for simple form submissions
    static async submitSimpleForm(formId, endpoint, redirectUrl) {
        return this.submitForm(formId, endpoint, {
            redirectUrl: redirectUrl
        });
    }
}

// Global form handler instance
window.FormHandler = FormHandler;
