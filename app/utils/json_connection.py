# E:\Gcon\lutron\lutron_backend_app\app\utils\json_connection.py

import orjson
import ssl
import socket
import time
from app.utils.definitions import (
    LEAP_PRIVATE_KEY_FILE,
    LEAP_SIGNED_CSR_FILE,
    LAP_LUTRON_ROOT_FILE,
    get_proc_hostname,
    get_processor_cert_paths,
)

CRLF = b"\r\n"
MAX_READ_SIZE = 100 * 1024 * 1024  # 100 MB


def create_ssl_connection(
    ip: str,
    mac: str,
    system: str,
    processor_ipv4: str = None,
    port: int = 8081,
    timeout: int = 5,
    processor_id: int = None,
):
    """
    Establish SSL connection to Lutron processor using processor-specific certificates.
    """
    t0 = time.perf_counter()
    try:
        hostname = get_proc_hostname(system, mac)
        
        cert_paths = get_processor_cert_paths(processor_ipv4)

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.verify_mode = ssl.CERT_REQUIRED
        context.check_hostname = False
        context.load_verify_locations(cafile=cert_paths['lap_root'])
        context.load_cert_chain(certfile=cert_paths['leap_signed_csr'], keyfile=cert_paths['leap_private_key'])

        raw_sock = socket.create_connection((ip, port), timeout=timeout)
        sock = context.wrap_socket(raw_sock, server_hostname=hostname)
        try:
            from app.monitoring.leap_connection_limits import report_connect_outcome

            report_connect_outcome(
                success=True,
                processor_id=processor_id,
                ipv4=processor_ipv4 or ip,
                duration_ms=(time.perf_counter() - t0) * 1000.0,
                source="json_connection",
            )
        except Exception:
            pass
        return sock
    except Exception as e:
        print(f"[SSL CONNECTION ERROR] {ip}: {e}")
        try:
            from app.monitoring.leap_connection_limits import report_connect_outcome

            report_connect_outcome(
                success=False,
                processor_id=processor_id,
                ipv4=processor_ipv4 or ip,
                duration_ms=(time.perf_counter() - t0) * 1000.0,
                error=e,
                source="json_connection",
            )
        except Exception:
            pass
        return None


def connect_to_processor(
    ip: str,
    mac: str,
    system: str,
    processor_ipv4: str = None,
    port: int = 8081,
    timeout: int = 5,
    processor_id: int = None,
):
    """
    Helper function to simplify processor connection using identity info.
    """
    return create_ssl_connection(
        ip=ip,
        mac=mac,
        system=system,
        processor_ipv4=processor_ipv4,
        port=port,
        timeout=timeout,
        processor_id=processor_id,
    )


def send_json(sock, data: dict):
    """
    Send orjson-encoded dict as JSON line ending with \r\n to the socket.
    """
    try:
        sock.sendall(orjson.dumps(data) + CRLF)
    except Exception as e:
        print(f"[SEND ERROR] {e}")


def recv_json(sock, *, processor_id: int = None):
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
        parsed = orjson.loads(first)
        try:
            from app.monitoring.leap_connection_limits import report_leap_header_status

            if isinstance(parsed, dict):
                report_leap_header_status(
                    processor_id=processor_id,
                    header=parsed.get("Header"),
                    source="json_connection.recv_json",
                )
        except Exception:
            pass
        return parsed
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
            if not isinstance(resp, dict):
                break
            area = (resp.get("Body") or {}).get("Area")
            if not area:
                break

            name = area.get("Name")
            if name:
                path_parts.insert(0, name)

            parent = area.get("Parent") or {}
            parent_href = parent.get("href") if isinstance(parent, dict) else None
            current_href = parent_href if parent_href else None

        return "/".join(path_parts)
    except Exception as e:
        print(f"[AREA PATH ERROR] {e}")
        return None
    finally:
        if sock:
            sock.close()
