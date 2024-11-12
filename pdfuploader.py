import os
from telethon import TelegramClient
from telethon.tl.types import Channel, Chat
from dotenv import load_dotenv
import asyncio
import json
from datetime import datetime
import time
from humanize import naturalsize
from pathlib import Path
import argparse

# Load environment variables
load_dotenv()

class TelegramUploader:
    def __init__(self):
        self.api_id = int(os.getenv('TELEGRAM_API_ID'))
        self.api_hash = os.getenv('TELEGRAM_API_HASH')
        self.target_names = os.getenv('TELEGRAM_TARGETS', '').split(',')
        self.target_names = [name.strip() for name in self.target_names if name.strip()]
        
        self.media_folder = Path(os.getenv('MEDIA_FOLDER_PATH', 'media_files'))
        self.history_file = Path(os.getenv('HISTORY_FILE_PATH', 'upload_history.json'))
        
        self.client = TelegramClient('uploader_session', self.api_id, self.api_hash)
        self.upload_history = self.load_history()
        self.targets = []
        
        self.stats = {
            'uploaded': 0,
            'skipped': 0,
            'failed': 0,
            'total_size': 0,
            'last_file': None
        }

    def load_history(self) -> dict:
        """Load upload history from file"""
        try:
            if self.history_file.exists():
                return json.loads(self.history_file.read_text())
            return {}
        except Exception as e:
            print(f"Error loading history: {e}")
            return {}

    def save_history(self):
        """Save upload history to file"""
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        self.history_file.write_text(json.dumps(self.upload_history))

    async def _get_entity_from_name(self, target_name: str):
        """Helper to get entity from name or ID"""
        try:
            # If it's a numeric ID
            if str(target_name).replace('-', '').isdigit():
                # Remove any existing -100 prefix if present
                clean_id = str(target_name).replace('-100', '')
                # Try different ID formats
                for id_format in [
                    int(target_name),  # Original format
                    int(f"-100{clean_id}"),  # With -100 prefix
                    int(clean_id)  # Without any prefix
                ]:
                    try:
                        return await self.client.get_entity(id_format)
                    except ValueError:
                        continue
                        
            # If it's a username/channel name
            for name_format in [
                target_name,
                f"@{target_name}",
                target_name.lower(),
                f"@{target_name.lower()}"
            ]:
                try:
                    return await self.client.get_entity(name_format)
                except ValueError:
                    continue
                    
            raise ValueError(f"Could not find channel: {target_name}")
        except Exception as e:
            raise ValueError(f"Invalid target format: {target_name} ({str(e)})")

    async def initialize_targets(self):
        """Initialize target channels/groups directly from env config"""
        print("Initializing configured targets...")
        
        for target_name in self.target_names:
            try:
                entity = await self._get_entity_from_name(target_name.strip().strip('@'))
                
                if isinstance(entity, (Channel, Chat)):
                    self.targets.append(entity)
                    print(f"✓ Added target: {entity.title}")
                else:
                    print(f"✗ Skipped {target_name}: Not a channel or group")
            except Exception as e:
                print(f"✗ Failed to add target {target_name}: {str(e)}")

        self._print_target_summary()

    def _print_target_summary(self):
        """Print summary of initialized targets"""
        if self.targets:
            print(f"\nSuccessfully initialized {len(self.targets)} targets:")
            for target in self.targets:
                print(f"  • {target.title}")
        else:
            print("\nNo valid targets found. Please check your TELEGRAM_TARGETS in .env")

    async def upload_file(self, filepath: Path, target) -> bool:
        """Upload a single file to a target"""
        target_id = str(target.id)
        file_hash = f"{filepath.stat().st_size}_{filepath.stat().st_mtime}"
        
        # Check if already uploaded
        if target_id in self.upload_history and file_hash in self.upload_history[target_id]:
            self.stats['skipped'] += 1
            return False

        try:
            await self.client.send_file(target, str(filepath))
            
            # Update history and stats
            if target_id not in self.upload_history:
                self.upload_history[target_id] = {}
            
            self.upload_history[target_id][file_hash] = {
                'filename': filepath.name,
                'timestamp': datetime.now().isoformat(),
                'size': filepath.stat().st_size
            }
            
            self.stats['uploaded'] += 1
            self.stats['total_size'] += filepath.stat().st_size
            self.stats['last_file'] = filepath.name
            self.save_history()
            
            print(f"✓ Uploaded {filepath.name} to {target.title}")
            return True

        except Exception as e:
            print(f"✗ Failed to upload {filepath.name} to {target.title}: {e}")
            self.stats['failed'] += 1
            return False

    async def resume_upload(self):
        """Resume upload from last successful file"""
        files = sorted(self.media_folder.glob('*'))
        if self.stats['last_file']:
            try:
                last_idx = [f.name for f in files].index(self.stats['last_file'])
                files = files[last_idx + 1:]
            except ValueError:
                pass
        return files

    async def start_upload(self, resume=False):
        """Main upload function"""
        if not self.targets:
            print("No valid targets configured. Check TELEGRAM_TARGETS in .env")
            return

        files = await self.resume_upload() if resume else sorted(self.media_folder.glob('*'))
        
        if not files:
            print("No files to upload!")
            return

        print(f"\nStarting upload of {len(files)} files to {len(self.targets)} targets")
        start_time = time.time()
        last_update = time.time()

        for file in files:
            for target in self.targets:
                await self.upload_file(file, target)
                await asyncio.sleep(2)  # Rate limiting

            # Progress update every 2 minutes
            if time.time() - last_update >= 120:
                self.print_progress(start_time)
                last_update = time.time()

        self.print_progress(start_time)
        print("\nUpload completed!")

    def print_progress(self, start_time):
        elapsed = time.time() - start_time
        speed = self.stats['total_size'] / elapsed if elapsed > 0 else 0
        
        print(f"\n=== Upload Progress ===")
        print(f"Files Uploaded: {self.stats['uploaded']}")
        print(f"Files Skipped: {self.stats['skipped']}")
        print(f"Failed Uploads: {self.stats['failed']}")
        print(f"Total Size: {naturalsize(self.stats['total_size'])}")
        print(f"Average Speed: {naturalsize(speed)}/s")
        print(f"Time Elapsed: {int(elapsed)}s")
        if self.stats['last_file']:
            print(f"Last File: {self.stats['last_file']}")
        print("=====================\n")

    async def list_available_targets(self):
        """List all available channels and groups"""
        print("\nAvailable Channels and Groups:")
        print("-" * 50)
        
        async for dialog in self.client.iter_dialogs():
            if isinstance(dialog.entity, (Channel, Chat)):
                entity_type = "Channel" if isinstance(dialog.entity, Channel) else "Group"
                print(f"{entity_type}: {dialog.title}")
                print(f"ID: {dialog.id}")
                if hasattr(dialog.entity, 'username') and dialog.entity.username:
                    print(f"Username: @{dialog.entity.username}")
                print("-" * 50)

async def main():
    parser = argparse.ArgumentParser(description='Telegram File Uploader')
    parser.add_argument('--resume', action='store_true', help='Resume from last upload')
    parser.add_argument('--status', action='store_true', help='Show current upload status')
    parser.add_argument('--list', action='store_true', help='List available channels and groups')
    args = parser.parse_args()

    uploader = TelegramUploader()
    
    try:
        await uploader.client.start()
        await uploader.initialize_targets()

        if args.status:
            uploader.print_progress(time.time())
        elif args.list:
            await uploader.list_available_targets()
            return
        else:
            await uploader.start_upload(resume=args.resume)
            
    except KeyboardInterrupt:
        print("\nUpload interrupted by user")
    finally:
        await uploader.client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())