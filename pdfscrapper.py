from telethon import TelegramClient
import os
from dotenv import load_dotenv
import asyncio
import hashlib
from datetime import datetime
from humanize import naturalsize
from typing import Dict, Set

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

# Progress update interval (in seconds)
PROGRESS_UPDATE_INTERVAL = 60  # Show progress every minute

# Directory settings
DOWNLOADS_DIR = 'ebooks'

# Supported e-book formats
EBOOK_FORMATS = {
    'application/pdf': '.pdf',
    'application/epub+zip': '.epub',
    'application/x-mobipocket-ebook': '.mobi',
    'application/vnd.amazon.ebook': '.azw'
}


class DownloadStats:
    def __init__(self):
        self.downloaded_files = 0
        self.skipped_files = 0
        self.total_size = 0
        self.start_time = datetime.now()
        self.last_message_id = None
        self.files_since_last_update = 0
    
    def format_time(self, seconds):
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        seconds = seconds % 60
        return f"{int(hours)}h {int(minutes)}m {int(seconds)}s"
    
    def get_progress_string(self):
        elapsed_time = (datetime.now() - self.start_time).total_seconds()
        if self.downloaded_files > 0:
            avg_speed = self.total_size / elapsed_time
            files_per_minute = (self.downloaded_files / elapsed_time) * 60
            return (
                f"\n=== Download Progress ===\n"
                f"Downloaded: {self.downloaded_files} e-books "
                f"({self.skipped_files} skipped)\n"
                f"Total Size: {naturalsize(self.total_size)}\n"
                f"Average Speed: {naturalsize(avg_speed)}/s\n"
                f"Download Rate: {files_per_minute:.1f} files/minute\n"
                f"Time Elapsed: {self.format_time(elapsed_time)}\n"
                f"Last Message ID: {self.last_message_id}\n"
                f"Files in last minute: {self.files_since_last_update}\n"
                f"========================="
            )
        return "Starting download..."

