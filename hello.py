from telethon import TelegramClient
import os
from dotenv import load_dotenv
import asyncio
import hashlib
from datetime import datetime
from humanize import naturalsize
from typing import Dict, Set, List
from telethon.tl.types import InputMediaPhoto, InputMediaDocument

# Load environment variables
load_dotenv()

# Credentials
api_id = int(os.getenv('TELEGRAM_API_ID'))
api_hash = os.getenv('TELEGRAM_API_HASH')
channel_username = os.getenv('TELEGRAM_CHANNEL_USERNAME')

# Rate limiting
DELAY_BETWEEN_DOWNLOADS = 1
MAX_DOWNLOADS_PER_MINUTE = 600
MAX_DOWNLOADS_PER_HOUR = 30000

# Directory settings
DOWNLOADS_DIR = 'downloads'

class DownloadStats:
    def __init__(self):
        self.downloaded_files = 0
        self.skipped_files = 0
        self.total_size = 0
        self.start_time = datetime.now()
        self.last_message_id = None
    
    def format_time(self, seconds):
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        seconds = seconds % 60
        return f"{int(hours)}h {int(minutes)}m {int(seconds)}s"
    
    def get_progress_string(self):
        elapsed_time = (datetime.now() - self.start_time).total_seconds()
        if self.downloaded_files > 0:
            avg_speed = self.total_size / elapsed_time
            return (
                f"\n=== Download Progress ===\n"
                f"Downloaded: {self.downloaded_files} files "
                f"({self.skipped_files} skipped)\n"
                f"Total Size: {naturalsize(self.total_size)}\n"
                f"Average Speed: {naturalsize(avg_speed)}/s\n"
                f"Time Elapsed: {self.format_time(elapsed_time)}\n"
                f"Last Message ID: {self.last_message_id}\n"
                f"========================="
            )
        return "Starting download..."

