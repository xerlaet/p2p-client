import socket
import threading
import time
import struct
import hashlib
import random
import requests
import sys
import os
from utils import Torrent, recv_all

# Configuration
MY_PEER_ID = 'Peer-' + str(random.randint(100000000000, 999999999999))

def create_bitfield(bitfield_list):
    """Converts a list of booleans to a compact bitfield byte string."""
    num_pieces = len(bitfield_list)
    num_bytes = (num_pieces + 7) // 8
    byte_array = bytearray(num_bytes)
    for i, have_piece in enumerate(bitfield_list):
        if have_piece:
            byte_index = i // 8
            bit_index = i % 8
            byte_array[byte_index] |= (1 << (7 - bit_index))
    return bytes(byte_array)

class PieceManager:
    """Manages file writing and which pieces we have."""
    def __init__(self, torrent, port):
        self.torrent = torrent
        self.port = port
        self.bitfield = [False] * torrent.total_pieces
        self.file_lock = threading.Lock()

        # Check for and verify existing file on startup
        self._verify_existing_file()

    def _verify_existing_file(self):
        """Checks for the file on disk and verifies it against piece hashes."""
        if os.path.exists(self.torrent.name) and os.path.getsize(self.torrent.name) == self.torrent.length:
            print(f"[{self.port}] File exists, verifying pieces...")
            is_valid = True
            for i in range(self.torrent.total_pieces):
                piece_data = self._read_piece_data(i)
                if hashlib.sha1(piece_data).digest() != self.torrent.pieces[i]:
                    print(f"[{self.port}] Verification failed: Piece #{i} is corrupt. Re-downloading required.")
                    is_valid = False
                    break

            if is_valid:
                print(f"[{self.port}] File verification successful. All pieces are present.")
                self.bitfield = [True] * self.torrent.total_pieces

        if not self.is_complete():
            if not os.path.exists(self.torrent.name):
                print(f"[{self.port}] Pre-allocating file on disk...")
                with open(self.torrent.name, 'wb') as f:
                    f.seek(self.torrent.length - 1)
                    f.write(b'\0')

    def _read_piece_data(self, index):
        """Internal helper to read piece data from disk, bypassing bitfield check."""
        start_pos = index * self.torrent.piece_length
        length = self.torrent.piece_length
        if index == self.torrent.total_pieces - 1:
            remainder = self.torrent.length % self.torrent.piece_length
            if remainder > 0: length = remainder

        with self.file_lock:
            with open(self.torrent.name, 'rb') as f:
                f.seek(start_pos)
                return f.read(length)

    def write_piece(self, index, data):
        # Verify piece hash before writing
        piece_hash = hashlib.sha1(data).digest()
        if piece_hash != self.torrent.pieces[index]:
            print(f"[{self.port}] Error: Hash mismatch for piece #{index}. Discarding.")
            return False

        with self.file_lock:
            if self.bitfield[index]:
                return True # Already have it

            start_pos = index * self.torrent.piece_length
            with open(self.torrent.name, 'r+b') as f:
                f.seek(start_pos)
                f.write(data)
            self.bitfield[index] = True
            print(f"[{self.port}] Downloaded Piece #{index} | Progress: {sum(self.bitfield)}/{len(self.bitfield)}")
        return True

    def read_piece(self, index):
        with self.file_lock:
            if not self.bitfield[index]:
                return None
        return self._read_piece_data(index)

    def is_complete(self):
        return all(self.bitfield)

