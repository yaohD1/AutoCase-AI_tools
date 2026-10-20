import os
from dotenv import load_dotenv

load_dotenv()


def _positive_int_env(name, default, minimum=1):
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _bool_env(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


def _port_env(name, default=0):
    try:
        port = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return port if 0 <= port <= 65535 else default


def _ratio_env(name, default, low=0.2, high=0.8):
    """面板窗口占屏幕宽度的比例，超出范围就退回默认值。"""
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if low <= value <= high else default


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-12345'
    
    UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'storage', 'uploads')
    EXPORT_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'storage', 'exports')
    AUTOMATION_CONTROL_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'storage', 'automation_controls')
    AUTOMATION_WORKSPACE_ROOT = os.environ.get('AUTOMATION_WORKSPACE_ROOT') or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'storage', 'automation_workspaces'
    )
    OPENCODE_BIN = os.environ.get('OPENCODE_BIN') or 'opencode'
    OPENCODE_CONFIG = os.environ.get('OPENCODE_CONFIG')
    OPENCODE_TIMEOUT = _positive_int_env('OPENCODE_TIMEOUT', 3600, 30)
    OPENCODE_PURE = _bool_env('OPENCODE_PURE')
    # Headless OpenCode server used to observe real-time subagent events over the /event SSE bus.
    OPENCODE_SERVE_HOST = os.environ.get('OPENCODE_SERVE_HOST') or '127.0.0.1'
    # 0 means pick a free ephemeral port for each run.
    OPENCODE_SERVE_PORT = _port_env('OPENCODE_SERVE_PORT', 0)
    OPENCODE_SERVE_STARTUP_TIMEOUT = _positive_int_env('OPENCODE_SERVE_STARTUP_TIMEOUT', 60, 5)

    # 录制布局：embed = 被测页面以 iframe 嵌在 AutoCase 前端里（单窗口，左侧完整）；
    # split = 两个并排窗口（旧方案，目标站禁止 iframe 时可用）。
    RECORDING_LAYOUT = (os.environ.get('RECORDING_LAYOUT') or 'embed').strip().lower()
    # 编排面板/外壳页地址（前端录制页）；embed 模式会带上 embed=1&target=...
    RECORDING_PANEL_URL = os.environ.get('RECORDING_PANEL_URL') or 'http://localhost:5173/recording'
    # 面板占屏幕宽度的比例（仅 split 模式用；单屏 55/45 传 0.45）。
    RECORDING_PANEL_RATIO = _ratio_env('RECORDING_PANEL_RATIO', 0.45, 0.25, 0.7)
    # 被录制页面内的悬浮面板：默认关闭，只保留操作点的即时徽标。
    RECORDING_HUD_PANEL = _bool_env('RECORDING_HUD_PANEL', False)
    
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024
    
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'docx', 'doc', 'md'}
    
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'storage', 'app.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY')
    GLM_API_KEY = os.environ.get('GLM_API_KEY')
