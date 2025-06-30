#!/bin/bash
# Bash Startup File for Deploy / Production
# Navigate to the home directory for consistency, though it might already be there
cd /home/container

# Auto-update from Git if enabled
if [[ -d .git ]] && [[ "{{AUTO_UPDATE}}" == "1" ]]; then
    git pull
fi

# Install ffmpeg only if it's not already installed
# This check prevents re-downloading/installing on every startup if it persists
if ! command -v ffmpeg &> /dev/null
then
    echo "ffmpeg not found, installing..."
    # Update package lists
    apt-get update -y
    # Install ffmpeg
    apt-get install -y ffmpeg
    echo "ffmpeg installation complete."
else
    echo "ffmpeg already installed."
fi

# Install Python packages from PY_PACKAGES environment variable
if [[ ! -z "{{PY_PACKAGES}}" ]]; then
    pip install -U --prefix .local {{PY_PACKAGES}}
fi

# Install Python packages from requirements file if it exists
if [[ -f /home/container/${REQUIREMENTS_FILE} ]]; then
    pip install -U --prefix .local -r ${REQUIREMENTS_FILE}
fi

# Execute the bot script
/usr/local/bin/python /home/container/{{PY_FILE}}