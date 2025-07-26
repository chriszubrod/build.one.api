// Utility functions for file handling and other common operations

/**
 * Converts a file to base64 string
 * @param {File} file - The file to convert
 * @returns {Promise<string>} - Promise that resolves to base64 string
 */
function toBase64(file) {

    return new Promise((resolve, reject) => {
        // FileReader is a built-in browser API that allows you to read the contents of a file async.
        // Reads file conents from user's computer
        // Converts to different format (base64 here)
        const reader = new FileReader();
        reader.readAsDataURL(file);
        reader.onload = () => resolve(reader.result.split(',')[1]);
        reader.onerror = (error) => reject(error);
    });
}

/**
 * Clears a file input and related UI elements
 * @param {Object} options - Configuration options
 * @param {HTMLInputElement} options.fileInput - The file input element to clear
 * @param {HTMLElement} options.fileNameEl - Element to display file name (optional)
 * @param {HTMLElement} options.dropArea - Drop area element to show (optional)
 * @param {HTMLElement} options.preview - Preview element to hide (optional)
 * @param {HTMLElement} options.pdfViewer - PDF viewer element to clear (optional)
 */
function clearFile(options = {}) {
    const {
        fileInput,
        fileNameEl,
        dropArea,
        preview,
        pdfViewer
    } = options;

    if (fileInput) fileInput.value = '';
    if (fileNameEl) fileNameEl.textContent = '';
    if (dropArea) dropArea.classList.remove('hidden');
    if (preview) preview.classList.add('hidden');
    if (pdfViewer) {
        pdfViewer.src = '';
        pdfViewer.classList.add('hidden');
    }
}

/**
 * Gets the appropriate icon for a file type
 * @param {string} fileType - The MIME type of the file
 * @param {string} fileName - The name of the file
 * @returns {string} - SVG icon HTML
 */
function getFileIcon(fileType, fileName = '') {
    const extension = fileName.split('.').pop()?.toLowerCase();
    
    // Document types
    if (fileType === 'application/pdf' || extension === 'pdf') {
        return `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-6 h-6 text-red-600">
            <path stroke-linecap="round" stroke-linejoin="round" d="M10.125 2.25h-4.5c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125v-9M10.125 2.25h.375a9 9 0 0 1 9 9v.375M10.125 2.25A3.375 3.375 0 0 1 13.5 5.625v1.5c0 .621.504 1.125 1.125 1.125h1.5a3.375 3.375 0 0 1 3.375 3.375M9 15l2.25 2.25L15 12" />
        </svg>`;
    }
    
    // Word documents
    if (fileType.includes('word') || extension === 'doc' || extension === 'docx') {
        return `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-6 h-6 text-blue-600">
            <path stroke-linecap="round" stroke-linejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z" />
        </svg>`;
    }
    
    // Excel files
    if (fileType.includes('excel') || fileType.includes('spreadsheet') || extension === 'xls' || extension === 'xlsx') {
        return `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-6 h-6 text-green-600">
            <path stroke-linecap="round" stroke-linejoin="round" d="M3.75 3v11.25A2.25 2.25 0 0 0 6 16.5h2.25M3.75 3h-1.5m1.5 0h16.5m0 0h1.5m-1.5 0v11.25A2.25 2.25 0 0 1 18 16.5h-2.25m-7.5 0h7.5m-7.5 0-1 3m8.5-3 1 3m0 0 .5 1.5m-.5-1.5h-9.5m0 0-.5 1.5m.75-9 3-3 2.148 2.148A12.061 12.061 0 0 1 16.5 7.605" />
        </svg>`;
    }
    
    // Images
    if (fileType.startsWith('image/')) {
        return `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-6 h-6 text-purple-600">
            <path stroke-linecap="round" stroke-linejoin="round" d="m2.25 15.75 5.159-5.159a2.25 2.25 0 0 1 3.182 0l5.159 5.159m-1.5-1.5 1.409-1.409a2.25 2.25 0 0 1 3.182 0l2.909 2.909m-18 3.75h16.5a1.5 1.5 0 0 0 1.5-1.5V6a1.5 1.5 0 0 0-1.5-1.5H3.75A1.5 1.5 0 0 0 2.25 6v12a1.5 1.5 0 0 0 1.5 1.5Zm10.5-11.25h.008v.008h-.008V8.25Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Z" />
        </svg>`;
    }
    
    // Text files
    if (fileType.startsWith('text/') || extension === 'txt' || extension === 'md') {
        return `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-6 h-6 text-gray-600">
            <path stroke-linecap="round" stroke-linejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m2.25 0H3.375c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h17.25c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z" />
        </svg>`;
    }
    
    // Archive files
    if (fileType.includes('zip') || fileType.includes('rar') || fileType.includes('7z') || 
        extension === 'zip' || extension === 'rar' || extension === '7z' || extension === 'tar' || extension === 'gz') {
        return `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-6 h-6 text-orange-600">
            <path stroke-linecap="round" stroke-linejoin="round" d="M20.25 7.5l-.625 10.632a2.25 2.25 0 01-2.247 2.118H6.622a2.25 2.25 0 01-2.247-2.118L3.75 7.5m8.25 3v6.75m0 0l-3-3m3 3l3-3M3.375 7.5h17.25c.621 0 1.125-.504 1.125-1.125v-1.5c0-.621-.504-1.125-1.125-1.125H3.375c-.621 0-1.125.504-1.125 1.125v1.5c0 .621.504 1.125 1.125 1.125z" />
        </svg>`;
    }
    
    // Default file icon
    return `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-6 h-6 text-gray-600">
        <path stroke-linecap="round" stroke-linejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z" />
    </svg>`;
}

