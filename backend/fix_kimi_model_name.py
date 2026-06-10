from app import create_app
from app.models import AIConfig, db

app = create_app()

with app.app_context():
    kimi_config = AIConfig.query.filter_by(provider='kimi').first()
    
    if kimi_config:
        print(f"Found Kimi config:")
        print(f"  Current model: {kimi_config.model}")
        print(f"  Correcting to: kimi-k2.6")
        
        kimi_config.model = "kimi-k2.6"
        db.session.commit()
        
        print(f"  Updated model: {kimi_config.model}")
        print("\n✅ Database updated successfully!")
        print("\nThe config is now:")
        print(f"  Provider: {kimi_config.provider}")
        print(f"  Model: {kimi_config.model}")
        print(f"  Is Active: {kimi_config.is_active}")
        
        print("\nYou can now test generating test cases with Kimi!")
    else:
        print("No Kimi config found in database.")
        print("Please add Kimi configuration in Settings page with model name: kimi-k2.6")