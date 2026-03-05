import socket
import os
import shutil
import json
import ssl as ssl_module
from app.models.processor import Processor
from app.database.session import SessionLocal, engine
from sqlalchemy import inspect
from zeroconf import Zeroconf, ServiceBrowser, ServiceListener
from sqlalchemy.orm import Session
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa



def ensure_processor_table():
    inspector = inspect(engine)
    if not inspector.has_table("processor"):
        Processor.__table__.create(bind=engine)


def safe_decode(info, key):
    return info.properties.get(key, b"").decode("ASCII")


# Processor connection check
def is_processor_reachable(ip: str, port: int = 8081) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=2):
            return True
    except Exception:
        return False


class MyListener(ServiceListener):
    def __init__(self):
        self.devices = []

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name)
        if not info:
            return

        ip = str(info._ipv4_addresses[0]) if info._ipv4_addresses else "N/A"

        if not is_processor_reachable(ip):
            print(f" Skipping unreachable processor ") #at {ip} add this to show ip
            return

        device = {
            "server": info.server,
            "ipv4": ip,
            "system": safe_decode(info, b'SYSTYPE'),
            "serial": safe_decode(info, b'SERNUM'),
            "mac": safe_decode(info, b'MACADDR'),
            "claimed": safe_decode(info, b'CLAIM_STATUS'),
            "sw_version": safe_decode(info, b'CODEVER')
        }
        self.devices.append(device)


# Dependency to get a DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


from datetime import datetime
from app.utils.json_connection import create_ssl_connection, send_json, recv_json


# ============================================================================
# CERTIFICATE MANAGEMENT HELPER FUNCTIONS
# ============================================================================

def get_processor_cert_dir(processor_ipv4: str) -> str:
    """
    Get the certificate directory path for a specific processor.
    
    Args:
        processor_ipv4: IPv4 address of the processor (e.g., "192.168.1.100")
    
    Returns:
        str: Path to processor-specific certificate directory
        Example: "app/certificates/192.168.1.100"
    """
    base_cert_dir = "app/certificates"
    processor_cert_dir = os.path.join(base_cert_dir, processor_ipv4)
    return processor_cert_dir


def get_base_cert_dir() -> str:
    """
    Get the base certificate directory path.
    
    Returns:
        str: Path to base certificate directory
        Example: "app/certificates"
    """
    return "app/certificates"


def ensure_processor_cert_dir(processor_ipv4: str) -> tuple[bool, str]:
    """
    Ensure processor-specific certificate directory exists.
    If it doesn't exist, create it and copy ALL files from base certificate folder.
    If it exists, reuse it (for re-handshake).
    
    Args:
        processor_ipv4: IPv4 address of the processor
    
    Returns:
        tuple: (success: bool, message: str)
    """
    try:
        base_cert_dir = get_base_cert_dir()
        processor_cert_dir = get_processor_cert_dir(processor_ipv4)
        
        # Check if processor directory already exists
        if os.path.exists(processor_cert_dir):
            return True, f"Processor certificate directory already exists: {processor_cert_dir} (will update certificates)"
        
        # Create processor certificate directory
        os.makedirs(processor_cert_dir, exist_ok=True)
        
        # Copy ALL files from base certificate directory to processor directory
        files_copied = 0
        for item in os.listdir(base_cert_dir):
            src_path = os.path.join(base_cert_dir, item)
            dst_path = os.path.join(processor_cert_dir, item)
            
            # Only copy files, not directories (skip templates folder, etc.)
            if os.path.isfile(src_path):
                shutil.copy2(src_path, dst_path)
                files_copied += 1
        
        if files_copied == 0:
            return False, f"No certificate files found in base directory: {base_cert_dir}"
        
        return True, f"Created processor certificate directory and copied {files_copied} files from base: {processor_cert_dir}"
    
    except Exception as e:
        return False, f"Failed to setup processor certificate directory: {str(e)}"


# ============================================================================
# CERTIFICATE HANDSHAKE FUNCTIONS (Modified from certificate_manager.py)
# ============================================================================

DEVICE_COMMON_NAME = "Ganakalabs Development"
LAP_PORT = 8083
BUTTON_PRESS_TIMEOUT = 120


def send_json_handshake(sock, json_msg):
    """Send JSON message to socket"""
    send_msg = (json.dumps(json_msg) + "\r\n").encode("ASCII")
    sock.sendall(send_msg)


def recv_json_handshake(sock, timeout=None):
    """Receive JSON message from socket"""
    if timeout:
        sock.settimeout(timeout)
    
    recv_msg = sock.recv(5000)
    
    if timeout:
        sock.settimeout(None)
    
    if len(recv_msg) == 0:
        return None
    
    return json.loads(recv_msg.decode("ASCII"))


