"""Versioned RGB-D recordings. One transaction per pair; no network or device output.

Monotonic timestamps describe capture-session history, not freshness after replay.
"""
import hashlib
import json
import math
import sqlite3
from pathlib import Path

VERSION = 1

def validate(meta, depth, color):
    required = {'sequence', 'session_id', 'host_mono_ns', 'depth_us', 'color_us',
                'width', 'height', 'color_width', 'color_height', 'depth_scale_mm',
                'depth_format', 'color_format', 'aligned'}
    if set(meta) != required:
        raise ValueError('Unexpected or missing frame metadata')
    for key in ('sequence', 'host_mono_ns', 'depth_us', 'color_us'):
        if type(meta[key]) is not int or meta[key] < 0:
            raise ValueError('Invalid '+key)
    for key in ('width', 'height', 'color_width', 'color_height'):
        if type(meta[key]) is not int or not 1 <= meta[key] <= 4096:
            raise ValueError('Invalid '+key)
    if not isinstance(meta['session_id'], str) or not 1 <= len(meta['session_id']) <= 128:
        raise ValueError('Invalid session_id')
    scale = meta['depth_scale_mm']
    if type(scale) not in (int, float) or not math.isfinite(scale) or scale <= 0:
        raise ValueError('Invalid depth scale')
    if meta['depth_format'] != 'Y16_LE' or meta['color_format'] != 'MJPG' or type(meta['aligned']) is not bool:
        raise ValueError('Unsupported frame format')
    if len(depth) != meta['width'] * meta['height'] * 2:
        raise ValueError('Depth size mismatch')
    # UVC buffers can retain padding after JPEG EOI. Preserve raw bytes and hash
    # the complete buffer; image decoders validate the JPEG before displaying it.
    if not 4 <= len(color) <= 32 * 1024**2 or not color.startswith(b'\xff\xd8') or color.rfind(b'\xff\xd9') < 2:
        raise ValueError('Invalid JPEG envelope; decoder must also validate before use')

def checksum(meta_json, depth, color):
    h = hashlib.sha256()
    for blob in (meta_json.encode(), depth, color):
        h.update(len(blob).to_bytes(8, 'big')); h.update(blob)
    return h.hexdigest()

class Recorder:
    def __init__(self, path, max_payload_bytes=64*1024**2):
        if type(max_payload_bytes) is not int or max_payload_bytes <= 0:
            raise ValueError('Invalid quota')
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Do not overwrite or append a second boot session to a recording.
        with path.open('xb'):
            pass
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('PRAGMA journal_mode=DELETE')
        self.db.execute('CREATE TABLE format(version INTEGER NOT NULL)')
        self.db.execute('INSERT INTO format VALUES(?)', (VERSION,))
        self.db.execute('CREATE TABLE frames(sequence INTEGER PRIMARY KEY, meta TEXT NOT NULL, depth BLOB NOT NULL, color BLOB NOT NULL, sha256 TEXT NOT NULL)')
        self.db.commit()
        self.limit, self.size, self.last, self.session, self.last_time = max_payload_bytes, 0, -1, None, -1

    def append(self, meta, depth, color):
        validate(meta, depth, color)
        if meta['sequence'] <= self.last or meta['host_mono_ns'] <= self.last_time:
            raise ValueError('Non-increasing sequence or capture time')
        if self.session is not None and meta['session_id'] != self.session:
            raise ValueError('Mixed capture sessions')
        encoded = json.dumps(meta, sort_keys=True, separators=(',', ':'), allow_nan=False)
        size = len(depth)+len(color)+len(encoded.encode())
        if self.size+size > self.limit:
            raise ValueError('Recording payload quota reached')
        with self.db:
            self.db.execute('INSERT INTO frames VALUES(?,?,?,?,?)', (meta['sequence'], encoded, depth, color, checksum(encoded, depth, color)))
        self.size += size
        self.last, self.last_time, self.session = meta['sequence'], meta['host_mono_ns'], meta['session_id']

    def close(self):
        self.db.close()

def replay(path):
    """Yield verified historical frames. Caller must never actuate from these timestamps."""
    uri = Path(path).resolve().as_uri()+'?mode=ro'
    db = sqlite3.connect(uri, uri=True)
    try:
        if db.execute('PRAGMA quick_check').fetchone() != ('ok',):
            raise ValueError('Database integrity error')
        if db.execute('SELECT version FROM format').fetchall() != [(VERSION,)]:
            raise ValueError('Unsupported recording version')
        last, last_time, session = -1, -1, None
        for seq, encoded, depth, color, sha in db.execute('SELECT sequence,meta,depth,color,sha256 FROM frames ORDER BY sequence'):
            if checksum(encoded, depth, color) != sha:
                raise ValueError('Frame checksum mismatch')
            meta = json.loads(encoded)
            validate(meta, depth, color)
            if seq != meta['sequence'] or seq <= last or meta['host_mono_ns'] <= last_time:
                raise ValueError('Invalid recording order')
            if session is not None and meta['session_id'] != session:
                raise ValueError('Mixed capture sessions')
            last, last_time, session = seq, meta['host_mono_ns'], meta['session_id']
            yield meta, depth, color
    finally:
        db.close()