class TelegramDownloader:
    def __init__(self):
        self.client = TelegramClient('ebook_session', api_id, api_hash)
        self.stats = DownloadStats()
        self.existing_files: Dict[str, str] = {}
        self.processed_ids: Set[int] = set()

    async def initialize(self):
        await self.client.start()
        self.existing_files, _, _ = self.get_existing_files()

    def get_existing_files(self):
        existing_files = {}
        total_size = 0
        file_count = 0
        os.makedirs(DOWNLOADS_DIR, exist_ok=True)
        
        for filename in os.listdir(DOWNLOADS_DIR):
            if filename.lower().endswith(tuple(EBOOK_FORMATS.values())):
                filepath = os.path.join(DOWNLOADS_DIR, filename)
                file_hash = self.get_file_hash(filepath)
                if file_hash:
                    existing_files[file_hash] = filename
                    total_size += os.path.getsize(filepath)
                    file_count += 1
                try:
                    # Add validation for the message ID
                    parts = filename.split('_')
                    if len(parts) > 1:
                        msg_id = int(parts[1].split('.')[0])
                        if msg_id > 0:  # Only add valid positive message IDs
                            self.processed_ids.add(msg_id)
                except (ValueError, IndexError):
                    continue  # Skip if we can't parse the ID
        return existing_files, total_size, file_count

    @staticmethod
    def get_file_hash(filepath):
        if not os.path.exists(filepath):
            return None
        try:
            with open(filepath, 'rb') as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception as e:
            print(f"Error calculating hash for {filepath}: {str(e)}")
            return None

    def is_ebook(self, message):
        if not message or not message.document:
            return False, None
        
        try:
            mime_type = message.document.mime_type
            if mime_type in EBOOK_FORMATS:
                return True, EBOOK_FORMATS[mime_type]
            
            if message.document.attributes:
                for attr in message.document.attributes:
                    if hasattr(attr, 'file_name'):
                        file_ext = os.path.splitext(attr.file_name)[1].lower()
                        if file_ext in EBOOK_FORMATS.values():
                            return True, file_ext
        except Exception as e:
            print(f"Error checking ebook format: {str(e)}")
            return False, None
        
        return False, None

    async def process_single_message(self, message, minute_count, minute_start):
        if not message or not hasattr(message, 'id'):
            return minute_count, False

        if message.id in self.processed_ids:
            self.stats.skipped_files += 1
            return minute_count, False

        is_ebook, ext = self.is_ebook(message)
        if not is_ebook:
            return minute_count, False

        minute_count = await self.handle_rate_limiting(minute_count, minute_start)
        await self.download_ebook(message, ext)
        return minute_count + 1, True

    async def handle_rate_limiting(self, minute_count, minute_start):
        if minute_count >= MAX_DOWNLOADS_PER_MINUTE:
            current_time = datetime.now()
            if (current_time - minute_start).total_seconds() < 60:
                await asyncio.sleep(60 - (current_time - minute_start).total_seconds())
            return 0
        return minute_count

    async def download_ebook(self, message, ext):
        filename = self.generate_filename(message, ext)
        path = await self.client.download_media(
            message.document,
            file=os.path.join(DOWNLOADS_DIR, filename)
        )
        
        if path and os.path.exists(path):
            self.update_stats(message, path)
            print(f"Downloaded: {filename} ({naturalsize(os.path.getsize(path))})")
        await asyncio.sleep(DELAY_BETWEEN_DOWNLOADS)

    def generate_filename(self, message, ext):
        original_name = ""
        for attr in message.document.attributes:
            if hasattr(attr, 'file_name'):
                original_name = attr.file_name
                break
        
        if not original_name:
            original_name = f"ebook_{message.id}{ext}"

        safe_filename = "".join(c for c in original_name if c.isalnum() or c in (' ', '-', '_', '.'))
        return f"{message.id}_{safe_filename}"

    def update_stats(self, message, path):
        file_size = os.path.getsize(path)
        self.stats.total_size += file_size
        self.stats.downloaded_files += 1
        self.stats.files_since_last_update += 1
        self.processed_ids.add(message.id)

    async def download_media(self, start_from_msg_id=None):
        try:
            channel = await self.client.get_entity(channel_username)
            print(f"Connected to channel: {channel_username}")
            print("Starting e-book download...")
            
            minute_count = 0
            minute_start = datetime.now()
            last_progress_update = datetime.now()
            self.stats.files_since_last_update = 0

            message_iterator = self.client.iter_messages(
                channel,
                **({"offset_id": start_from_msg_id} if start_from_msg_id is not None else {})
            )

            async for message in message_iterator:
                self.stats.last_message_id = message.id
                try:
                    minute_count, processed = await self.process_single_message(message, minute_count, minute_start)
                    if processed and self.should_update_progress(last_progress_update):
                        print(self.stats.get_progress_string())
                        last_progress_update = datetime.now()
                        self.stats.files_since_last_update = 0
                except Exception as e:
                    print(f"Error downloading e-book from message {message.id}: {str(e)}")
                    await asyncio.sleep(DELAY_BETWEEN_DOWNLOADS)

            print("\nDownload Complete!")
            print(self.stats.get_progress_string())

        except Exception as e:
            print(f"Error in download_media: {str(e)}")
            raise

    def should_update_progress(self, last_progress_update):
        return (datetime.now() - last_progress_update).total_seconds() >= PROGRESS_UPDATE_INTERVAL

async def main_menu():
    downloader = TelegramDownloader()
    await downloader.initialize()

    while True:
        print("\n=== Telegram E-book Downloader ===")
        print("1. Start new download")
        print("2. Resume from last message")
        print("3. Start from specific message ID")
        print("4. Exit")
        
        choice = input("\nEnter your choice (1-4): ")
        
        if choice == '1':
            await downloader.download_media()
        
        elif choice == '2':
            if downloader.processed_ids:
                try:
                    # Filter out None values and find the maximum valid message ID
                    valid_ids = [id for id in downloader.processed_ids if id is not None]
                    if valid_ids:
                        last_id = max(valid_ids)
                        print(f"Resuming from message ID: {last_id}")
                        await downloader.download_media(start_from_msg_id=last_id)
                    else:
                        print("No valid message ID found. Starting new download...")
                        await downloader.download_media()
                except ValueError:
                    print("No valid message IDs found. Starting new download...")
                    await downloader.download_media()
            else:
                print("No previous downloads found. Starting new download...")
                await downloader.download_media()
        
        elif choice == '3':
            try:
                msg_id = int(input("Enter message ID to start from: "))
                if msg_id > 0:
                    await downloader.download_media(start_from_msg_id=msg_id)
                else:
                    print("Please enter a valid positive message ID.")
            except ValueError:
                print("Invalid message ID. Please enter a number.")
        
        elif choice == '4':
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