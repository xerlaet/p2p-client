import hashlib
import os
import sys
from utils import encode_bencode

def create_torrent_file(file_path, tracker_url, output_torrent_path, piece_size=262144): # 256KB pieces
    """Creates a .torrent file for the given file."""
    if not os.path.exists(file_path):
        print(f"Error: File not found at {file_path}")
        return

    # 1. Read the file and generate piece hashes
    pieces = b""
    with open(file_path, 'rb') as f:
        while True:
            piece = f.read(piece_size)
            if not piece:
                break
            pieces += hashlib.sha1(piece).digest()

    # 2. Create the 'info' dictionary
    info = {
        b'name': os.path.basename(file_path).encode('utf-8'),
        b'piece length': piece_size,
        b'pieces': pieces,
        b'length': os.path.getsize(file_path)
    }

    # 3. Create the root dictionary
    torrent_data = {
        b'announce': tracker_url.encode('utf-8'),
        b'info': info
    }

    # 4. Bencode the data and write to file
    with open(output_torrent_path, 'wb') as f:
        f.write(encode_bencode(torrent_data))
    print(f"Torrent file created: {output_torrent_path}")
    print(f"File: {os.path.basename(file_path)}")
    print(f"Tracker: {tracker_url}")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python create_torrent.py <file_to_share> <tracker_url>")
        print("Example: python create_torrent.py test_video.mov http://127.0.0.1:8000/announce")
    else:
        file_to_share = sys.argv[1]
        tracker_url = sys.argv[2]
        output_path = file_to_share + ".torrent"
        create_torrent_file(file_to_share, tracker_url, output_path)

