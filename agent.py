import json
import time
import os
import subprocess
import socket

# Load configuration
config = {
    "UPSTAT_API_KEY": os.environ.get("UPSTAT_API_KEY", ""),
    "UPSTAT_BACKEND_URL": os.environ.get("UPSTAT_BACKEND_URL", "https://upstat.yogeshramadoss.cloud"),
    "UPSTAT_LOG_TYPE": os.environ.get("UPSTAT_LOG_TYPE", "docker"),
}

config_path = "/etc/upstat-log-agent/config"
if os.path.exists(config_path):
    with open(config_path) as f:
        for line in f:
            line = line.strip()
            if line and '=' in line and not line.startswith('#'):
                name, val = line.split('=', 1)
                config[name.strip()] = val.strip()

API_KEY = config["UPSTAT_API_KEY"]
BACKEND_URL = config["UPSTAT_BACKEND_URL"]
LOG_TYPE = config["UPSTAT_LOG_TYPE"]
STATE_FILE = "/etc/upstat-log-agent/state.json"

if not API_KEY:
    print("Error: UPSTAT_API_KEY is not configured.")
    time.sleep(10)
    exit(1)

# Keep track of the last read log timestamp per container
state = {}
if os.path.exists(STATE_FILE):
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except:
        pass

def save_state():
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except:
        pass

def detect_level(message):
    msg_lower = message.lower()
    if 'error' in msg_lower or 'err' in msg_lower or 'exception' in msg_lower or 'fail' in msg_lower or 'fatal' in msg_lower:
        return 'ERROR'
    elif 'warn' in msg_lower or 'warning' in msg_lower:
        return 'WARN'
    elif 'debug' in msg_lower:
        return 'DEBUG'
    return 'INFO'

def get_docker_containers():
    containers = []
    try:
        out = subprocess.check_output(["docker", "ps", "--format", "{{.ID}}\t{{.Names}}"], text=True)
        for line in out.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) == 2:
                containers.append({"id": parts[0], "name": parts[1]})
    except Exception as e:
        print("Error listing docker containers:", e)
    return containers

def get_container_logs(container_id, container_name):
    logs = []
    last_ts = state.get(container_id)
    
    cmd = ["docker", "logs", "--timestamps"]
    if last_ts:
        cmd.extend(["--since", last_ts])
    else:
        cmd.extend(["--since", "10s"])
        
    cmd.append(container_id)
    
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        new_last_ts = last_ts
        
        for line in out.splitlines():
            if not line:
                continue
            parts = line.split(" ", 1)
            if len(parts) == 2:
                line_ts, message = parts
                
                # Simple validation of timestamp format
                if 'T' in line_ts and ('Z' in line_ts or '+' in line_ts):
                    if last_ts and line_ts <= last_ts:
                        continue
                        
                    logs.append({
                        "logType": "DOCKER",
                        "logLevel": detect_level(message),
                        "message": message,
                        "containerName": container_name,
                        "containerId": container_id,
                        "timestamp": line_ts
                    })
                    
                    if not new_last_ts or line_ts > new_last_ts:
                        new_last_ts = line_ts
                    
        if new_last_ts:
            state[container_id] = new_last_ts
            
    except Exception as e:
        print(f"Error getting logs for container {container_name}: {e}")
        
    return logs

def send_logs(logs):
    if not logs:
        return
        
    payload = {"logs": logs}
    url = f"{BACKEND_URL}/api/logs/ingest"
    
    try:
        cmd = [
            "curl",
            "-s",
            "-w", "\n%{http_code}",
            "-X", "POST",
            "-H", "Content-Type: application/json",
            "-H", f"Authorization: Bearer {API_KEY}",
            "-d", "@-",
            url
        ]
        res = subprocess.run(cmd, input=json.dumps(payload), capture_output=True, text=True, timeout=15)
        if res.returncode != 0:
            print("Error executing curl to send logs:", res.stderr)
            return
            
        parts = res.stdout.rsplit("\n", 1)
        if len(parts) == 2:
            body, http_code = parts
            http_code = http_code.strip()
            if http_code not in ("200", "201", "204"):
                print(f"Error sending logs to backend: HTTP Error {http_code}")
                print("Response body:", body)
        else:
            print("Error parsing curl response:", res.stdout)
    except Exception as e:
        print("Error sending logs to backend:", e)

def main():
    print("UpStat Docker Log Agent started.")
    print("Backend URL:", BACKEND_URL)
    print("Log type:", LOG_TYPE)
    
    while True:
        containers = get_docker_containers()
        batch = []
        for c in containers:
            batch.extend(get_container_logs(c["id"], c["name"]))
            
        if batch:
            chunk_size = 500
            for i in range(0, len(batch), chunk_size):
                send_logs(batch[i:i + chunk_size])
            save_state()
            
        time.sleep(5)

if __name__ == "__main__":
    main()
