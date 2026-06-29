import os
from flask import Flask
from flask_cors import CORS
from app.config import Config
from app.models.database import init_db

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    
    CORS(app)
    init_db(app)
    
    from app.routes.upload import upload_bp
    from app.routes.testcase import testcase_bp
    from app.routes.export import export_bp
    from app.routes.config import config_bp
    from app.routes.sprint import sprint_bp
    from app.routes.knowledge import knowledge_bp
    
    app.register_blueprint(upload_bp, url_prefix='/api')
    app.register_blueprint(testcase_bp, url_prefix='/api')
    app.register_blueprint(export_bp, url_prefix='/api')
    app.register_blueprint(config_bp, url_prefix='/api')
    app.register_blueprint(sprint_bp, url_prefix='/api')
    app.register_blueprint(knowledge_bp, url_prefix='/api')
    
    return app