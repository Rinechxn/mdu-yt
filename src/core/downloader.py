import os
import re
import subprocess
import sys
from PySide6.QtCore import QObject, Signal

workdir = os.path.join(os.path.dirname(__file__),'bin')
class DownloaderSignals(QObject):
    progress = Signal(float, str, str, str)
    error = Signal(str)
    finished = Signal(str, str, str)  # filename, file_path, file_type

class Downloader(QObject):
    def __init__(self):
        super().__init__()
        self.signals = DownloaderSignals()

    def download(self, url, is_audio, audio_format, resolution, fps, download_dir):
        try:
            # Construct the command with the working directory set to the bin directory
            cmd = ['yt-dlp.exe', url, '--newline', '-P', download_dir]
            if is_audio:
                cmd.extend(['-x', '--audio-format', audio_format])
            else:
                # Construct video format string based on resolution
                format_string = f"bestvideo[height<={resolution}][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
                cmd.extend(['-f', format_string])
                if fps:
                    cmd.append(f'--fps={fps}')

            # Run the subprocess with the bin directory as the working directory
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                cwd=workdir,  # Set working directory
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            
            filename = None
            for line in process.stdout:
                if '[download]' in line:
                    self.parse_progress(line)
                elif 'ERROR:' in line:
                    self.signals.error.emit(line.strip())
                elif '[ExtractAudio] Destination:' in line:
                    filename = line.split('Destination:')[1].strip()

            process.wait()
            if process.returncode != 0:
                self.signals.error.emit(f"yt-dlp exited with code {process.returncode}")
            else:
                if not filename:
                    filename = self.get_last_modified_file(download_dir)
                file_path = os.path.join(download_dir, filename)
                file_type = "Audio" if is_audio else "Video"
                self.signals.finished.emit(filename, file_path, file_type)
        except Exception as e:
            self.signals.error.emit(str(e))
            print(os.path.join(os.path.dirname(__file__),".", 'bin'))

    def parse_progress(self, line):
        progress = 0
        file_size = ""
        download_speed = ""
        eta = ""

        match = re.search(r'(\d+(?:\.\d+)?)%', line)
        if match:
            progress = float(match.group(1))

        size_match = re.search(r'of\s+(\S+)', line)
        if size_match:
            file_size = size_match.group(1)

        speed_match = re.search(r'at\s+(\S+)', line)
        if speed_match:
            download_speed = speed_match.group(1)

        eta_match = re.search(r'ETA\s+(\S+)', line)
        if eta_match:
            eta = eta_match.group(1)

        self.signals.progress.emit(progress, file_size, download_speed, eta)

    def get_last_modified_file(self, directory):
        files = [os.path.join(directory, f) for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f))]
        if not files:
            return None
        return max(files, key=os.path.getmtime)
