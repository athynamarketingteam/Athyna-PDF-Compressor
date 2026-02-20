import os
import io
import sys
import uuid
import time
import shutil
import tempfile
import traceback
import logging
import gc
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.middleware.proxy_fix import ProxyFix
import fitz  # PyMuPDF
from PIL import Image
import pikepdf

# Fix Windows console encoding
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max upload
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0  # No caching for static files

# Temp directory — use system temp (outside project dir)
UPLOAD_DIR = os.path.join(tempfile.gettempdir(), 'athyna_compressor')
os.makedirs(UPLOAD_DIR, exist_ok=True)
logger.info(f'Temp directory: {UPLOAD_DIR}')


# ============================================================
# Error handlers — catch everything
# ============================================================
@app.errorhandler(413)
def too_large(e):
    logger.error('File too large (413)')
    return jsonify({'error': 'File is too large. Maximum size is 100 MB.'}), 413


@app.errorhandler(500)
def internal_error(e):
    logger.error(f'Internal server error: {e}')
    return jsonify({'error': f'Server error: {str(e)}'}), 500


@app.errorhandler(Exception)
def handle_exception(e):
    logger.error(f'Unhandled exception: {traceback.format_exc()}')
    return jsonify({'error': f'Unexpected error: {str(e)}'}), 500


# ============================================================
# Routes
# ============================================================
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/health')
def health():
    """Simple health check."""
    return jsonify({'status': 'ok', 'temp_dir': UPLOAD_DIR})


@app.route('/favicon.ico')
def favicon():
    """Prevent 404 for favicon requests."""
    return '', 204


# ============================================================
# Compression Engine
# ============================================================
def compress_pdf(input_path, output_path, target_mb):
    """
    Compress a PDF to a target file size in MB.
    Strategy: render pages as images, recompress with adaptive
    JPEG quality, rebuild PDF, apply pikepdf optimizations.
    """
    target_bytes = target_mb * 1024 * 1024
    original_size = os.path.getsize(input_path)
    logger.info(f'Compression start: {original_size / (1024*1024):.1f}MB -> target {target_mb}MB')

    # If already under target, just copy
    if original_size <= target_bytes:
        shutil.copy2(input_path, output_path)
        return {
            'status': 'already_under_target',
            'original_mb': round(original_size / (1024 * 1024), 2),
            'compressed_mb': round(original_size / (1024 * 1024), 2),
            'ratio': 0
        }

    doc = fitz.open(input_path)
    num_pages = len(doc)
    doc.close()
    logger.info(f'PDF has {num_pages} pages')

    # Iterative compression: try progressively lower DPI until target is met
    dpi_steps = [150, 120, 100, 85, 72]

    # For aggressive targets, start lower
    if target_mb <= 5:
        if num_pages > 50:
            dpi_steps = [85, 72, 60]
        elif num_pages > 20:
            dpi_steps = [120, 100, 85, 72]

    temp_pdf_path = output_path + '.temp.pdf'
    final_size = original_size

    for attempt, dpi in enumerate(dpi_steps):
        doc = fitz.open(input_path)
        logger.info(f'Attempt {attempt+1}: DPI={dpi}')

        # Phase 1: Find optimal JPEG quality for this DPI
        best_quality = _find_optimal_quality(doc, dpi, target_bytes, num_pages)
        logger.info(f'  Quality={best_quality}')

        # Phase 2: Build compressed PDF
        _build_compressed_pdf(doc, temp_pdf_path, dpi, best_quality)
        doc.close()
        gc.collect()

        # Phase 3: pikepdf optimization
        _optimize_with_pikepdf(temp_pdf_path, output_path)

        if os.path.exists(temp_pdf_path):
            os.remove(temp_pdf_path)

        final_size = os.path.getsize(output_path)
        logger.info(f'  Result: {final_size / (1024*1024):.1f}MB')

        if final_size <= target_bytes:
            logger.info(f'  Target reached!')
            break

        # If close (within 30%), try one more shot with lower quality at same DPI
        if final_size <= target_bytes * 1.3:
            ratio = target_bytes / final_size
            adjusted_quality = max(15, int(best_quality * ratio * 0.85))
            logger.info(f'  Close! Retrying with quality={adjusted_quality}')
            doc = fitz.open(input_path)
            _build_compressed_pdf(doc, temp_pdf_path, dpi, adjusted_quality)
            doc.close()
            gc.collect()
            _optimize_with_pikepdf(temp_pdf_path, output_path)
            if os.path.exists(temp_pdf_path):
                os.remove(temp_pdf_path)
            final_size = os.path.getsize(output_path)
            logger.info(f'  Retry result: {final_size / (1024*1024):.1f}MB')
            if final_size <= target_bytes:
                logger.info(f'  Target reached on retry!')
                break

    compressed_mb = round(final_size / (1024 * 1024), 2)
    original_mb = round(original_size / (1024 * 1024), 2)
    ratio = round((1 - final_size / original_size) * 100, 1)

    status = 'success' if final_size <= target_bytes else 'partial_success'
    if status == 'partial_success':
        logger.info(f'Partial success: wanted {target_mb}MB, achieved {compressed_mb}MB')

    return {
        'status': status,
        'original_mb': original_mb,
        'compressed_mb': compressed_mb,
        'ratio': ratio,
        'target_mb': target_mb
    }


