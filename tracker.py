import http.server
import socketserver
import urllib.parse
import json
import time
import threading
import sys
from utils import Torrent

# --- Globals ---
PEERS_DB = {}
DB_LOCK = threading.Lock()
ALLOWED_INFO_HASH = None # Will be set from the torrent file at startup

def periodic_logger():
    """Periodically logs the state of all torrents and peers for monitoring."""
    while True:
        time.sleep(30)

        print("\n" + "="*20 + " Tracker Monitor " + "="*20)
        if not PEERS_DB or not ALLOWED_INFO_HASH:
            print("  No active torrents.")
            print("="*57 + "\n")
            continue

        print(f"  Torrent Hash: {ALLOWED_INFO_HASH.hex()}")

        # The DB only has one key, but this is robust
        for info_hash, peers in PEERS_DB.items():
            print(f"  Active Peer Count: {len(peers)}")
            if not peers:
                print("  No participating peers.")
            else:
                print("  Participating Peers:")
                for peer in peers:
                    last_seen_ago = int(time.time() - peer['last_seen'])
                    print(f"    - Peer ID: {peer['id']}, IP: {peer['ip']}:{peer['port']}, Status: {peer.get('status', 'unknown')}, Last Seen: {last_seen_ago}s ago")
        print("="*57 + "\n")

class TrackerHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress the default HTTP log messages for cleaner output
        return

    def do_GET(self):
        global ALLOWED_INFO_HASH
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query, encoding='latin1')

        if parsed.path == "/announce":
            try:
                # 1. Extract info_hash and validate it
                info_hash_str = params.get('info_hash', [None])[0]
                if not info_hash_str:
                    self.send_error(400, "Missing info_hash")
                    return

                info_hash_from_req = info_hash_str.encode('latin1')
                if info_hash_from_req != ALLOWED_INFO_HASH:
                    self.send_error(400, b"This tracker does not serve the requested torrent.")
                    return

                # 2. Extract other peer info
                info_hash_key = info_hash_str
                peer_id = params.get('peer_id', [None])[0]
                port = int(params.get('port', [0])[0])
                event = params.get('event', [''])[0]
                client_ip = self.client_address[0]

                if not peer_id:
                    self.send_error(400, "Missing peer_id")
                    return

                with DB_LOCK:
                    # 3. Update Database based on event
                    if info_hash_key not in PEERS_DB:
                        PEERS_DB[info_hash_key] = []

                    peer_list = PEERS_DB[info_hash_key]
                    peer_index = next((i for i, p in enumerate(peer_list) if p['id'] == peer_id), -1)

                    if event == 'stopped':
                        if peer_index != -1:
                            peer_list.pop(peer_index)
                    elif peer_index != -1:
                        # Peer exists, update last_seen and status if event is significant
                        peer_list[peer_index]['last_seen'] = time.time()
                        if event in ['started', 'completed']:
                            peer_list[peer_index]['status'] = event
                    else:
                        # New peer, add to list
                        peer_entry = {
                            'id': peer_id,
                            'ip': client_ip,
                            'port': port,
                            'status': 'started' if event != 'completed' else 'completed',
                            'last_seen': time.time()
                        }
                        peer_list.append(peer_entry)

                    # 4. Clean up timed-out peers
                    now = time.time()
                    PEERS_DB[info_hash_key] = [p for p in PEERS_DB[info_hash_key] if now - p['last_seen'] < 60]

                    # 5. Construct Response
                    response_peers = []
                    for p in PEERS_DB[info_hash_key]:
                        if p['id'] != peer_id:
                            response_peers.append({'ip': p['ip'], 'port': p['port'], 'id': p['id']})

                response = {'interval': 30, 'peers': response_peers}
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(response).encode('utf-8'))

                # Log the specific event that was processed
                print(f"Tracker: Processed '{event if event else 'periodic'}' announce from {peer_id}.")

            except Exception as e:
                print(f"Error handling announce: {e}")
                self.send_error(500)

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python tracker.py <torrent_file>")
        sys.exit(1)

    torrent_file = sys.argv[1]
    try:
        torrent = Torrent(torrent_file)
        ALLOWED_INFO_HASH = torrent.info_hash
        print(f"Tracker configured to serve torrent: {torrent.name}")
        print(f"Info Hash: {ALLOWED_INFO_HASH.hex()}")
    except Exception as e:
        print(f"Error loading torrent file: {e}")
        sys.exit(1)

    # Start the periodic logger in a background thread
    monitor_thread = threading.Thread(target=periodic_logger, daemon=True)
    monitor_thread.start()

    PORT = 8000

    # Create the server and run it in a background thread
    httpd = socketserver.TCPServer(("", PORT), TrackerHandler)
    server_thread = threading.Thread(target=httpd.serve_forever)
    server_thread.daemon = True # Allows main thread to exit even if this thread is running
    server_thread.start()

    print(f"Tracker running on port {PORT}. Press Ctrl+C to stop.")

    try:
        # Keep the main thread alive to handle the shutdown
        while server_thread.is_alive():
            server_thread.join(timeout=1.0)
    except KeyboardInterrupt:
        print("\nCtrl+C received, shutting down tracker...")
        httpd.shutdown()
        httpd.server_close()

    print("Tracker stopped.")

