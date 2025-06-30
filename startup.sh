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

# Create a Python virtual environment if it doesn't exist
if [ ! -d "/home/container/venv" ]; then
    echo "Creating virtual environment..."
    /usr/local/bin/python -m venv /home/container/venv
    echo "Virtual environment created."
fi

# Activate the virtual environment and install Python packages from requirements.txt
source /home/container/venv/bin/activate
# Update yt-dlp to the latest version within the virtual environment
echo "Updating yt-dlp..."
pip install -U yt-dlp
echo "yt-dlp updated."
pip install -r /home/container/requirements.txt

# Execute the bot script using the virtual environment's python
/home/container/venv/bin/python /home/container/bot.py