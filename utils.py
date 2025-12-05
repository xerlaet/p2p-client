import struct
import hashlib
import io

# Bencode Encoder
def encode_bencode(data):
    """Encodes a Python object into a bencoded string."""
    if isinstance(data, int):
        return f"i{data}e".encode('latin1')
    elif isinstance(data, bytes):
        return f"{len(data)}:".encode('latin1') + data
    elif isinstance(data, str):
        return encode_bencode(data.encode('utf-8'))
    elif isinstance(data, list):
        return b"l" + b"".join(encode_bencode(item) for item in data) + b"e"
    elif isinstance(data, dict):
        # Keys must be bytes and sorted
        sorted_items = sorted(data.items())
        encoded_items = b""
        for k, v in sorted_items:
            encoded_key = encode_bencode(k)
            encoded_value = encode_bencode(v)
            encoded_items += encoded_key + encoded_value
        return b"d" + encoded_items + b"e"
    raise TypeError(f"Cannot bencode type: {type(data)}")

# Bencode Decoder
def _decode_func(s):
    """Helper function that also returns the rest of the string"""
    if len(s) == 0: return None, s
    char = chr(s[0])
    if char == 'i': # Integer
        end = s.find(b'e')
        return int(s[1:end]), s[end+1:]
    elif char == 'l': # List
        lst = []
        rest = s[1:]
        while rest[0] != ord('e'):
            val, rest = _decode_func(rest)
            lst.append(val)
        return lst, rest[1:]
    elif char == 'd': # Dictionary
        d = {}
        rest = s[1:]
        while rest[0] != ord('e'):
            key, rest = _decode_func(rest)
            val, rest = _decode_func(rest)
            # The key must be decoded to be used in Python dicts
            d[key.decode('utf-8')] = val
        return d, rest[1:]
    elif char.isdigit(): # String
        colon = s.find(b':')
        length = int(s[:colon])
        return s[colon+1:colon+1+length], s[colon+1+length:]
    return None, s

def decode_bencode(data):
    """Decodes a bencoded string and returns the Python object."""
    if isinstance(data, str): data = data.encode('latin1')
    res, _ = _decode_func(data)
    return res

# Torrent File Parser
class Torrent:
    def __init__(self, filename):
        with open(filename, 'rb') as f:
            bencoded_data = f.read()
        meta = decode_bencode(bencoded_data)
        self.announce = meta['announce'].decode('utf-8')
        info = meta['info']
        self.piece_length = info['piece length']
        self.name = info['name'].decode('utf-8')
        pieces_blob = info['pieces']
        self.length = info['length']
        # Correctly calculate Info Hash (SHA1 of the bencoded info dictionary)
        info_start_index = bencoded_data.find(b'4:info') + len(b'4:info')
        _, rest = _decode_func(bencoded_data[info_start_index:])
        info_len = len(bencoded_data[info_start_index:]) - len(rest)
        raw_info = bencoded_data[info_start_index : info_start_index + info_len]
        self.info_hash = hashlib.sha1(raw_info).digest()
        # Split pieces blob into 20-byte SHA1 hashes
        self.pieces = [pieces_blob[i:i+20] for i in range(0, len(pieces_blob), 20)]
        self.total_pieces = len(self.pieces)

# Networking Helper
def recv_all(sock, n):
    """Helper to ensure we get exactly n bytes from TCP stream"""
    data = b''
    while len(data) < n:
        packet = sock.recv(n - len(data))
        if not packet: return None
        data += packet
    return data

