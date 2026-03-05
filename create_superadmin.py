import subprocess
import sys
import site

def install_package(package):
    """Install a package using pip"""
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])

def check_and_install_libraries():
    """Check if required libraries are installed, install if missing"""
    required_libraries = {
        'passlib': 'passlib[bcrypt]',
        'psycopg2': 'psycopg2-binary'
    }
    
    # Add user site-packages to path
    try:
        user_site = site.getusersitepackages()
        if user_site not in sys.path:
            site.addsitedir(user_site)
    except Exception:
        pass
    
    missing_libraries = {}
    
    for module_name, package_name in required_libraries.items():
        try:
            __import__(module_name)
            print(f"{module_name} is already installed")
        except ImportError:
            print(f"{module_name} not found. Installing {package_name}...")
            missing_libraries[module_name] = package_name
    
    if missing_libraries:
        print("Installing missing libraries...")
        for module_name, package_name in missing_libraries.items():
            try:
                install_package(package_name)
                print(f"Successfully installed {package_name}")
            except Exception as e:
                print(f"Error installing {package_name}: {e}")
                sys.exit(1)
        
        # Refresh sys.path to include newly installed packages
        try:
            user_site = site.getusersitepackages()
            if user_site not in sys.path:
                site.addsitedir(user_site)
        except Exception:
            pass
        
        # Clear import cache and verify installation
        for module_name in missing_libraries.keys():
            # Remove from cache if already attempted
            if module_name in sys.modules:
                del sys.modules[module_name]
            try:
                __import__(module_name)
                print(f"Verified: {module_name} is now available")
            except ImportError as e:
                print(f"Warning: {module_name} installed but not immediately importable: {e}")
                print("The package was installed successfully. Please run the script again.")
                sys.exit(1)
        
        print("All libraries installed successfully!\n")

# Check and install required libraries
check_and_install_libraries()

from passlib.context import CryptContext
import psycopg2
from datetime import datetime

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Get user details from input
print("Enter Superadmin User Details:")
print("="*80)
name = input("Name: ").strip()
email = input("Email: ").strip()
password = input("Password: ").strip()
hashed_password = pwd_context.hash(password)

# Database connection (update these if needed)
DB_HOST = "127.0.0.1"
DB_PORT = 5432
DB_NAME = "lutron"
DB_USERNAME = "postgres"
DB_PASSWORD = "root"

print(f"\nGenerated password hash: {hashed_password}")
print("\n" + "="*80)
print("SQL INSERT Statement (for reference):")
print("="*80)
print(f"""
INSERT INTO users (name, email, hashed_password, role, change_password, is_active, created_at, updated_at)
VALUES (
    '{name}',
    '{email}',
    '{hashed_password}',
    'Superadmin',
    false,
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
        (name, email, hashed_password, "Superadmin", False, True, datetime.utcnow(), datetime.utcnow())
    )
    conn.commit()
    print(f"Superadmin user '{name}' ({email}) created successfully!")
    print(f"  Password: {password}")
    print(f"  Role: Superadmin")
    
    cursor.close()
    conn.close()
except psycopg2.Error as e:
    print(f"Database error: {e}")
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()


