/**
 * Athyna PDF Compressor — Client-Side Logic
 * Handles drag & drop, file upload, progress simulation, and download
 */

(function () {
    'use strict';

    // DOM Elements
    const dropZone = document.getElementById('dropZone');
    const dropContent = document.getElementById('dropContent');
    const fileInfo = document.getElementById('fileInfo');
    const fileInput = document.getElementById('fileInput');
    const fileName = document.getElementById('fileName');
    const fileSize = document.getElementById('fileSize');
    const removeFile = document.getElementById('removeFile');
    const targetSection = document.getElementById('targetSection');
    const btn5mb = document.getElementById('btn5mb');
    const btn10mb = document.getElementById('btn10mb');
    const targetHint = document.getElementById('targetHint');
    const compressBtn = document.getElementById('compressBtn');
    const uploadCard = document.getElementById('uploadCard');
    const progressCard = document.getElementById('progressCard');
    const progressBar = document.getElementById('progressBar');
    const progressStatus = document.getElementById('progressStatus');
    const resultCard = document.getElementById('resultCard');
    const resultFilename = document.getElementById('resultFilename');
    const statOriginal = document.getElementById('statOriginal');
    const statCompressed = document.getElementById('statCompressed');
    const statRatio = document.getElementById('statRatio');
    const downloadBtn = document.getElementById('downloadBtn');
    const anotherBtn = document.getElementById('anotherBtn');
    const errorCard = document.getElementById('errorCard');
    const errorMessage = document.getElementById('errorMessage');
    const retryBtn = document.getElementById('retryBtn');

    // State
    let selectedFile = null;
    let targetMB = 5;
    let downloadUrl = '';
    let downloadFilename = '';

    // ============================
    // DRAG & DROP
    // ============================
    dropZone.addEventListener('click', (e) => {
        if (!selectedFile) {
            fileInput.click();
        }
    });

    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            handleFile(e.target.files[0]);
        }
    });

    ['dragenter', 'dragover'].forEach(evt => {
        dropZone.addEventListener(evt, (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (!selectedFile) {
                dropZone.classList.add('drag-over');
            }
        });
    });

    ['dragleave', 'drop'].forEach(evt => {
        dropZone.addEventListener(evt, (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropZone.classList.remove('drag-over');
        });
    });

    dropZone.addEventListener('drop', (e) => {
        const files = e.dataTransfer.files;
        if (files.length > 0) {
            handleFile(files[0]);
        }
    });

    // Prevent default browser behavior for drag
    document.addEventListener('dragover', (e) => e.preventDefault());
    document.addEventListener('drop', (e) => e.preventDefault());

    // ============================
    // FILE HANDLING
    // ============================
    function handleFile(file) {
        // Validate file type
        if (!file.name.toLowerCase().endsWith('.pdf')) {
            showError('Please upload a PDF file.');
            return;
        }

        // Validate file size (100 MB max)
        if (file.size > 100 * 1024 * 1024) {
            showError('File is too large. Maximum allowed size is 100 MB.');
            return;
        }

        selectedFile = file;

        // Update UI
        fileName.textContent = file.name;
        fileSize.textContent = formatFileSize(file.size);

        dropContent.style.display = 'none';
        fileInfo.style.display = 'flex';
        dropZone.classList.add('has-file');

        targetSection.style.display = 'block';
        compressBtn.style.display = 'flex';

        // Auto-select target based on file size
        const fileMB = file.size / (1024 * 1024);
        if (fileMB <= 10) {
            selectTarget(5);
        } else {
            selectTarget(5);
        }
    }

    function clearFile() {
        selectedFile = null;
        fileInput.value = '';

        dropContent.style.display = 'flex';
        fileInfo.style.display = 'none';
        dropZone.classList.remove('has-file');

        targetSection.style.display = 'none';
        compressBtn.style.display = 'none';
    }

    removeFile.addEventListener('click', (e) => {
        e.stopPropagation();
        clearFile();
    });

    // ============================
    // TARGET SELECTION
    // ============================
    function selectTarget(mb) {
        targetMB = mb;

        btn5mb.classList.toggle('active', mb === 5);
        btn10mb.classList.toggle('active', mb === 10);

        if (mb === 5) {
            targetHint.textContent = 'Maximum compression — best for email attachments';
        } else {
            targetHint.textContent = 'Balanced compression — good quality and size';
        }
    }

    btn5mb.addEventListener('click', () => selectTarget(5));
    btn10mb.addEventListener('click', () => selectTarget(10));

    // ============================
    // COMPRESSION
    // ============================
    compressBtn.addEventListener('click', startCompression);

    let isCompressing = false;

    function startCompression() {
        if (!selectedFile || isCompressing) return;
        isCompressing = true;

        // Show progress, hide others
        uploadCard.style.display = 'none';
        resultCard.style.display = 'none';
        errorCard.style.display = 'none';
        progressCard.style.display = 'block';
        progressBar.style.width = '0%';

        // Start progress simulation
        simulateProgress();

        // Build form data
        const formData = new FormData();
        formData.append('file', selectedFile);
        formData.append('target_mb', targetMB);

        // Send to server with extended timeout (10 min for aggressive compression)
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 600000);

        fetch('/compress', {
            method: 'POST',
            body: formData,
            signal: controller.signal
        })
            .then(response => {
                clearTimeout(timeoutId);
                return response.json();
            })
            .then(data => {
                completeProgress(() => {
                    if (data.error) {
                        showError(data.error);
                        return;
                    }

                    showResult(data);
                });
            })
            .catch(err => {
                clearTimeout(timeoutId);
                if (err.name === 'AbortError') {
                    completeProgress(() => {
                        showError('Compression timed out. Try a larger target size (10MB).');
                    });
                } else {
                    completeProgress(() => {
                        showError('Network error. Please make sure the server is running.');
                    });
                }
            });
    }

    // ============================
    // PROGRESS SIMULATION
    // ============================
    let progressInterval = null;
    let currentProgress = 0;
    const statusMessages = [
        'Analyzing document structure',
        'Extracting page content',
        'Optimizing images',
        'Recompressing with quality preservation',
        'Rebuilding PDF structure',
        'Applying final optimizations',
        'Finishing up...'
    ];

    function simulateProgress() {
        currentProgress = 0;
        let msgIndex = 0;

        progressInterval = setInterval(() => {
            // Slow, steady climb to avoid stalling perception
            if (currentProgress < 20) {
                currentProgress += Math.random() * 4;
            } else if (currentProgress < 50) {
                currentProgress += Math.random() * 2;
            } else if (currentProgress < 75) {
                currentProgress += Math.random() * 1;
            } else if (currentProgress < 90) {
                currentProgress += Math.random() * 0.3;
            }

            // Cap at 92% (server completes to 100%)
            currentProgress = Math.min(currentProgress, 92);
            progressBar.style.width = currentProgress + '%';

            // Update status message
            const newIndex = Math.min(Math.floor(currentProgress / 15), statusMessages.length - 1);
            if (newIndex !== msgIndex) {
                msgIndex = newIndex;
                progressStatus.textContent = statusMessages[msgIndex];
            }
        }, 500);
    }

    function completeProgress(callback) {
        clearInterval(progressInterval);
        isCompressing = false;
        progressBar.style.width = '100%';
        progressStatus.textContent = 'Complete!';

        setTimeout(() => {
            progressCard.style.display = 'none';
            callback();
        }, 500);
    }

    // ============================
    // RESULT
    // ============================
    function showResult(data) {
        progressCard.style.display = 'none';
        errorCard.style.display = 'none';
        resultCard.style.display = 'block';

        resultFilename.textContent = data.filename;
        statOriginal.textContent = data.original_mb + ' MB';
        statCompressed.textContent = data.compressed_mb + ' MB';
        statRatio.textContent = data.ratio + '%';

        downloadUrl = `/download/${data.download_id}?filename=${encodeURIComponent(data.filename)}`;
        downloadFilename = data.filename;

        const partialBanner = document.getElementById('partialBanner');
        const partialBannerText = document.getElementById('partialBannerText');
        const resultTitle = document.querySelector('.result-title');

        if (data.status === 'already_under_target') {
            resultTitle.textContent = 'Already Under Target!';
            statRatio.textContent = '0%';
            partialBanner.style.display = 'none';
        } else if (data.status === 'partial_success') {
            resultTitle.textContent = 'Best Compression Achieved';
            partialBanner.style.display = 'flex';
            partialBannerText.textContent =
                `The ${data.target_mb}MB target is too aggressive for this document. ` +
                `We achieved the best possible compression at ${data.compressed_mb}MB — ` +
                `a ${data.ratio}% reduction from the original.`;
        } else {
            resultTitle.textContent = 'Compression Complete!';
            partialBanner.style.display = 'none';
        }
    }

    downloadBtn.addEventListener('click', () => {
        if (downloadUrl) {
            const a = document.createElement('a');
            a.href = downloadUrl;
            a.download = downloadFilename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        }
    });

    // ============================
    // ERROR
    // ============================
    function showError(message) {
        progressCard.style.display = 'none';
        resultCard.style.display = 'none';
        errorCard.style.display = 'block';
        errorMessage.textContent = message;
    }

    // ============================
    // RESET (Compress Another)
    // ============================
    function resetApp() {
        clearFile();
        uploadCard.style.display = 'block';
        progressCard.style.display = 'none';
        resultCard.style.display = 'none';
        errorCard.style.display = 'none';
        progressBar.style.width = '0%';
        currentProgress = 0;
        downloadUrl = '';
        downloadFilename = '';
        isCompressing = false;
    }

    anotherBtn.addEventListener('click', resetApp);
    retryBtn.addEventListener('click', resetApp);

    // ============================
    // UTILITIES
    // ============================
    function formatFileSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }

})();
