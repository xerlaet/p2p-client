# Simple BitTorrent-style P2P Client

This project is an implementation of a simple BitTorrent-style P2P client in Python. It includes a tracker, a peer client, and a utility to create torrent files. The system is designed to demonstrate core BitTorrent concepts like peer discovery, multi-peer communication, and parallel piece-based file transfers

## Project Structure

The project is built using only Python's standard libraries and the `requests` library for tracker communication. The code is organized into the following files:

-   **`utils.py`**: A utility module containing helper functions for the project
    -   `encode_bencode`/`decode_bencode`: Functions for serializing and deserializing data using the Bencode format required by the BitTorrent protocol
    -   `Torrent`: A class that parses a `.torrent` file and provides easy access to its metadata, such as the announce URL and piece hashes

-   **`create_torrent.py`**: A command-line script used to generate a new `.torrent` file from a source file

-   **`tracker.py`**: A central tracker server built using Python's `http.server`
    -   It manages a single torrent, specified by the `.torrent` file it's started with
    -   It responds to announce requests from peers, maintaining a list of active peers for the torrent
    -   It includes a periodic monitor that logs the tracker's status and the list of connected peers

-   **`client.py`**: The main P2P client application
    -   **Multi-Threaded Architecture**: The client is heavily multi-threaded to handle simultaneous operations. A main `Client` class orchestrates everything, while each connection to a peer is managed in a separate `PeerConnection` thread
    -   `PieceManager`: A thread-safe class that manages the state of the file on disk—verifying existing files, writing downloaded pieces, and tracking piece availability via a bitfield
    -   **Pipelined Requests**: To improve download speed, the client pipelines piece requests, keeping multiple requests in-flight to each peer
    -   **Graceful Shutdown**: The client and tracker can be stopped cleanly with `Ctrl+C`, ensuring all threads are terminated and final "stopped" messages are sent

## Example Usage
Below are instructions for testing the program with three clients and is easily generalizable to any configuration of your choosing

### Step 1: Create the `.torrent` File
Either find a file to share or generate one. This command creates a 10MB file named `test_video.mov`.
```bash
dd if=/dev/zero of=test_video.mov bs=1M count=10
```

With that file, use the provided script to create a torrent file for it. This command generates the torrent file `test_video.mov.torrent`.
```bash
python create_torrent.py test_video.mov http://127.0.0.1:8000/announce
```

### Step 2: Prepare Peer Directories
Create separate directories to simulate different machines. The first peer will be the "seeder" and start with the file.
```bash
mkdir -p peer1 peer2 peer3
mv test_video.mov peer1/
```

### Step 3: Run the Simulation
Start the tracker with the torrent file it will manage.
```bash
python tracker.py test_video.mov.torrent
```

Open three additional terminals in the project's root directory for the clients.
```bash
cd peer1/
python ../client.py ../test_video.mov.torrent 6881
```

```bash
cd peer2/
python ../client.py ../test_video.mov.torrent 6882
```

```bash
cd peer3/
python ../client.py ../test_video.mov.torrent 6883
```

Watch as the file is transferred! Press `Ctrl+C` to stop any of the programs.

## Command-Line Options

### `create_torrent.py`
```
Usage: python create_torrent.py <file_to_share> <tracker_url>
```
-   **`<file_to_share>`**: (Required) The path to the file you want to create a torrent for.
-   **`<tracker_url>`**: (Required) The full announce URL of the tracker.

### `tracker.py`
```
Usage: python tracker.py <torrent_file>
```
-   **`<torrent_file>`**: (Required) The path to the `.torrent` file that this tracker will manage. The tracker will only serve peers for this specific torrent.

### `client.py`
```
Usage: python client.py <torrent_file> [port]
```
-   **`<torrent_file>`**: (Required) The path to the `.torrent` file you wish to download or seed.
-   **`[port]`**: (Optional) The port number for the client to listen on for incoming peer connections. Defaults to `6881`. **You must specify a unique port for each client running on the same machine.**

