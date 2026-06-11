DOC_EXTENSIONS = {'docx', 'doc', 'md'}
IMG_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}


def is_document(filename):
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    return ext in DOC_EXTENSIONS


def is_image(filename):
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    return ext in IMG_EXTENSIONS


def read_document_content(file_path):
    ext = file_path.rsplit('.', 1)[1].lower()
    if ext in ('docx', 'doc'):
        try:
            from docx import Document
            doc = Document(file_path)
            text = '\n'.join(p.text for p in doc.paragraphs if p.text.strip())
            return text[:10000]
        except ImportError:
            raise Exception('python-docx not installed. Run: pip install python-docx')
    elif ext == 'md':
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()[:10000]
    return ''