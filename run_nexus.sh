#!/bin/bash

# 1. Go to the project folder
cd /Users/mustafaasghari/code/Nexus

# 2. Activate Python environment (source your venv)
# Verify if your venv is named 'venv' or '.venv'
source vnev/bin/activate

# 3. Run Nexus in the background
# We pipe output to a log file so you can check errors if it breaks
/Users/mustafaasghari/code/Nexus/vnev/bin/python3 nexus.py >> nexus.log 2>&1