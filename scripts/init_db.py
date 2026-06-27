from app.core.database import init_db
import app.models.user

if __name__ == "__main__":
    init_db()
    print("Database initialized.")
