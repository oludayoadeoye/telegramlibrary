import os
from telethon import TelegramClient, events
from telethon.tl.types import Channel, Chat, Message
from dotenv import load_dotenv
import asyncio
import json
from datetime import datetime, timedelta
import time
from humanize import naturalsize
from pathlib import Path
import argparse
import sys
from rich.console import Console
from rich.prompt import Prompt
from rich import print as rprint

# Load environment variables
load_dotenv()

class TelegramForwarder:
    def __init__(self):
        self.api_id = int(os.getenv('TELEGRAM_API_ID'))
        self.api_hash = os.getenv('TELEGRAM_API_HASH')
        self.source_channels = os.getenv('SOURCE_CHANNELS', '').split(',')
        self.target_channels = os.getenv('TARGET_CHANNELS', '').split(',')
        
        self.source_channels = [name.strip() for name in self.source_channels if name.strip()]
        self.target_channels = [name.strip() for name in self.target_channels if name.strip()]
        
        self.history_file = Path('forward_history.json')
        self.client = TelegramClient('forwarder_session', self.api_id, self.api_hash)
        self.forward_history = self.load_history()
        
        self.sources = []
        self.targets = []
        
        self.stats = {
            'forwarded': 0,
            'skipped': 0,
            'failed': 0,
            'last_message_id': None,
            'last_update': time.time(),
            'messages_in_window': 0,
            'last_print': time.time()
        }
        
        self.console = Console()
        self.request_lock = asyncio.Lock()
        self.last_request_time = time.time()
        self.requests_in_window = 0
        self.RATE_LIMIT = 30  # requests per second
        self.RATE_WINDOW = 1  # window in seconds

    def load_history(self) -> dict:
        """Load forwarding history from file"""
        try:
            if os.path.exists(self.history_file):
                with open(self.history_file, 'r') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            rprint(f"[red]Error loading history: {str(e)}[/red]")
            return {}

    def save_history(self):
        """Save forwarding history to file"""
        try:
            with open(self.history_file, 'w') as f:
                json.dump(self.forward_history, f, indent=2)
        except Exception as e:
            rprint(f"[red]Error saving history: {str(e)}[/red]")

    async def initialize_channels(self):
        """Initialize source and target channels"""
        print("\nInitializing channels...")
        
        # Initialize source channels
        print("\nSource Channels:")
        for channel in self.source_channels:
            try:
                entity = await self._get_entity_from_name(channel)
                if isinstance(entity, (Channel, Chat)):
                    self.sources.append(entity)
                    print(f"✓ Added source: {entity.title}")
            except Exception as e:
                print(f"✗ Failed to add source {channel}: {str(e)}")

        # Initialize target channels
        print("\nTarget Channels:")
        for channel in self.target_channels:
            try:
                entity = await self._get_entity_from_name(channel)
                if isinstance(entity, (Channel, Chat)):
                    self.targets.append(entity)
                    print(f"✓ Added target: {entity.title}")
            except Exception as e:
                print(f"✗ Failed to add target {channel}: {str(e)}")

    async def _get_entity_from_name(self, channel_name: str):
        """Helper to get entity from name or ID"""
        try:
            if str(channel_name).replace('-', '').isdigit():
                clean_id = str(channel_name).replace('-100', '')
                for id_format in [int(channel_name), int(f"-100{clean_id}"), int(clean_id)]:
                    try:
                        return await self.client.get_entity(id_format)
                    except ValueError:
                        continue
            
            for name_format in [channel_name, f"@{channel_name}"]:
                try:
                    return await self.client.get_entity(name_format)
                except ValueError:
                    continue
                    
            raise ValueError(f"Could not find channel: {channel_name}")
        except Exception as e:
            raise ValueError(f"Invalid channel format: {channel_name} ({str(e)})")

    async def _handle_media_message(self, message, target_channel, caption):
        """Handle forwarding of media messages"""
        try:
            # Debug the media download
            rprint(f"[cyan]Attempting to download media for message {message.id}[/cyan]")
            temp_path = Path(f"temp_{message.id}")
            file_path = await self.client.download_media(message, str(temp_path))
            
            if file_path:
                rprint(f"[green]Successfully downloaded media to {file_path}[/green]")
                await self.client.send_file(
                    target_channel,
                    file_path,
                    caption=caption,
                    force_document=True
                )
                if os.path.exists(file_path):
                    os.remove(file_path)
                    rprint(f"[green]Cleaned up temporary file {file_path}[/green]")
            else:
                rprint(f"[red]Failed to download media for message {message.id}[/red]")
                
        except Exception as e:
            rprint(f"[red]Error handling media message {message.id}: {type(e).__name__}: {str(e)}[/red]")
            raise

    async def _update_history(self, message_id, source_channel, target_id):
        """Update forwarding history"""
        if message_id not in self.history:
            self.history[message_id] = {
                'timestamp': datetime.now().isoformat(),
                'source_channel': str(source_channel.id),
                'targets': []
            }
        self.history[message_id]['targets'].append(target_id)
        self.save_history()

    async def _wait_for_rate_limit(self):
        """Ensure we don't exceed Telegram's rate limits"""
        try:
            async with self.request_lock:
                current_time = time.time()
                time_passed = current_time - self.last_request_time
                
                if time_passed >= self.RATE_WINDOW:
                    # Reset window
                    self.requests_in_window = 0
                    self.last_request_time = current_time
                elif self.requests_in_window >= self.RATE_LIMIT:
                    # Wait for next window
                    wait_time = self.RATE_WINDOW - time_passed
                    try:
                        await asyncio.sleep(wait_time)
                    except asyncio.CancelledError:
                        rprint("\n[yellow]Rate limit wait cancelled[/yellow]")
                        raise
                    self.requests_in_window = 0
                    self.last_request_time = time.time()
                
                self.requests_in_window += 1
        except asyncio.CancelledError:
            raise
        except Exception as e:
            rprint(f"[red]Rate limit error: {str(e)}[/red]")
            raise

    async def forward_message(self, source_channel, target_channel, message):
        """Re-send message content instead of forwarding for protected chats"""
        await self._wait_for_rate_limit()
        try:
            message_id = f"{source_channel.id}_{message.id}"
            target_id = str(target_channel.id)
            
            rprint(f"[cyan]Processing message {message.id} from {source_channel.title}[/cyan]")
            
            try:
                # Instead of forwarding, we'll copy the content
                if hasattr(message, 'media') and message.media:
                    rprint(f"[cyan]Copying media message {message.id}[/cyan]")
                    temp_path = Path(f"temp_{message.id}")
                    file_path = await self.client.download_media(message, str(temp_path))
                    
                    if file_path:
                        caption = message.text if hasattr(message, 'text') else message.caption if hasattr(message, 'caption') else None
                        rprint(f"[cyan]Uploading media to {target_channel.title}[/cyan]")
                        sent_message = await self.client.send_file(
                            target_channel,
                            file_path,
                            caption=caption,
                            force_document=True
                        )
                        if os.path.exists(file_path):
                            os.remove(file_path)
                            rprint(f"[green]Media transferred successfully[/green]")
                    else:
                        rprint(f"[red]Failed to download media[/red]")
                        return False
                        
                elif hasattr(message, 'text') and message.text:
                    rprint(f"[cyan]Copying text message to {target_channel.title}[/cyan]")
                    sent_message = await self.client.send_message(target_channel, message.text)
                    if not sent_message:
                        rprint(f"[red]Failed to send text message[/red]")
                        return False
                else:
                    rprint(f"[yellow]Message {message.id} has no content to copy[/yellow]")
                    return False

                # Verify the message was sent
                if sent_message:
                    rprint(f"[green]✓ Successfully sent message {sent_message.id} to {target_channel.title}[/green]")
                    # Update history with new format
                    self.forward_history[message_id] = {
                        'timestamp': datetime.now().isoformat(),
                        'source_channel': str(source_channel.id),
                        'target_channel': str(target_channel.id),
                        'source_message_id': message.id,
                        'target_message_id': sent_message.id
                    }
                    self.save_history()
                    return True
                else:
                    rprint(f"[red]✗ Failed to send message[/red]")
                    return False

            except Exception as e:
                rprint(f"[red]Error sending message: {type(e).__name__}: {str(e)}[/red]")
                return False

        except Exception as e:
            rprint(f"[red]Error processing message: {type(e).__name__}: {str(e)}[/red]")
            return False

    async def forward_messages(self, source, limit=None, offset_id=0):
        """Forward messages from source to targets"""
        try:
            source_id = str(source.id)
            message_count = 0
            
            async for message in self.client.iter_messages(source, limit=limit, offset_id=offset_id):
                try:
                    await self._wait_for_rate_limit()  # Rate limit check
                    
                    message_hash = f"{source_id}_{message.id}"
                    
                    if message_hash in self.forward_history:
                        self.stats['skipped'] += 1
                        continue

                    # Process message for each target
                    for target in self.targets:
                        success = await self.forward_message(source, target, message)
                        if success:
                            self.stats['forwarded'] += 1
                        else:
                            self.stats['failed'] += 1
                        await asyncio.sleep(0.5)  # Small delay between forwards
                    
                    # Update history
                    self.forward_history[message_hash] = {
                        'timestamp': datetime.now().isoformat(),
                        'source': source.title,
                        'message_id': message.id
                    }
                    self.save_history()
                    
                    # Update stats
                    self.stats['messages_in_window'] += 1
                    self.stats['last_message_id'] = message.id
                    message_count += 1
                    
                    # Print progress every 10 seconds
                    current_time = time.time()
                    if current_time - self.stats['last_print'] >= 10:
                        await self.print_progress()
                        self.stats['messages_in_window'] = 0
                        self.stats['last_print'] = current_time

                except Exception as e:
                    rprint(f"[red]Error processing message {message.id}: {str(e)}[/red]")
                    self.stats['failed'] += 1
                    continue

        except asyncio.CancelledError:
            rprint("\n[yellow]Forwarding cancelled by user[/yellow]")
            raise
        except Exception as e:
            rprint(f"[red]Error in forward_messages: {str(e)}[/red]")
        finally:
            await self.print_progress()

    async def print_progress(self):
        """Print forwarding progress"""
        current_time = datetime.now().strftime("%H:%M:%S")
        rprint(f"\n[bold green]=== Forwarding Progress ({current_time}) ===[/bold green]")
        
        # Add source channel info
        rprint("\n[bold yellow]Source Channel:[/bold yellow]")
        for source in self.sources:
            rprint(f"• {source.title} (ID: {source.id})")
        
        rprint(f"\nMessages Forwarded: [cyan]{self.stats['forwarded']}[/cyan]")
        rprint(f"Messages Skipped: [yellow]{self.stats['skipped']}[/yellow]")
        rprint(f"Failed Forwards: [red]{self.stats['failed']}[/red]")
        rprint(f"Messages in last window: [cyan]{self.stats['messages_in_window']}[/cyan]")
        if self.stats['last_message_id']:
            rprint(f"Last Message ID: [cyan]{self.stats['last_message_id']}[/cyan]")
        
        rprint("\n[bold yellow]Target Channels:[/bold yellow]")
        for target in self.targets:
            try:
                # Verify channel is accessible
                last_msg = await self.client.get_messages(target, limit=1)
                last_time = last_msg[0].date if last_msg else "No messages"
                rprint(f"• {target.title} (ID: {target.id}) - Last message: {last_time}")
            except Exception as e:
                rprint(f"• {target.title} (ID: {target.id}) - [red]Error: {str(e)}[/red]")
        
        rprint("[bold green]=====================[/bold green]\n")

    async def verify_permissions(self):
        """Verify bot permissions in all channels"""
        rprint("\n[yellow]Verifying permissions...[/yellow]")
        
        for source in self.sources:
            try:
                rprint(f"\nSource channel: {source.title}")
                messages = await self.client.get_messages(source, limit=1)
                if messages:
                    rprint("[green]✓ Can read messages[/green]")
                else:
                    rprint("[red]✗ Cannot read messages[/red]")
            except Exception as e:
                rprint(f"[red]✗ Error accessing source: {str(e)}[/red]")
        
        for target in self.targets:
            try:
                rprint(f"\nTarget channel: {target.title}")
                test_msg = await self.client.send_message(target, "Test message - will be deleted")
                if test_msg:
                    rprint("[green]✓ Can send messages[/green]")
                    await self.client.delete_messages(target, test_msg)
                else:
                    rprint("[red]✗ Cannot send messages[/red]")
            except Exception as e:
                rprint(f"[red]✗ Error accessing target: {str(e)}[/red]")

    async def interactive_menu(self):
        """Interactive command-line menu"""
        while True:
            self.console.clear()
            rprint("\n[bold cyan]Telegram Channel Forwarder[/bold cyan]")
            
            # Print current configuration
            rprint("\n[bold green]Current Configuration:[/bold green]")
            for source in self.sources:
                rprint(f"Source: [cyan]{source.title}[/cyan]")
            rprint(f"Forwarding to [cyan]{len(self.targets)}[/cyan] target channels")
            
            # Menu options
            rprint("\n1. Start forwarding from beginning")
            rprint("2. Resume forwarding")
            rprint("3. Show current status")
            rprint("4. Test forward single message")
            rprint("5. Exit")
            
            choice = Prompt.ask("\nEnter your choice", choices=["1", "2", "3", "4", "5"])
            
            if choice == "4":
                try:
                    rprint("[yellow]Starting test copy...[/yellow]")
                    
                    if not self.sources or not self.targets:
                        rprint("[red]Please configure both source and target channels![/red]")
                        input("\nPress Enter to continue...")
                        continue
                    
                    source = self.sources[0]
                    messages = await self.client.get_messages(source, limit=1)
                    if not messages:
                        rprint("[red]No messages found in source channel![/red]")
                        input("\nPress Enter to continue...")
                        continue
                        
                    message = messages[0]
                    rprint(f"[cyan]Found message with ID: {message.id}[/cyan]")
                    
                    for target in self.targets:
                        rprint(f"\n[yellow]Attempting to copy to: {target.title}[/yellow]")
                        success = await self.forward_message(source, target, message)
                        if success:
                            rprint(f"[green]Successfully copied to {target.title}[/green]")
                        else:
                            rprint(f"[red]Failed to copy to {target.title}[/red]")
                    
                    input("\nTest complete. Press Enter to continue...")
                    
                except Exception as e:
                    rprint(f"[red]Test failed: {type(e).__name__}: {str(e)}[/red]")
                    input("\nPress Enter to continue...")
            
            elif choice == "5":
                rprint("[yellow]Exiting...[/yellow]")
                break
            elif choice == "1":
                for source in self.sources:
                    await self.forward_messages(source)
            elif choice == "2":
                for source in self.sources:
                    last_id = max((int(k.split('_')[1]) for k in self.forward_history.keys() 
                                 if k.startswith(str(source.id))), default=0)
                    await self.forward_messages(source, offset_id=last_id)
            elif choice == "3":
                await self.verify_permissions()
                input("\nPress Enter to continue...")

async def main():
    forwarder = TelegramForwarder()
    
    try:
        await forwarder.client.start()
        await forwarder.initialize_channels()
        await forwarder.interactive_menu()
    except asyncio.CancelledError:
        rprint("\n[yellow]Operation cancelled by user[/yellow]")
    except KeyboardInterrupt:
        rprint("\n[yellow]Program interrupted by user[/yellow]")
    except Exception as e:
        rprint(f"[red]Error: {str(e)}[/red]")
    finally:
        # Save any pending progress
        forwarder.save_history()
        await forwarder.client.disconnect()
        rprint("[green]Session saved and cleaned up[/green]")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        rprint("\n[yellow]Program terminated by user[/yellow]")