def _find_optimal_quality(doc, dpi, target_bytes, num_pages):
    """Binary search for optimal JPEG quality."""
    low, high = 20, 85
    best_quality = 50

    sample_pages = min(5, num_pages)
    sample_indices = [int(i * num_pages / sample_pages) for i in range(sample_pages)]

    for iteration in range(6):
        mid = (low + high) // 2
        estimated_total = 0

        for idx in sample_indices:
            page = doc[idx]
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            pix = page.get_pixmap(matrix=mat)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            del pix  # free pixmap immediately

            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=mid, optimize=True)
            page_size = buf.tell()
            estimated_total += page_size
            buf.close()
            del img  # free image immediately

        gc.collect()

        avg_page_size = estimated_total / sample_pages
        projected_total = avg_page_size * num_pages * 1.08

        if projected_total <= target_bytes:
            best_quality = mid
            low = mid + 1
        else:
            high = mid - 1

    return best_quality


def _build_compressed_pdf(doc, output_path, dpi, quality):
    """Build a new PDF with compressed page images — memory-efficient batched approach."""
    num_pages = len(doc)
    batch_size = 5  # Process 5 pages at a time to limit peak RAM
    batch_files = []

    try:
        for batch_start in range(0, num_pages, batch_size):
            batch_end = min(batch_start + batch_size, num_pages)
            batch_doc = fitz.open()

            for page_num in range(batch_start, batch_end):
                page = doc[page_num]
                mat = fitz.Matrix(dpi / 72, dpi / 72)
                pix = page.get_pixmap(matrix=mat)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                del pix  # free pixmap immediately

                img_buf = io.BytesIO()
                img.save(img_buf, format='JPEG', quality=quality, optimize=True, subsampling=2)
                img_buf.seek(0)

                rect = page.rect
                new_page = batch_doc.new_page(width=rect.width, height=rect.height)
                new_page.insert_image(rect, stream=img_buf.read())
                img_buf.close()
                del img  # free image immediately

            # Save batch to disk and free memory
            batch_path = output_path + f'.batch{batch_start}.pdf'
            batch_doc.save(batch_path, garbage=4, deflate=True)
            batch_doc.close()
            batch_files.append(batch_path)
            gc.collect()
            logger.info(f'  Batch {batch_start}-{batch_end-1} saved to disk')

        # Merge all batches using pikepdf (memory efficient)
        if len(batch_files) == 1:
            shutil.move(batch_files[0], output_path)
        else:
            merger = pikepdf.Pdf.new()
            for bf in batch_files:
                src = pikepdf.open(bf)
                merger.pages.extend(src.pages)
                src.close()
                gc.collect()
            merger.save(output_path, linearize=True)
            merger.close()
            gc.collect()

    finally:
        # Clean up batch files
        for bf in batch_files:
            try:
                if os.path.exists(bf):
                    os.remove(bf)
            except Exception:
                pass


def _optimize_with_pikepdf(input_path, output_path):
    """Apply pikepdf structure-level optimizations."""
    with pikepdf.open(input_path) as pdf:
        pdf.save(
            output_path,
            linearize=True,
            object_stream_mode=pikepdf.ObjectStreamMode.generate,
            compress_streams=True,
            recompress_flate=True
        )


# ============================================================
# Compress Endpoint
# ============================================================
@app.route('/compress', methods=['POST'])
def compress():
    """Handle PDF compression request."""
    logger.info(f'=== /compress request received === Method: {request.method}, URL: {request.url}')

    try:
        if 'file' not in request.files:
            logger.error('No file in request')
            return jsonify({'error': 'No file uploaded'}), 400

        file = request.files['file']
        if file.filename == '':
            logger.error('Empty filename')
            return jsonify({'error': 'No file selected'}), 400

        if not file.filename.lower().endswith('.pdf'):
            logger.error(f'Not a PDF: {file.filename}')
            return jsonify({'error': 'Only PDF files are supported'}), 400

        target_mb = int(request.form.get('target_mb', 5))
        if target_mb not in (5, 10):
            return jsonify({'error': 'Target must be 5 or 10 MB'}), 400

        # Save uploaded file
        job_id = str(uuid.uuid4())[:8]
        original_name = Path(file.filename).stem
        input_path = os.path.join(UPLOAD_DIR, f'{job_id}_original.pdf')
        output_filename = f'{original_name}_compressed.pdf'
        output_path = os.path.join(UPLOAD_DIR, f'{job_id}_compressed.pdf')

        logger.info(f'Saving upload: {file.filename} ({target_mb}MB target) -> job {job_id}')
        file.save(input_path)
        file_size = os.path.getsize(input_path)
        logger.info(f'Upload saved: {file_size / (1024*1024):.1f}MB at {input_path}')

        # Run compression
        result = compress_pdf(input_path, output_path, target_mb)
        result['download_id'] = job_id
        result['filename'] = output_filename
        logger.info(f'Done! Result: {result}')
        return jsonify(result)

    except Exception as e:
        logger.error(f'COMPRESSION ERROR:\n{traceback.format_exc()}')
        return jsonify({'error': f'Compression failed: {str(e)}'}), 500

    finally:
        # Clean up original
        try:
            if 'input_path' in locals() and os.path.exists(input_path):
                os.remove(input_path)
        except Exception:
            pass


@app.route('/download/<job_id>')
def download(job_id):
    """Download compressed PDF."""
    filepath = os.path.join(UPLOAD_DIR, f'{job_id}_compressed.pdf')
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found or expired'}), 404

    filename = request.args.get('filename', 'compressed.pdf')
    return send_file(filepath, as_attachment=True, download_name=filename)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"\n  Athyna PDF Compressor")
    print("  " + "-" * 24)
    print(f"  Running at: http://localhost:{port}\n")

    # Use Waitress — a production WSGI server that handles large
    # file uploads reliably (Flask's dev server crashes on large uploads)
    from waitress import serve
    serve(app, host='0.0.0.0', port=port, channel_timeout=300,
          recv_bytes=262144, max_request_body_size=104857600)

