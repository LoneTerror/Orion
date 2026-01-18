import os
import urllib.request
import tarfile
import shutil
import platform
import sys

# Detect System Architecture (ARM64 vs x64)
arch = platform.machine().lower()
if "aarch64" in arch or "arm" in arch:
    NODE_DIST = "node-v20.10.0-linux-arm64" # ARM Version
    print(f"🖥️ Detected ARM Architecture ({arch}). Using ARM64 Node.js.")
else:
    NODE_DIST = "node-v20.10.0-linux-x64"   # Standard Intel/AMD Version
    print(f"🖥️ Detected x64 Architecture ({arch}). Using Standard Node.js.")

NODE_URL = f"https://nodejs.org/dist/v20.10.0/{NODE_DIST}.tar.gz"
INSTALL_DIR = os.getcwd()

def install_node():
    node_bin = os.path.join(INSTALL_DIR, NODE_DIST, "bin", "node")
    
    # Check if already installed
    if os.path.exists(node_bin):
        print(f"✅ Node.js is already installed at: {node_bin}")
        return

    print(f"📦 Downloading Node.js from {NODE_URL}...")
    try:
        filename = "node.tar.gz"
        urllib.request.urlretrieve(NODE_URL, filename)
        
        print("📦 Extracting Node.js...")
        with tarfile.open(filename, "r:gz") as tar:
            tar.extractall(path=INSTALL_DIR)
            
        os.remove(filename)
        print(f"✅ Node.js installed successfully!")
        
    except Exception as e:
        print(f"❌ Error installing Node.js: {e}")

if __name__ == "__main__":
    install_node()