/**
 * Handles file upload and UI updates
 * @param {File} file - The file to handle
 * @param {Object} options - Configuration options
 * @param {HTMLInputElement} options.fileInput - The file input element
 * @param {HTMLElement} options.fileNameEl - Element to display file name
 * @param {HTMLElement} options.dropArea - Drop area element
 * @param {HTMLElement} options.preview - Preview element
 * @param {HTMLElement} options.pdfViewer - PDF viewer element (optional)
 * @param {string|Array} options.acceptedTypes - Accepted file types (default: common document types)
 * @param {number} options.maxSize - Maximum file size in bytes (optional)
 */
function handleFile(file, options = {}) {
    const {
        fileInput,
        fileNameEl,
        dropArea,
        preview,
        pdfViewer,
        acceptedTypes = [
            'application/pdf',
            'application/msword',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/vnd.ms-excel',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'image/jpeg',
            'image/png',
            'image/gif',
            'image/webp',
            'text/plain',
            'text/markdown',
            'application/zip',
            'application/x-rar-compressed',
            'application/x-7z-compressed'
        ],
        maxSize
    } = options;

    // Check if file type is accepted
    const isAcceptedType = Array.isArray(acceptedTypes) 
        ? acceptedTypes.includes(file.type)
        : file.type === acceptedTypes;

    if (!file || !isAcceptedType) {
        console.warn('File type not accepted:', file?.type);
        return false;
    }

    // Check file size if specified
    if (maxSize && file.size > maxSize) {
        console.warn('File too large:', file.size, 'bytes');
        return false;
    }

    // Store the file in the file input
    const dataTransfer = new DataTransfer();
    dataTransfer.items.add(file);
    fileInput.files = dataTransfer.files;
    
    // Update the UI with appropriate icon
    fileNameEl.innerHTML = getFileIcon(file.type, file.name);
    dropArea.classList.add('hidden');
    preview.classList.remove('hidden');
    
    // Show PDF in the viewer if it's a PDF and viewer is provided
    if (file.type === 'application/pdf' && pdfViewer) {
        const url = URL.createObjectURL(file);
        pdfViewer.src = url;
        pdfViewer.classList.remove('hidden');
    }
    
    return true;
}

/**
 * Formats an input field as currency
 * @param {HTMLInputElement} input - The input element to format
 * @param {string} currency - Currency code (default: 'USD')
 */
function formatCurrency(input, currency = 'USD') {
    const cleaned = input.value.replace(/[^0-9.-]/g, '');
    const value = parseFloat(cleaned);
    if (!isNaN(value)) {
        input.value = new Intl.NumberFormat('en-US', {
            style: 'currency',
            currency: currency,
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        }).format(value);
    } else {
        input.value = '$0.00';
    }
}

/**
 * Updates total amount based on line item amounts
 * @param {string} amountSelector - CSS selector for amount inputs
 * @param {string} totalSelector - CSS selector for total input
 * @param {string} currency - Currency code (default: 'USD')
 */
function updateTotalAmount(amountSelector = 'input[name="line_amount[]"]', totalSelector = 'input[name="total_amount"]', currency = 'USD') {
    const amountInputs = document.querySelectorAll(amountSelector);
    const totalInput = document.querySelector(totalSelector);
    
    if (!totalInput) return;
    
    let total = 0;
    amountInputs.forEach(input => {
        const cleaned = input.value.replace(/[^0-9.-]/g, '');
        const value = parseFloat(cleaned);
        if (!isNaN(value)) total += value;
    });
    
    totalInput.value = new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: currency,
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    }).format(total);
}

/**
 * Previews a PDF file in an iframe
 * @param {HTMLInputElement} fileInput - The file input element
 * @param {string} viewerId - ID of the PDF viewer iframe
 */
function previewPDF(fileInput, viewerId = 'pdf-viewer') {
    const file = fileInput.files[0];
    const pdfViewer = document.getElementById(viewerId);
    
    if (file && pdfViewer) {
        const url = URL.createObjectURL(file);
        pdfViewer.src = url;
        pdfViewer.classList.remove('hidden');
    }
}
