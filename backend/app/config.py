import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-12345'
    
    UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'storage', 'uploads')
    EXPORT_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'storage', 'exports')
    
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024
    
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'docx', 'doc', 'md'}
    
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'storage', 'app.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY')
    GLM_API_KEY = os.environ.get('GLM_API_KEY')