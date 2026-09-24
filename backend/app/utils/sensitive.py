import re


_SENSITIVE_PATTERNS = (
    (
        r"(?i)([\"']?(?:password|passwd|pwd|token|access[_-]?token|refresh[_-]?token|api[_-]?key|apikey|authorization|cookie|client[_-]?secret|private[_-]?key|secret|account|username|user|email|账号|用户名|用户|密码|口令|密钥|秘钥)[\"']?\s*[:=：]\s*)([\"'])(?:\\.|(?!\2).)*\2",
        r'\1\2[REDACTED]\2',
    ),
    (
        r'(?i)((?:password|passwd|pwd|token|access[_-]?token|refresh[_-]?token|api[_-]?key|apikey|authorization|cookie|client[_-]?secret|private[_-]?key|secret|account|username|user|email|账号|用户名|用户|密码|口令|密钥|秘钥)\s*[:=：]\s*)([^\s,，;；。]+)',
        r'\1[REDACTED]',
    ),
    (r'(?i)(\bBearer\s+)[^\s,}"\']+', r'\1[REDACTED]'),
    (r'(?i)([?&](?:token|access[_-]?token|api[_-]?key|apikey|password|secret)=)[^&\s]+', r'\1[REDACTED]'),
    (r'(?is)(-----BEGIN [^-]+-----)[\s\S]*?(-----END [^-]+-----)', r'\1[REDACTED]\2'),
    (r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b', '[REDACTED]'),
    (r'(?i)(https?://[^\s/@]+:)[^@\s]+(@)', r'\1[REDACTED]\2'),
)


def redact_sensitive_text(value):
    text = str(value or '')
    for pattern, replacement in _SENSITIVE_PATTERNS:
        text = re.sub(pattern, replacement, text)
    return text