class TelegramDownloader:
    def __init__(self):
        self.client = TelegramClient('image_session', api_id, api_hash)
        self.stats = DownloadStats()
        self.existing_files: Dict[str, str] = {}
        self.processed_ids: Set[int] = set()
        self.target_channels = os.getenv('TELEGRAM_TARGET_CHANNELS', '').split(',')
        self.target_channels = [ch.strip() for ch in self.target_channels if ch.strip()]

    async def initialize(self):
        await self.client.start()
        self.existing_files, _, _ = self.get_existing_files()

    def get_existing_files(self):
        existing_files = {}
        total_size = 0
        file_count = 0
        os.makedirs(DOWNLOADS_DIR, exist_ok=True)
        for filename in os.listdir(DOWNLOADS_DIR):
            if filename.lower().endswith(('.jpg', '.jpeg', '.gif', '.png')):
                filepath = os.path.join(DOWNLOADS_DIR, filename)
                file_hash = self.get_file_hash(filepath)
                if file_hash:
                    existing_files[file_hash] = filename
                    total_size += os.path.getsize(filepath)
                    file_count += 1
                try:
                    msg_id = int(filename.split('_')[1].split('.')[0])
                    self.processed_ids.add(msg_id)
                except:
                    pass
        return existing_files, total_size, file_count

    @staticmethod
    def get_file_hash(filepath):
        if not os.path.exists(filepath):
            return None
        with open(filepath, 'rb') as f:
            return hashlib.md5(f.read()).hexdigest()

    async def upload_to_channels(self, filepath: str) -> None:
        """Upload a file to all configured target channels."""
        if not self.target_channels:
            return

        try:
            # Determine if it's a photo or GIF
            is_gif = filepath.lower().endswith('.gif')
            
            for channel in self.target_channels:
                try:
                    if is_gif:
                        await self.client.send_file(
                            channel,
                            filepath,
                            force_document=True
                        )
                    else:
                        await self.client.send_file(
                            channel,
                            filepath,
                            force_document=False
                        )
                    print(f"Uploaded {filepath} to {channel}")
                    await asyncio.sleep(2)  # Rate limiting between uploads
                except Exception as e:
                    print(f"Failed to upload to {channel}: {str(e)}")

        except Exception as e:
            print(f"Error uploading {filepath}: {str(e)}")

    async def download_media(self, start_from_msg_id=None):
        channel = await self.client.get_entity(channel_username)
        print(f"Connected to channel: {channel_username}")
        print("Starting download...")
        
        download_count = 0
        minute_count = 0
        minute_start = datetime.now()
        last_progress_update = datetime.now()

        async for message in self.client.iter_messages(channel, offset_id=start_from_msg_id):
            if message.id in self.processed_ids:
                self.stats.skipped_files += 1
                continue

            self.stats.last_message_id = message.id
            
            try:
                if message.photo or (message.document and message.document.mime_type == 'image/gif'):
                    if message.id in self.processed_ids:
                        self.stats.skipped_files += 1
                        continue

                    # Rate limiting checks
                    current_time = datetime.now()
                    if minute_count >= MAX_DOWNLOADS_PER_MINUTE:
                        if (current_time - minute_start).total_seconds() < 60:
                            await asyncio.sleep(60 - (current_time - minute_start).total_seconds())
                        minute_count = 0
                        minute_start = datetime.now()

                    # Determine file type and name
                    if message.photo:
                        ext = '.jpg'
                        prefix = 'photo'
                    else:
                        ext = '.gif'
                        prefix = 'gif'

                    filename = f"{prefix}_{message.id}{ext}"
                    path = await self.client.download_media(
                        message.media,
                        file=os.path.join(DOWNLOADS_DIR, filename)
                    )

                    # Update stats
                    file_size = os.path.getsize(path)
                    self.stats.total_size += file_size
                    self.stats.downloaded_files += 1
                    self.processed_ids.add(message.id)

                    print(f'Downloaded {prefix}: {message.id} ({naturalsize(file_size)})')
                    download_count += 1
                    minute_count += 1

                    if (datetime.now() - last_progress_update).total_seconds() >= 10:
                        print(self.stats.get_progress_string())
                        last_progress_update = datetime.now()

                    await asyncio.sleep(DELAY_BETWEEN_DOWNLOADS)

                    if path:
                        await self.upload_to_channels(path)

            except Exception as e:
                print(f"Error downloading media from message {message.id}: {str(e)}")
                await asyncio.sleep(DELAY_BETWEEN_DOWNLOADS)

        print("\nDownload Complete!")
        print(self.stats.get_progress_string())

async def main_menu():
    downloader = TelegramDownloader()
    await downloader.initialize()

    while True:
        print("\n=== Telegram Media Downloader ===")
        print("1. Start new download")
        print("2. Resume from last message")
        print("3. Start from specific message ID")
        print("4. Upload existing files to channels")
        print("5. Exit")
        
        choice = input("\nEnter your choice (1-5): ")
        
        if choice == '1':
            await downloader.download_media()
        
        elif choice == '2':
            last_id = max(downloader.processed_ids) if downloader.processed_ids else None
            if last_id:
                print(f"Resuming from message ID: {last_id}")
                await downloader.download_media(start_from_msg_id=last_id)
            else:
                print("No previous downloads found. Starting new download...")
                await downloader.download_media()
        
        elif choice == '3':
            try:
                msg_id = int(input("Enter message ID to start from: "))
                await downloader.download_media(start_from_msg_id=msg_id)
            except ValueError:
                print("Invalid message ID. Please enter a number.")
        
        elif choice == '4':
            print(f"Target channels: {', '.join(downloader.target_channels)}")
            if not downloader.target_channels:
                print("No target channels configured. Please add TELEGRAM_TARGET_CHANNELS to .env")
                continue
                
            for filename in os.listdir(DOWNLOADS_DIR):
                if filename.lower().endswith(('.jpg', '.jpeg', '.gif', '.png')):
                    filepath = os.path.join(DOWNLOADS_DIR, filename)
                    await downloader.upload_to_channels(filepath)
        
        elif choice == '5':
            print("Exiting...")
            break
        
        else:
            print("Invalid choice. Please try again.")

if __name__ == "__main__":
    try:
        asyncio.run(main_menu())
    except KeyboardInterrupt:
        print("\nDownload interrupted by user")
    except Exception as e:
        print(f"An error occurred: {str(e)}")