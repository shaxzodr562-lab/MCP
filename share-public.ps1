# Gives the locally running web app (port 8000) a temporary PUBLIC https link.
# Requires: run-web.bat already running. Uses the free localhost.run service over SSH (no account, nothing to install).
# The link works only while this window stays open and your computer is on; closing the window stops the sharing.
# Anyone with the link can use the app (and see only public SIAT statistics; no keys or personal data are exposed).
ssh -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -R 80:127.0.0.1:8000 nokey@localhost.run
