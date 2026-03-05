from passlib.context import CryptContext
import psycopg2
from datetime import datetime

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# User details
name = "Harsha"
email = "harshap2ganakalabs.in"
password = "Harsha@12"
hashed_password = pwd_context.hash(password)

# Database connection (update these if needed)
DB_HOST = "127.0.0.1"
DB_PORT = 5432
DB_NAME = "lutron"
DB_USERNAME = "postgres"
DB_PASSWORD = "root"

print(f"Generated password hash: {hashed_password}")
print("\n" + "="*80)
print("SQL INSERT Statement (for reference):")
print("="*80)
print(f"""
INSERT INTO users (name, email, hashed_password, role, change_password, is_active, created_at, updated_at)
VALUES (
    'Harsha',
    'harshap2ganakalabs.in',
    '{hashed_password}',
    'Superadmin',
    true,
    true,
    NOW(),
    NOW()
);
""")
print("="*80 + "\n")

try:
    conn = psycopg2.connect(
        host=DB_HOST,
        database=DB_NAME,
        user=DB_USERNAME,
        password=DB_PASSWORD,
        port=DB_PORT
    )
    cursor = conn.cursor()
    
    # Delete existing user if exists
    cursor.execute("DELETE FROM users WHERE email = %s", (email,))
    deleted_count = cursor.rowcount
    if deleted_count > 0:
        print(f"Deleted {deleted_count} existing user(s) with email {email}")
        conn.commit()
    
    # Insert new superadmin user
    cursor.execute(
        """INSERT INTO users (name, email, hashed_password, role, change_password, is_active, created_at, updated_at) 
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (name, email, hashed_password, "Superadmin", True, True, datetime.utcnow(), datetime.utcnow())
    )
    conn.commit()
    print(f"✓ Superadmin user '{name}' ({email}) created successfully!")
    print(f"  Password: {password}")
    print(f"  Role: Superadmin")
    
    cursor.close()
    conn.close()
except psycopg2.Error as e:
    print(f"✗ Database error: {e}")
except Exception as e:
    print(f"✗ Error: {e}")
    import traceback
    traceback.print_exc()


