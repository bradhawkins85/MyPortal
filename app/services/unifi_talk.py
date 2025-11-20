from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import paramiko
from loguru import logger



async def download_recordings_from_sftp(
    *,
    remote_host: str,
    remote_path: str,
    username: str,
    password: str,
    local_path: str,
    port: int = 22,
) -> dict[str, Any]:
    """
    Download MP3 recordings from Unifi Talk server via SFTP.
    
    Args:
        remote_host: IP address or hostname of the Unifi Talk server
        remote_path: Path to recordings directory on remote server
        username: SFTP username
        password: SFTP password
        local_path: Local directory to download recordings to
        port: SFTP port (default: 22)
    
    Returns:
        Dictionary with download statistics
    """
    # Validate and create local directory if needed
    try:
        local_dir = Path(local_path).expanduser().resolve()
        local_dir.mkdir(parents=True, exist_ok=True)
    except (ValueError, OSError) as e:
        raise ValueError(f"Invalid local path: {local_path}") from e
    
    # Track statistics
    downloaded = 0
    skipped = 0
    errors: list[str] = []
    
    # Connect to SFTP server
    ssh = paramiko.SSHClient()
    
    # Try to load system host keys first for better security
    try:
        ssh.load_system_host_keys()
    except Exception:
        pass  # If system keys aren't available, continue
    
    # Load user known_hosts if available
    known_hosts_path = Path.home() / ".ssh" / "known_hosts"
    if known_hosts_path.exists():
        try:
            ssh.load_host_keys(str(known_hosts_path))
        except Exception:
            pass
    
    # Always use RejectPolicy for security - requires proper known_hosts configuration
    # To configure: ssh-keyscan -H <hostname> >> ~/.ssh/known_hosts
    ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
    
    try:
        logger.info(
            "Connecting to Unifi Talk SFTP server",
            host=remote_host,
            port=port,
            username=username,
        )
        ssh.connect(
            hostname=remote_host,
            port=port,
            username=username,
            password=password,
            timeout=30,
        )
        
        sftp = ssh.open_sftp()
        
        try:
            # List files in remote directory
            logger.info(f"Listing files in remote directory: {remote_path}")
            try:
                remote_files = sftp.listdir(remote_path)
            except FileNotFoundError as e:
                raise FileNotFoundError(f"Remote path does not exist: {remote_path}") from e
            except IOError as e:
                # Paramiko raises IOError for file not found
                raise FileNotFoundError(f"Remote path does not exist: {remote_path}") from e
            
            # Filter for MP3 files only
            mp3_files = [f for f in remote_files if f.lower().endswith('.mp3')]
            logger.info(f"Found {len(mp3_files)} MP3 files on remote server")
            
            # Download each MP3 file
            for filename in mp3_files:
                remote_file_path = f"{remote_path.rstrip('/')}/{filename}"
                local_file_path = local_dir / filename
                
                # Skip if file already exists locally
                if local_file_path.exists():
                    logger.debug(f"Skipping existing file: {filename}")
                    skipped += 1
                    continue
                
                try:
                    logger.info(f"Downloading: {filename}")
                    sftp.get(remote_file_path, str(local_file_path))
                    downloaded += 1
                    logger.info(f"Downloaded: {filename} -> {local_file_path}")
                except Exception as e:
                    error_msg = f"Failed to download {filename}: {str(e)}"
                    logger.error(error_msg)
                    errors.append(error_msg)
        
        finally:
            sftp.close()
    
    except FileNotFoundError as e:
        error_msg = str(e)
        logger.error(error_msg)
        raise FileNotFoundError(error_msg) from e
    
    except paramiko.SSHException as e:
        # Check if this is a host key rejection
        if "not found in known_hosts" in str(e).lower() or "reject" in str(e).lower():
            error_msg = (
                f"Host key verification failed for {remote_host}. "
                f"To fix, add the host key to known_hosts: "
                f"ssh-keyscan -H {remote_host} >> ~/.ssh/known_hosts"
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e
        error_msg = f"SSH connection failed: {str(e)}"
        logger.error(error_msg)
        raise RuntimeError(error_msg) from e
    
    except paramiko.AuthenticationException as e:
        error_msg = f"Authentication failed for {remote_host}: {str(e)}"
        logger.error(error_msg)
        raise ValueError(error_msg) from e
    
    except Exception as e:
        error_msg = f"Unexpected error during SFTP download: {str(e)}"
        logger.error(error_msg)
        raise RuntimeError(error_msg) from e
    
    finally:
        ssh.close()
        logger.info("SFTP connection closed")
    
    return {
        "status": "ok" if not errors else "partial",
        "downloaded": downloaded,
        "skipped": skipped,
        "errors": errors,
        "total_files": len(mp3_files) if 'mp3_files' in locals() else 0,
    }
