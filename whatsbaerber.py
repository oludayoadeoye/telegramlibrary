import pywhatkit as kit
import time
import datetime
import os
import signal
import sys

# List of WhatsApp group names
whatsapp_groups = [
    "Testgroup23",  # Replace with actual group names
    # "Group Name 2",
    # "Group Name 3",
    # "Group Name 4",
    # "Group Name 5",
    # "Group Name 6",  # Add more group names as needed
    # "Group Name 7",
    # "Group Name 8",
    # "Group Name 9",
    # "Group Name 10",
    # Add more group names as needed
]

# Path to the folder containing the photos
photos_folder = "/home/deetech/Documents/nextks/fastapi/allscrapper/downloads"  # Replace with the actual path to your photos folder

# Log file to keep track of sent files
log_file = "sent_files.log"

# Function to send photo to a WhatsApp group
def send_photo_to_whatsapp_group(group_name, photo_path):
    try:
        kit.sendwhats_image(group_name, photo_path)
        time.sleep(5)  # Wait a bit after sending to avoid rate limits
    except Exception as e:
        print(f"Error sending photo to {group_name}: {str(e)}")

# Function to get user input for the time in 12-hour format
def get_user_input_time():
    while True:
        try:
            time_input = input("Enter the time to send the photos (HH:MM AM/PM): ")
            time_obj = datetime.datetime.strptime(time_input, '%I:%M %p')
            return time_obj.hour, time_obj.minute
        except ValueError:
            print("Invalid input. Please enter the time in HH:MM AM/PM format.")

# Function to load sent files from the log
def load_sent_files():
    if not os.path.exists(log_file):
        return set()
    with open(log_file, 'r') as f:
        return set(line.strip() for line in f)

# Function to log sent files
def log_sent_file(file_path):
    with open(log_file, 'a') as f:
        f.write(file_path + '\n')

def format_time_remaining(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    seconds = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

def signal_handler(sig, frame):
    print("\nGracefully shutting down...")
    sys.exit(0)

def wait_with_progress(delay):
    start_time = time.time()
    try:
        while True:
            elapsed = time.time() - start_time
            remaining = delay - elapsed
            
            if remaining <= 0:
                break
                
            print(f"\rWaiting to send photos... Time remaining: {format_time_remaining(remaining)}", end='', flush=True)
            time.sleep(1)
        print("\nStarting to send photos...")
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(0)

# Main function
def main():
    signal.signal(signal.SIGINT, signal_handler)
    
    time_hour, time_min = get_user_input_time()

    # Calculate the delay to the specified time
    now = datetime.datetime.now()
    scheduled_time = now.replace(hour=time_hour, minute=time_min, second=0, microsecond=0)
    if scheduled_time < now:
        scheduled_time += datetime.timedelta(days=1)
    delay = (scheduled_time - now).total_seconds()

    print(f"Photos will be sent at {scheduled_time.strftime('%I:%M %p')}")
    print(f"Total wait time: {format_time_remaining(delay)}")
    
    # Ask for confirmation
    confirm = input("Do you want to continue? (y/n): ").lower()
    if confirm != 'y':
        print("Operation cancelled")
        return

    # Wait with progress bar
    wait_with_progress(delay)

    # Load sent files from the log
    sent_files = load_sent_files()

    # Get list of photos in the folder
    photos = [os.path.join(photos_folder, f) for f in os.listdir(photos_folder) 
             if f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp'))]

    if not photos:
        print("No photos found in the specified folder!")
        return

    print(f"Found {len(photos)} photos to send")
    
    # Send photos to groups in batches of 5
    batch_size = 5
    for i in range(0, len(whatsapp_groups), batch_size):
        batch = whatsapp_groups[i:i + batch_size]
        for group in batch:
            for photo in photos:
                if photo not in sent_files:
                    print(f"\nSending photo to {group}...")
                    send_photo_to_whatsapp_group(group, photo)
                    log_sent_file(photo)
                    sent_files.add(photo)
                    time.sleep(10)  # Wait for 10 seconds between messages
                    break  # Move to the next group after sending one photo
        print("\nWaiting 60 seconds before next batch...")
        time.sleep(60)  # Wait between batches

    print("\nAll photos sent successfully.")

if __name__ == "__main__":
    main()