def verify_lap_certificates_in_dir(cert_dir: str) -> tuple[bool, str]:
    """
    Verify LAP certificates exist in specified directory.
    
    Args:
        cert_dir: Directory path where certificates are located
    
    Returns:
        tuple: (success: bool, message: str)
    """
    required_files = [
        "lap_private_key.pem",
        "lap_signed_csr.pem",
        "lap_lutron_root.crt",
        "lap_lutron_intermediate.pem",
    ]
    
    missing = []
    for filename in required_files:
        filepath = os.path.join(cert_dir, filename)
        if not os.path.isfile(filepath):
            missing.append(filename)
    
    if missing:
        return False, f"Missing LAP certificates in {cert_dir}: {', '.join(missing)}"
    
    return True, "All LAP certificates found"


def create_lap_chain_in_dir(cert_dir: str) -> tuple[bool, str]:
    """
    Create LAP certificate chain file in specified directory.
    
    Args:
        cert_dir: Directory path where certificates are located
    
    Returns:
        tuple: (success: bool, message: str)
    """
    try:
        chain_file = os.path.join(cert_dir, "lap_lutron_chain.pem")
        root_file = os.path.join(cert_dir, "lap_lutron_root.crt")
        intermediate_file = os.path.join(cert_dir, "lap_lutron_intermediate.pem")
        
        with open(chain_file, "w") as dst_file:
            with open(root_file, "r") as src_file:
                dst_file.write(src_file.read())
            with open(intermediate_file, "r") as src_file:
                dst_file.write(src_file.read())
        
        return True, "LAP certificate chain created"
    except Exception as e:
        return False, f"Failed to create certificate chain: {str(e)}"


def generate_leap_keys_for_processor() -> tuple:
    """
    Generate device-specific private key and CSR for LEAP.
    
    Returns:
        tuple: (private_key, csr, success: bool, message: str)
    """
    try:
        cert_subject = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, DEVICE_COMMON_NAME)
        ])
        
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend()
        )
        
        csr = x509.CertificateSigningRequestBuilder().subject_name(
            cert_subject
        ).sign(private_key, hashes.SHA256(), default_backend())
        
        return private_key, csr, True, "LEAP key pair and CSR generated"
    
    except Exception as e:
        return None, None, False, f"Failed to generate keys: {str(e)}"


def perform_lap_handshake_for_processor(processor_dict: dict, private_key, csr, cert_dir: str) -> tuple[dict, bool, str]:
    """
    Perform LAP certificate exchange with processor.
    
    Args:
        processor_dict: Dict with processor details (ipv4, server, serial, etc.)
        private_key: Private key for LEAP
        csr: Certificate signing request
        cert_dir: Directory where certificates are located
    
    Returns:
        tuple: (certs: dict, success: bool, message: str)
    """
    try:
        lap_chain_file = os.path.join(cert_dir, "lap_lutron_chain.pem")
        lap_signed_csr = os.path.join(cert_dir, "lap_signed_csr.pem")
        lap_private_key = os.path.join(cert_dir, "lap_private_key.pem")
        
        # Setup SSL context with LAP certificates
        context = ssl_module.SSLContext(ssl_module.PROTOCOL_TLS_CLIENT)
        context.verify_mode = ssl_module.CERT_REQUIRED
        context.load_verify_locations(cafile=lap_chain_file)
        context.load_cert_chain(certfile=lap_signed_csr, keyfile=lap_private_key)
        context.check_hostname = False
        
        # Connect to processor
        sock = socket.create_connection((processor_dict['ipv4'], LAP_PORT), timeout=10)
        ssock = context.wrap_socket(sock)
        sock.close()
        
        # Step 1: Send Ping
        send_json_handshake(ssock, {
            "Header": {
                "RequestType": "Ping",
                "ClientTag": "ping"
            }
        })
        
        recv_msg = recv_json_handshake(ssock)
        if not recv_msg or recv_msg["Header"]["StatusCode"] != "204 No Content":
            if recv_msg is None:
                return None, False, "Processor closed connection. Has the processor been set up with Lutron's tools?"
            else:
                return None, False, f"Ping failed: {recv_msg.get('Header', {}).get('StatusCode', 'Unknown error')}"
        
        # Step 2: Wait for physical button press
        recv_msg = recv_json_handshake(ssock, timeout=BUTTON_PRESS_TIMEOUT)
        if not recv_msg or recv_msg["Header"]["StatusCode"] != "200 OK":
            return None, False, "Button press not detected or timeout occurred"
        
        # Step 3: Retrieve processor's root CA certificate
        send_json_handshake(ssock, {
            "Header": {
                "RequestType": "Read",
                "Url": "/certificate/root",
                "ClientTag": "read-root",
            },
        })
        
        root_resp = recv_json_handshake(ssock)
        if not root_resp or root_resp["Header"]["StatusCode"] != "200 OK":
            return None, False, "Failed to retrieve processor root certificate"
        
        proc_root_cert = root_resp["Body"]["Certificate"]["Certificate"]
        
        # Step 4: Send CSR for signing
        send_json_handshake(ssock, {
            "Header": {
                "RequestType": "Execute",
                "Url": "/pair",
                "ClientTag": "get-cert",
            },
            "Body": {
                "CommandType": "CSR",
                "Parameters": {
                    "CSR": csr.public_bytes(serialization.Encoding.PEM).decode('ASCII'),
                    "DisplayName": DEVICE_COMMON_NAME,
                    "DeviceUID": "000000000000"
                },
            },
        })
        
        recv_msg = recv_json_handshake(ssock)
        if not recv_msg or recv_msg["Header"]["StatusCode"] != "200 OK":
            return None, False, "CSR signing failed"
        
        # Extract certificates from response
        signed_csr = recv_msg["Body"]["SigningResult"]["Certificate"]
        proc_intermediate_cert = recv_msg["Body"]["SigningResult"]["RootCertificate"]
        private_key_pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()
        ).decode('ASCII')
        
        ssock.close()
        
        certs = {
            'private_key': private_key_pem,
            'signed_csr': signed_csr,
            'proc_root': proc_root_cert,
            'proc_intermediate': proc_intermediate_cert,
        }
        
        return certs, True, "Certificate handshake completed successfully"
    
    except socket.timeout:
        return None, False, f"Connection timeout - button not pressed within {BUTTON_PRESS_TIMEOUT} seconds"
    except Exception as e:
        return None, False, f"LAP handshake failed: {str(e)}"