class PeerConnection(threading.Thread):
    MAX_PIPELINED_REQUESTS = 5
    REQUEST_TIMEOUT = 20 # seconds

    def __init__(self, client, sock, ip, port, torrent, manager):
        threading.Thread.__init__(self)
        self.client = client
        self.sock = sock
        self.ip = ip
        self.port = port
        self.torrent = torrent
        self.manager = manager
        self.choked = True
        self.peer_bitfield = [False] * torrent.total_pieces
        self.last_message_sent = time.time()
        self.outstanding_requests = {} # {piece_index: timestamp}
        self.shutdown_event = threading.Event()

    def run(self):
        try:
            # 1. Handshake
            pstr = b'BitTorrent protocol'
            handshake = struct.pack('>B19s8s20s20s', 19, pstr, b'\0'*8, self.torrent.info_hash, MY_PEER_ID.encode())
            self.sock.send(handshake)
            self.last_message_sent = time.time()

            response = recv_all(self.sock, 68)
            if not response or self.shutdown_event.is_set() or response[28:48] != self.torrent.info_hash:
                return

            print(f"[{self.client.port}] Connected to peer {self.ip}:{self.port}")

            # 2. Exchange bitfields, get interested, and unchoke
            self.send_message(5, create_bitfield(self.manager.bitfield))
            self.send_message(2, b'') # Interested
            self.send_message(1, b'') # Unchoke

            # 3. Main Loop - runs until shutdown is signaled
            while not self.shutdown_event.is_set():
                now = time.time()

                # Send keep-alive
                if now - self.last_message_sent > 60:
                    self.send_message(-1, b'')

                # Check for time out requests
                timed_out = [idx for idx, ts in self.outstanding_requests.items() if now - ts > self.REQUEST_TIMEOUT]
                for idx in timed_out:
                    print(f"[{self.client.port}] Request for piece #{idx} from {self.ip} timed out. Re-queueing.")
                    del self.outstanding_requests[idx]

                # Pipelined requests
                if not self.manager.is_complete():
                    while len(self.outstanding_requests) < self.MAX_PIPELINED_REQUESTS and not self.choked:
                        found_piece = False
                        for i in range(len(self.manager.bitfield)):
                            if not self.manager.bitfield[i] and self.peer_bitfield[i] and i not in self.outstanding_requests:
                                length = self.torrent.piece_length
                                if i == self.torrent.total_pieces - 1:
                                    remainder = self.torrent.length % self.torrent.piece_length
                                    if remainder > 0: length = remainder
                                req = struct.pack('>III', i, 0, length)
                                print(f"[{self.client.port}] Requesting piece #{i} from {self.ip}")
                                self.send_message(6, req)
                                self.outstanding_requests[i] = time.time()
                                found_piece = True
                                break # Request one piece per outer loop iteration
                        if not found_piece:
                            break

                # Read messages
                self.sock.settimeout(1.0)
                try:
                    length_bytes = recv_all(self.sock, 4)
                except (socket.timeout, ConnectionAbortedError):
                    continue
                except OSError:
                    break
                self.sock.settimeout(None)

                if not length_bytes: break
                length = struct.unpack('>I', length_bytes)[0]
                if length == 0: continue

                msg_id_bytes = recv_all(self.sock, 1)
                if not msg_id_bytes: break
                msg_id = msg_id_bytes[0]

                payload_len = length - 1
                payload = recv_all(self.sock, payload_len) if payload_len > 0 else b''

                if msg_id == 0: # Choke
                    print(f"[{self.client.port}] CHOKED by {self.ip}")
                    self.choked = True
                    self.outstanding_requests.clear()
                elif msg_id == 1: # Unchoke
                    print(f"[{self.client.port}] UNCHOKED by {self.ip}")
                    self.choked = False
                elif msg_id == 4: # Have
                    index = struct.unpack('>I', payload)[0]
                    # print(f"[{self.client.port}] Peer {self.ip} now has piece #{index}")
                    if index < len(self.peer_bitfield):
                        self.peer_bitfield[index] = True
                elif msg_id == 5: # Bitfield
                    temp_bitfield = [False] * self.torrent.total_pieces
                    for i, byte in enumerate(payload):
                        for j in range(8):
                            piece_index = i * 8 + j
                            if piece_index < self.torrent.total_pieces:
                                if (byte >> (7 - j)) & 1:
                                    temp_bitfield[piece_index] = True
                    self.peer_bitfield = temp_bitfield
                    print(f"[{self.client.port}] Peer {self.ip} has {sum(self.peer_bitfield)}/{self.torrent.total_pieces} pieces.")
                elif msg_id == 6: # Request
                    idx, begin, length = struct.unpack('>III', payload)
                    # print(f"[{self.client.port}] Peer {self.ip} is requesting piece #{idx}")
                    data = self.manager.read_piece(idx)
                    if data:
                        header = struct.pack('>II', idx, begin)
                        self.send_message(7, header + data)
                elif msg_id == 7: # Piece
                    idx, begin = struct.unpack('>II', payload[:8])
                    block = payload[8:]
                    if idx in self.outstanding_requests:
                        del self.outstanding_requests[idx]
                        if self.manager.write_piece(idx, block):
                            self.client.broadcast_have(idx)

        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            if not self.shutdown_event.is_set():
                import traceback
                print(f"Generic error with {self.ip}: {e}\n{traceback.format_exc()}")
        finally:
            if not self.sock._closed:
                self.sock.close()

    def send_message(self, msg_id, payload):
        if self.sock._closed: return
        if msg_id == -1:
            msg = struct.pack('>I', 0)
        else:
            msg = struct.pack('>IB', len(payload) + 1, msg_id) + payload
        try:
            self.sock.sendall(msg)
            self.last_message_sent = time.time()
        except OSError:
            pass
    
    def send_have(self, piece_index):
        payload = struct.pack('>I', piece_index)
        self.send_message(4, payload)

