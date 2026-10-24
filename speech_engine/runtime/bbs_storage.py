"""Read/write BBS1 film storage while preserving its numeric type tags.

Format verified against the installed BBS 2.3.3 DataStorage classes.
"""
import gzip
import io
import struct
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Node:
    tag: int
    value: object

    def __getitem__(self, key):
        return self.value[key]

    def __setitem__(self, key, value):
        self.value[key] = value if isinstance(value, Node) else node(value)


def node(value):
    if isinstance(value, Node):
        return value
    if isinstance(value, dict):
        return Node(0, {k: node(v) for k, v in value.items()})
    if isinstance(value, list):
        return Node(1, [node(v) for v in value])
    if isinstance(value, str):
        return Node(2, value)
    if isinstance(value, bool):
        return Node(3, int(value))
    if isinstance(value, int):
        return Node(5, value)
    if isinstance(value, float):
        return Node(8, value)
    raise TypeError(type(value))


def plain(value):
    if value.tag == 0:
        return {k: plain(v) for k, v in value.value.items()}
    if value.tag == 1:
        return [plain(v) for v in value.value]
    return value.value


NUMERIC = {3: 'b', 4: 'h', 5: 'i', 6: 'f', 7: 'q', 8: 'd'}
ARRAY = {9: 'b', 10: 'h', 11: 'i', 12: 'q'}


def _utf_encode(s):
    # Java modified UTF-8 encodes UTF-16 code units separately.
    units = s.encode('utf-16-be', errors='surrogatepass')
    out = bytearray()
    for (c,) in struct.iter_unpack('>H', units):
        if 0 < c < 0x80:
            out.append(c)
        elif c < 0x800:
            out.extend((0xc0 | (c >> 6), 0x80 | (c & 63)))
        else:
            out.extend((0xe0 | (c >> 12), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63)))
    return struct.pack('>H', len(out)) + out


def decode(raw):
    stream = io.BytesIO(raw)

    def read(fmt):
        length = struct.calcsize('>' + fmt)
        return struct.unpack('>' + fmt, stream.read(length))[0]

    def utf():
        b = stream.read(read('H')).replace(b'\xc0\x80', b'\x00')
        text = b.decode('utf-8', errors='surrogatepass')
        return text.encode('utf-16', errors='surrogatepass').decode('utf-16')

    if stream.read(4) != b'BBS1':
        raise ValueError('Not BBS1 film storage')
    index_format = ('B', 'H', 'i')[read('B')]
    keys = {}
    for _ in range(read(index_format)):
        index = read(index_format)
        keys[index] = utf()

    def parse():
        tag = read('B')
        if tag == 0:
            value = {}
            for _ in range(read('i')):
                key = keys[read(index_format)]
                value[key] = parse()
        elif tag == 1:
            value = [parse() for _ in range(read('i'))]
        elif tag == 2:
            value = utf()
        elif tag in NUMERIC:
            value = read(NUMERIC[tag])
        elif tag in ARRAY:
            value = [read(ARRAY[tag]) for _ in range(read('i'))]
        else:
            raise ValueError(f'Unknown BBS type: {tag}')
        return Node(tag, value)

    result = parse()
    if stream.read(1):
        raise ValueError('Trailing bytes in BBS storage')
    return result


def encode(root):
    keys = {}

    def collect(n):
        if n.tag == 0:
            for k, v in n.value.items():
                keys.setdefault(k, len(keys))
                collect(v)
        elif n.tag == 1:
            for v in n.value:
                collect(v)

    collect(root)
    index_tag = 0 if len(keys) < 256 else 1 if len(keys) < 65536 else 2
    index_format = ('B', 'H', 'i')[index_tag]
    stream = io.BytesIO()

    def write(fmt, value):
        stream.write(struct.pack('>' + fmt, value))

    stream.write(b'BBS1')
    write('B', index_tag)
    write(index_format, len(keys))
    for k, v in keys.items():
        write(index_format, v)
        stream.write(_utf_encode(k))

    def emit(n):
        write('B', n.tag)
        if n.tag == 0:
            write('i', len(n.value))
            for k, v in n.value.items():
                write(index_format, keys[k])
                emit(v)
        elif n.tag == 1:
            write('i', len(n.value))
            for v in n.value:
                emit(v)
        elif n.tag == 2:
            stream.write(_utf_encode(n.value))
        elif n.tag in NUMERIC:
            write(NUMERIC[n.tag], n.value)
        elif n.tag in ARRAY:
            write('i', len(n.value))
            for v in n.value:
                write(ARRAY[n.tag], v)
        else:
            raise ValueError(n.tag)

    emit(root)
    return stream.getvalue()


def load(path):
    return decode(gzip.decompress(Path(path).read_bytes()))


def save(path, root):
    Path(path).write_bytes(gzip.compress(encode(root), mtime=0))
