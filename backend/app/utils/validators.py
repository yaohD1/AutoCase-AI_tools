import re

def validate_email(email: str) -> bool:
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def validate_priority(priority: str) -> bool:
    return priority in ['P0', 'P1', 'P2']

def validate_case_type(case_type: str) -> bool:
    return case_type in ['functional', 'ui', 'boundary', 'exception']

def validate_project_name(name: str) -> bool:
    return len(name.strip()) >= 2 and len(name.strip()) <= 200

def sanitize_filename(filename: str) -> str:
    filename = re.sub(r'[^\w\s-.]', '', filename)
    filename = re.sub(r'[-\s]+', '-', filename)
    return filename.strip('._-')