class Client:
    def __init__(self, torrent_file, port):
        self.torrent = Torrent(torrent_file)
        self.manager = PieceManager(self.torrent, port)
        self.peers = []
        self.port = port
        self.download_complete = False
        self.shutdown_flag = threading.Event()
        self.listener_thread = None

    def broadcast_have(self, piece_index):
        """Announce we have a new piece to all connected peers."""
        for peer in self.peers:
            if peer.is_alive():
                peer.send_have(piece_index)

    def announce_to_tracker(self, event=""):
        if self.shutdown_flag.is_set() and event != "stopped":
            return
        try:
            # On the first announce, we may not know our status, so check manager
            if event == "started" and self.manager.is_complete():
                event = "completed"

            params = {
                'info_hash': self.torrent.info_hash,
                'peer_id': MY_PEER_ID,
                'port': self.port,
                'uploaded': 0, 'downloaded': sum(self.manager.bitfield) * self.torrent.piece_length,
                'left': self.torrent.length - (sum(self.manager.bitfield) * self.torrent.piece_length),
            }
            if event:
                params['event'] = event

            res = requests.get(self.torrent.announce, params=params, timeout=5)
            res.raise_for_status()
            data = res.json()

            if event != 'stopped':
                for p in data.get('peers', []):
                    self.connect_to_peer(p['ip'], p['port'])
        except requests.exceptions.RequestException as e:
            if not self.shutdown_flag.is_set():
                print(f"Tracker Announce Error: {e}")

    def start_tracker_thread(self):
        self.announce_to_tracker('started')

        def periodic_announce():
            while not self.shutdown_flag.is_set():
                if self.shutdown_flag.wait(10): # Wait for 10s or until shutdown is signaled
                    break

                if self.manager.is_complete() and not self.download_complete:
                    self.announce_to_tracker('completed')
                    self.download_complete = True
                    print("\n--- DOWNLOAD COMPLETE ---")
                else:
                    self.announce_to_tracker()

        threading.Thread(target=periodic_announce, daemon=True).start()

    def start_listener(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.settimeout(1.0) # Use timeout to be non-blocking
        server.bind(('0.0.0.0', self.port))
        server.listen(5)
        print(f"Listening for incoming peers on {self.port}...")

        while not self.shutdown_flag.is_set():
            try:
                client_sock, addr = server.accept()
                print(f"Accepted connection from {addr}")
                t = PeerConnection(self, client_sock, addr[0], addr[1], self.torrent, self.manager)
                t.start()
                self.peers.append(t)
            except socket.timeout:
                continue
            except OSError:
                if not self.shutdown_flag.is_set():
                    print("Listener socket error.")
                break

    def connect_to_peer(self, ip, port):
        if self.shutdown_flag.is_set(): return
        if port == self.port and ip in ('127.0.0.1', 'localhost', socket.gethostbyname(socket.gethostname())): 
            return

        if any(p.is_alive() and p.ip == ip and p.port == port for p in self.peers):
            return

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect((ip, port))
            s.settimeout(None)
            t = PeerConnection(self, s, ip, port, self.torrent, self.manager)
            t.start()
            self.peers.append(t)
        except (socket.timeout, ConnectionRefusedError):
            pass
        except Exception as e:
            if not self.shutdown_flag.is_set():
                print(f"Failed to connect to {ip}:{port} - {e}")

    def run(self):
        self.listener_thread = threading.Thread(target=self.start_listener)
        self.listener_thread.start()

        if self.manager.is_complete():
            print("\n--- DOWNLOAD COMPLETE ---")
            self.download_complete = True

        self.start_tracker_thread()

        try:
            while self.listener_thread.is_alive():
                self.peers = [p for p in self.peers if p.is_alive()]
                time.sleep(5)
        except KeyboardInterrupt:
            print("\nCtrl+C received, stopping client...")
        finally:
            self.stop()

    def stop(self):
        if self.shutdown_flag.is_set(): return
        print("Initiating graceful shutdown...")
        self.shutdown_flag.set()

        print("Shutting down peer connections...")
        for peer in self.peers:
            peer.shutdown_event.set()
        for peer in self.peers:
            peer.join(timeout=2.0)

        if self.listener_thread:
            self.listener_thread.join(timeout=2.0)

        print("Sending final 'stopped' to tracker...")
        self.announce_to_tracker('stopped')

        print("Client stopped.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python client.py <torrent_file> [port]")
        sys.exit(1)

    torrent_file = sys.argv[1]
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 6881

    client = Client(torrent_file, port)

    if client.manager.is_complete():
        print("File found and verified, starting as seeder.")
    else:
        print("File not found or incomplete, starting as leecher.")

    client.run()

