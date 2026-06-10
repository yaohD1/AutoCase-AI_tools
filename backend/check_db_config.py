from app import create_app
from app.models import AIConfig, db

app = create_app()

with app.app_context():
    configs = AIConfig.query.all()
    
    print("Current AI Configurations in Database:")
    print("=" * 60)
    
    if len(configs) == 0:
        print("No AI configurations found in database.")
        print("\nPlease add a configuration in Settings page.")
    else:
        for config in configs:
            print(f"ID: {config.id}")
            print(f"Provider: {config.provider}")
            print(f"Model: {config.model}")
            print(f"API Base: {config.api_base}")
            print(f"Is Active: {config.is_active}")
            print("-" * 40)
        
        print(f"\nTotal configurations: {len(configs)}")
        
        # Check if there's a kimi config
        kimi_config = AIConfig.query.filter_by(provider='kimi').first()
        if kimi_config:
            print(f"\nKimi config found:")
            print(f"  Model in DB: {kimi_config.model}")
            print(f"  Expected: kimi-k2.6")
            if kimi_config.model != "kimi-k2.6":
                print(f"\n  ⚠️  WARNING: Model name mismatch!")
                print(f"  Suggestion: Delete this config and add new one in Settings page")
                print(f"  OR update model name to 'kimi-k2.6'")
        else:
            print("\nNo 'kimi' provider configuration found.")
            print("Please add Kimi configuration in Settings page.")