def save_leap_certificates_to_dir(certs: dict, cert_dir: str) -> tuple[bool, str]:
    """
    Save LEAP certificates to specified directory.
    
    Args:
        certs: Dictionary containing certificate data
        cert_dir: Directory path where certificates should be saved
    
    Returns:
        tuple: (success: bool, message: str)
    """
    cert_files = [
        ("leap_private_key.pem", certs['private_key']),
        ("leap_signed_csr.pem", certs['signed_csr']),
        ("leap_lutron_proc_root.pem", certs['proc_root']),
        ("leap_lutron_proc_intermediate.pem", certs['proc_intermediate']),
    ]
    
    try:
        os.makedirs(cert_dir, exist_ok=True)
        
        for filename, content in cert_files:
            filepath = os.path.join(cert_dir, filename)
            with open(filepath, "w") as f:
                f.write(content)
        
        return True, f"LEAP certificates saved to {cert_dir}"
    except Exception as e:
        return False, f"Failed to save certificates: {str(e)}"


def enrich_processor_details(db: Session, processor: Processor):
    """
    Connect via LEAP, fetch /project → all /device/{id}, match by MAC, update processor row.
    """
    try:

        with create_ssl_connection(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4) as sock:
            # 1. Read /project
            send_json(sock, {"CommuniqueType": "ReadRequest", "Header": {"Url": "/project"}})
            project_resp = recv_json(sock)

            devices = (
                project_resp.get("Body", {})
                .get("Project", {})
                .get("MasterDeviceList", {})
                .get("Devices", [])
            )

            for dev in devices:
                dev_href = dev.get("href")
                if not dev_href:
                    continue


                # 2. Read each /device/{id}
                send_json(sock, {"CommuniqueType": "ReadRequest", "Header": {"Url": dev_href}})
                dev_resp = recv_json(sock)
                device_body = dev_resp.get("Body", {}).get("Device", {})

                macs = [ni.get("MACAddress") for ni in device_body.get("NetworkInterfaces", [])]

                # Normalize for comparison
                normalized_device_macs = [m.replace(":", "").lower() for m in macs if m]
                normalized_processor_mac = processor.mac.replace(":", "").lower() if processor.mac else ""

                if normalized_processor_mac in normalized_device_macs:

                    processor.associated_area = device_body.get("AssociatedArea", {}).get("href")
                    processor.device_code = device_body.get("href")
                    processor.model_number = device_body.get("ModelNumber")

                    installed = device_body.get("FirmwareImage", {}).get("Installed")
                    if installed:
                        processor.installed_at = datetime(
                            installed["Year"],
                            installed["Month"],
                            installed["Day"],
                            installed["Hour"],
                            installed["Minute"],
                            installed["Second"],
                        )


                    db.commit()
                    break

    except Exception as e:
        pass
