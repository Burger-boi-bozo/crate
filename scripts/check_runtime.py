"""Fail a build early when the tools required for real conversions are missing."""
import shutil
import subprocess
import sys

from yt_dlp.version import __version__

for tool in ("ffmpeg", "ffprobe", "node"):
    if not shutil.which(tool):
        sys.exit(f"Missing required runtime tool: {tool}")
    flag = "--version" if tool == "node" else "-version"
    output = subprocess.check_output([tool, flag], text=True)
    print(output.splitlines()[0])
print(f"yt-dlp {__version__}")
