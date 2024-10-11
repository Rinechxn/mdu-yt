import os
import re
import subprocess
import sys
import platform
import shutil
from PyQt5.QtCore import QObject, pyqtSignal
from pathlib import Path
from env import root
import yt_dlp
import json  # Make sure to import the json module

class DownloaderSignals(QObject):
    progress = pyqtSignal(float, str, str, str, int, int)
    title_fetched = pyqtSignal(str)  # New signal for title fetching
    file_downloaded = pyqtSignal(str, str, str)
    finished = pyqtSignal()
    error = pyqtSignal(str)

class Downloader(QObject):
    def __init__(self):
        super().__init__()
        self.system = platform.system().lower()
        self.workdir = self.get_workdir()
        self.yt_dlp_binary = self.get_yt_dlp_binary()
        self.ffmpeg_binary = self.get_ffmpeg_binary()
        self.signals = DownloaderSignals()
        self.process = None
        self.stop_flag = False
        self.video_file = None
        self.audio_file = None
        self.download_dir = None

    rootpath = root
    def get_workdir(self):
        if self.system == 'windows':
            return os.path.join(self.rootpath,'bin', 'win')
        elif self.system == 'darwin':
            return os.path.join(self.rootpath,'bin', 'mac')
        elif self.system == 'linux':
            return '/usr/local/bin'  # Default location for user-installed binaries
        else:
            raise OSError(f"Unsupported operating system: {self.system}")
    
    def get_yt_dlp_binary(self):
        if self.system == 'windows':
            return os.path.join(self.workdir, 'yt-dlp.exe')
        elif self.system == 'darwin':
            return os.path.join(self.workdir, 'yt-dlp')
        elif self.system == 'linux':
            return self.get_linux_binary('yt-dlp')
        else:
            raise OSError(f"Unsupported operating system: {self.system}")
    
    def get_ffmpeg_binary(self):
        if self.system == 'windows':
            return os.path.join(self.workdir, 'ffmpeg.exe')
        elif self.system == 'darwin':
            return os.path.join(self.workdir, 'ffmpeg')
        elif self.system == 'linux':
            return self.get_linux_binary('ffmpeg')
        else:
            raise OSError(f"Unsupported operating system: {self.system}")        

    def get_linux_binary(self, binary_name):
        if shutil.which(binary_name):
            return binary_name

        package_managers = [
            ('apt-get', f'apt install -y {binary_name}'),
            ('pacman', f'pacman -S --noconfirm {binary_name}'),
            ('dnf', f'dnf install -y {binary_name}'),
            ('yum', f'yum install -y {binary_name}'),
            ('zypper', f'zypper install -y {binary_name}')
        ]

        for pm, install_cmd in package_managers:
            if shutil.which(pm):
                try:
                    subprocess.run(['sudo', pm, 'update'], check=True)
                    subprocess.run(['sudo'] + install_cmd.split(), check=True)
                    return binary_name
                except subprocess.CalledProcessError:
                    print(f"Failed to install {binary_name} using {pm}")

        print(f"Using bundled {binary_name} binary")
        return os.path.join(bin, 'linux', binary_name)
    
    def fetch_title(self, url):
        """Gets the video title from yt-dlp without downloading."""
        try:
            ydl_opts = {'quiet': True, 'no_warnings': True}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Extracting video info without downloading
                info = ydl.extract_info(url, download=False)

            # Convert the info dictionary to JSON format
            info_json = json.dumps(info, indent=4)  # Pretty-print JSON with an indent of 4 spaces
            print(info_json)  # Print the JSON output

            title = info.get('title', None)  # Get the title from the info
            if title:
                self.signals.title_fetched.emit(title)  # Emit signal with the title
            return title  # Return the title
        except Exception as e:
            self.signals.error.emit(f"Failed to fetch title: {str(e)}")  # Emit error signal
            return None



    def download(self, url, is_audio, audio_format, resolution, fps, download_dir, is_playlist, with_thumbnail, get_title):
        self.download_dir = download_dir
        self.video_file = None
        self.audio_file = None

        # Fetch title if get_title is True
        if get_title:
            title = self.fetch_title(url)
            if title is None:
                return  # If title fetching failed, exit early

        try:
            self.stop_flag = False
            cmd = [self.yt_dlp_binary, url, '--no-mtime', '--newline']
            print(self.yt_dlp_binary)

            cmd.extend(['-P', download_dir])

            if is_playlist:
                cmd.extend(['--output', '%(playlist_title)s/%(title)s.%(ext)s'])
            else:
                cmd.extend(['--output', '%(title)s.%(ext)s'])

            if with_thumbnail:
                cmd.extend(["--embed-thumbnail", "--embed-metadata"])

            if is_audio:
                cmd.extend(['-x', '--audio-format', audio_format])
            else:
                format_string = f"bestvideo[height<={resolution}][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
                cmd.extend(['-f', format_string])
                if fps:
                    cmd.append(f'--fps={fps}')

            cmd.append('--yes-playlist' if is_playlist else '--no-playlist')

            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                creationflags=subprocess.CREATE_NO_WINDOW if self.system == 'windows' else 0
            )

            current_item = 0
            total_items = 1
            for line in self.process.stdout:
                if self.stop_flag:
                    self.process.terminate()
                    self.signals.error.emit("Download stopped by user")
                    return

                if '[download] Downloading item' in line:
                    match = re.search(r'item (\d+) of (\d+)', line)
                    if match:
                        current_item = int(match.group(1))
                        total_items = int(match.group(2))
                elif '[download]' in line:
                    progress, file_size, download_speed, eta = self.parse_progress(line)
                    self.signals.progress.emit(progress, file_size, download_speed, eta, current_item, total_items)
                elif '[download] Destination:' in line:
                    self.parse_destination(line)
                elif '[ExtractAudio] Destination:' in line or '[Merger] Merging formats into' in line:
                    filename = os.path.basename(line.split('"')[-2]) if '"' in line else line.split(':')[-1].strip()
                    file_path = os.path.join(download_dir, filename)
                    file_type = "Audio" if is_audio else "Video"
                    self.signals.file_downloaded.emit(filename, file_path, file_type)

            self.process.wait()
            if self.process.returncode != 0 and not self.stop_flag:
                self.signals.error.emit(f"yt-dlp exited with code {self.process.returncode}")
            elif not self.stop_flag:
                self.signals.finished.emit()

        except Exception as e:
            self.signals.error.emit(str(e))
            print(self.yt_dlp_binary)

    def stop(self):
        self.stop_flag = True
        if self.process:
            self.process.terminate()

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

        return progress, file_size, download_speed, eta

    def parse_destination(self, line):
        match = re.search(r'\[download\] Destination: (.+)$', line)
        if match:
            filename = match.group(1)
            file_path = os.path.join(self.download_dir, filename)
            
            # Check if it's a video file
            if any(filename.endswith(ext) for ext in ['.mp4', '.webm', '.mkv']):
                self.video_file = file_path
            # Check if it's an audio file
            elif any(filename.endswith(ext) for ext in ['.m4a', '.mp3', '.opus', '.wav']):
                self.audio_file = file_path
            
            # If we have both video and audio files, we can stop parsing
            if self.video_file and self.audio_file:
                return

    def processing_clip(self, output_file, codec='libx264', bitrate='5M', preset='fast'):
        if not self.video_file:
            self.signals.error.emit("Video file not available for processing")
            return

        try:
            output_path, output_filename = os.path.split(output_file)
            output_name, _ = os.path.splitext(output_filename)

            cmd = [
                self.ffmpeg_binary,
                '-y',  # Overwrite output files without asking
                '-i', self.video_file,  # Input video file
            ]

            if self.audio_file:
                cmd.extend(['-i', self.audio_file])  # Input audio file if available

            cmd.extend([
                '-c:v', codec,  # Video codec
                '-c:a', 'aac',  # Audio codec
                '-b:v', bitrate,  # Video bitrate
                '-preset', preset,  # Encoding preset
                '-strict', 'experimental',  # Allow experimental codecs
                f'{os.path.join(output_path, output_name)}.mp4'  # Output file
            ])

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                creationflags=subprocess.CREATE_NO_WINDOW if self.system == 'windows' else 0
            )

            for line in process.stdout:
                # You can add progress parsing here if needed
                print(line.strip())

            process.wait()
            if process.returncode != 0:
                self.signals.error.emit(f"FFmpeg exited with code {process.returncode}")
            else:
                output_file_path = f'{os.path.join(output_path, output_name)}.mp4'
                self.signals.file_downloaded.emit(os.path.basename(output_file_path), output_file_path, "Processed Clip")

        except Exception as e:
            self.signals.error.emit(str(e))