// Form handling module
const FormHandler = {
    // Initialize form with options
    init: function(formId, apiEndpoint, method = 'POST') {
        const form = document.getElementById(formId);
        const submitButton = document.getElementById('submitButton');

        if (form && submitButton) {
            console.log(`FormHandler initialized for ${formId} with endpoint ${apiEndpoint}`);
            submitButton.addEventListener('click', async function() {
                await FormHandler.handleSubmit(form, apiEndpoint, method);
            });
        } else {
            console.error(`Form or submit button not found. Form ID: ${formId}`);
            if (!form) console.error(`Form element with ID ${formId} not found`);
            if (!submitButton) console.error(`Submit button with ID 'submitButton' not found`);
        }
    },

    // Handle form submission
    handleSubmit: async function(form, apiEndpoint, method) {
        try {
            const payload = await this.preparePayload(form);
            console.log('Sending payload:', payload);

            const response = await fetch(apiEndpoint, {
                method: method,
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(payload)
            });

            const json_response = await response.json();
            console.log('Response json:', json_response);
            const data = json_response.data;
            console.log('Response data:', data);
            
            // Handle response (can be customized)
            // Update this to handle status true or false and redirect
            console.log('Data status code:', data.status_code);
            if (json_response.status_code === 200) {
                window.location.href = data.redirect_url;
            } else {
                alert(data.message || 'An error occurred');
            }

        } catch (error) {
            console.error('Submission error:', error);
        }
    },

    // Prepare payload with form data and files
    preparePayload: async function(form) {
        const formData = new FormData(form);
        
        // Define line item field names to exclude from main data
        const lineItemFields = ['sub-cost-code', 'description', 'units', 'rate', 'amount', 'is-billable', 'project'];
        
        // Get main form fields (excluding line items)
        const jsonData = Object.fromEntries(
            Array.from(formData.entries())
                .filter(([key]) => !key.startsWith('file') && !lineItemFields.includes(key))
        );

        // Get line items
        const lineItems = Array.from(document.querySelectorAll('.line-item-container')).map(container => {
            return {
                'sub-cost-code': container.querySelector('select[name="sub-cost-code"]')?.value,
                'description': container.querySelector('input[name="description"]')?.value,
                'units': container.querySelector('input[name="units"]')?.value,
                'rate': this.parseCurrency(container.querySelector('input[name="rate"]')?.value || '0'),
                'amount': this.parseCurrency(container.querySelector('input[name="amount"]')?.value || '0'),
                'is-billable': container.querySelector('input[name="is-billable"]')?.checked || false,
                'project': container.querySelector('select[name="project"]')?.value
            };
        });

        // Get files
        const fileArray = await this.processFiles(form);

        return {
            ...jsonData,
            line_items: lineItems,
            files: fileArray
        };
    },

    // Helper function to parse currency values
    parseCurrency: function(value) {
        return parseFloat(value.replace(/[$,]/g, '')) || 0;
    },

    // Process files from form
    processFiles: async function(form) {
        // Find the parent form that contains the file upload component
        const fileUploadForm = document.getElementById('fileUploadForm');
        let fileArray = [];

        if (fileUploadForm) {
            // Get the analyzed file data from hidden fields
            const fileData = {
                text: document.getElementById('file-text')?.value || '',
                numberOfPages: document.getElementById('file-number-of-pages')?.value || 0,
                analysisResult: document.getElementById('file-analysis-result')?.value || '',
            };

            // Get the actual file input
            const fileInput = document.getElementById('fileInput');
            
            if (fileInput && fileInput.files.length > 0) {
                const file = fileInput.files[0];
                
                fileArray.push({
                    name: file.name,
                    type: file.type,
                    size: file.size,
                    text: fileData.text,
                    pages: parseInt(fileData.numberOfPages) || 0,
                    analysisResult: fileData.analysisResult,
                    // Only include content if needed - you might not need this if you've already analyzed the file
                    content: await this.getFileContent(file)
                });
            }
        }

        return fileArray;
    },

    // Get file content as base64
    getFileContent: function(file) {
        return new Promise((resolve) => {
            const reader = new FileReader();
            reader.onloadend = () => resolve(reader.result);
            reader.readAsDataURL(file);
        });
    }
};

// Auto-initialize when script loads
document.addEventListener('DOMContentLoaded', function() {
    const scripts = document.querySelectorAll('script[data-form-id]');
    scripts.forEach(script => {
        const formId = script.dataset.formId;
        const endpoint = script.dataset.endpoint;
        const method = script.dataset.method || 'POST';
        
        if (formId && endpoint) {
            FormHandler.init(formId, endpoint, method);
        }
    });
});
