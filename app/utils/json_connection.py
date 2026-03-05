# E:\Gcon\lutron\lutron_backend_app\app\utils\json_connection.py

import orjson
import ssl
import socket
from app.utils.definitions import (
    LEAP_PRIVATE_KEY_FILE,
    LEAP_SIGNED_CSR_FILE,
    LAP_LUTRON_ROOT_FILE,
    get_proc_hostname,
    get_processor_cert_paths,
)

CRLF = b"\r\n"
MAX_READ_SIZE = 100 * 1024 * 1024  # 100 MB


def create_ssl_connection(ip: str, mac: str, system: str, processor_ipv4: str = None, port: int = 8081, timeout: int = 5):
    """
    Establish SSL connection to Lutron processor using processor-specific certificates.
    
    Args:
        ip: Processor IPv4 address
        mac: Processor MAC address
        system: Processor system type
        processor_ipv4: IPv4 to determine certificate folder (REQUIRED for multi-processor)
        port: Connection port (default: 8081)
        timeout: Connection timeout in seconds
    """
    try:
        hostname = get_proc_hostname(system, mac)
        
        # Get processor-specific certificate paths
        cert_paths = get_processor_cert_paths(processor_ipv4)

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.verify_mode = ssl.CERT_REQUIRED
        context.check_hostname = False
        context.load_verify_locations(cafile=cert_paths['lap_root'])
        context.load_cert_chain(certfile=cert_paths['leap_signed_csr'], keyfile=cert_paths['leap_private_key'])

        raw_sock = socket.create_connection((ip, port), timeout=timeout)
        return context.wrap_socket(raw_sock, server_hostname=hostname)
    except Exception as e:
        print(f"[SSL CONNECTION ERROR] {ip}: {e}")
        return None


def connect_to_processor(ip: str, mac: str, system: str, processor_ipv4: str = None, port: int = 8081, timeout: int = 5):
    """
    Helper function to simplify processor connection using identity info.
    Internally calls create_ssl_connection and returns the SSL-wrapped socket.
    
    Args:
        ip: Processor IPv4 address
        mac: Processor MAC address
        system: Processor system type
        processor_ipv4: IPv4 to determine certificate folder (REQUIRED for multi-processor)
        port: Connection port (default: 8081)
        timeout: Connection timeout in seconds
    """
    return create_ssl_connection(ip=ip, mac=mac, system=system, processor_ipv4=processor_ipv4, port=port, timeout=timeout)


def send_json(sock, data: dict):
    """
    Send orjson-encoded dict as JSON line ending with \r\n to the socket.
    """
    try:
        sock.sendall(orjson.dumps(data) + CRLF)
    except Exception as e:
        print(f"[SEND ERROR] {e}")


def recv_json(sock):
    """
    Receive JSON response from socket until CRLF.
    Returns parsed dict or None on failure.
    """
    buffer = b""
    try:
        while True:
            chunk = sock.recv(8192)
            if not chunk:
                break
            buffer += chunk

            if len(buffer) > MAX_READ_SIZE or CRLF in buffer:
                break

        first = buffer.split(CRLF)[0].strip()
        return orjson.loads(first)
    except orjson.JSONDecodeError as e:
        print(f"[DECODE ERROR] {e}")
    except Exception as e:
        print(f"[RECV ERROR] {e}")
    return None

def get_area_full_path_from_processor(ip: str, mac: str, system: str, area_code: str, processor_ipv4: str = None) -> str:
    """
    Resolve full hierarchical path (Floor/Room/...) of an area by walking
    the parent chain starting from the given area_code using LEAP ReadRequests.
    Returns a path string like "Floor 1/Conference Room" or None on error.
    
    Args:
        ip: Processor IPv4 address
        mac: Processor MAC address
        system: Processor system type
        area_code: Area code to resolve path for
        processor_ipv4: IPv4 to determine certificate folder (REQUIRED for multi-processor)
    """
    if not area_code:
        return None

    sock = None
    try:
        sock = connect_to_processor(ip=ip, mac=mac, system=system, processor_ipv4=processor_ipv4)
        if not sock:
            return None

        path_parts = []
        current_href = f"/area/{area_code}"

        while current_href:
            send_json(sock, {"CommuniqueType": "ReadRequest", "Header": {"Url": current_href}})
            resp = recv_json(sock)
            area = resp.get("Body", {}).get("Area")
            if not area:
                break

            name = area.get("Name")
            if name:
                path_parts.insert(0, name)

            parent_href = area.get("Parent", {}).get("href")
            current_href = parent_href if parent_href else None

        return "/".join(path_parts)
    except Exception as e:
        print(f"[AREA PATH ERROR] {e}")
        return None
    finally:
        if sock:
            sock.close()
