"""Read / restore the "marked" header layout written by the NBA2KHUB IFF Locker and by RED MC, so those
cyberfaces can be edited and keep their mark (and stay locked).

A 2K14 .iff header (before the first ZLIB chunk) is:
  0x00 magic, 0x04 header size, 0x08 BE tail offset, 0x10 chunk count, 0x14 -> chunk records,
  0x18 record count, 0x1C -> record pointer list; then the blocks themselves.
Pointers (0x14, 0x1C and every list entry) are self-relative with bit 0 set.

Normal files:  [0x20 fields][chunk records][record pointer list][records]
Marked files:  [0x20 fields][mark][record pointer list][chunk records][records]
  mark = "NBA2KHUB LOCKED!!!" (NBA2KHUB IFF Locker) or "EDITED WITH RED MC" (RED MC saves)
Both tools only move the blocks (records end up at odd offsets, which tools that scan from 0x20 in 4-byte steps
can't read); the game follows the pointers, so both read the same. unlock() rewrites a locked header in the normal
order, relock() puts it back exactly (relock(unlock(x)) == x, checked on every locked and RED MC file).
"""
import struct

LOCK_MARK = b'NBA2KHUB LOCKED'


def _u(d, o):
    return struct.unpack_from('<I', d, o)[0]


def _target(d, o):
    return o + (_u(d, o) & ~1)


def _set_ptr(d, o, target, old):
    struct.pack_into('<I', d, o, (target - o) | (old & 1))


def _parse(d):
    hs = _u(d, 4)
    if d[hs:hs + 4] != b'ZLIB':
        raise ValueError('unexpected header (no ZLIB chunk at the header end)')
    cr, lp, n = _target(d, 0x14), _target(d, 0x1C), _u(d, 0x18)
    recs = [_target(d, lp + 4 * i) for i in range(n)]
    return hs, cr, lp, n, recs


def _chunks(d, hs):
    """[(file offset, compressed length)] of the ZLIB chunk chain after the header"""
    out, pos = [], hs
    while d[pos:pos + 4] == b'ZLIB':
        c = struct.unpack_from('>I', d, pos + 8)[0]
        out.append((pos, c)); pos += c
    return out, pos


def is_locked(d):
    """NBA2KHUB IFF Locker mark"""
    return LOCK_MARK in bytes(d[:_u(d, 4)])


def is_marked(d):
    """header in the marked order (bytes between 0x20 and the record list, which sits before the chunk records)"""
    try:
        hs, cr, lp, n, recs = _parse(d)
    except Exception:
        return False
    return bool(recs) and 0x20 < lp and lp + 4 * n == cr < min(recs) <= hs


def mark_text(extra):
    return bytes(extra).split(bytes(1))[0].decode('latin-1').strip()


def _assemble(d, hs, blocks, n):
    """Rebuild the header from blocks [(name, bytes)] placed after the first 0x20 bytes; fix every pointer."""
    old = {name: (off, data) for name, off, data in blocks}
    new_hdr = bytearray(d[:0x20]); at = {}
    for name, off, data in blocks:
        at[name] = len(new_hdr); new_hdr += data
    nhs = len(new_hdr); delta = nhs - hs
    _set_ptr(new_hdr, 0x14, at['chunks'], _u(d, 0x14))
    _set_ptr(new_hdr, 0x1C, at['list'], _u(d, 0x1C))
    rec_old = old['records'][0]
    for i in range(n):
        e_old = old['list'][0] + 4 * i
        tgt = _target(d, e_old) - rec_old + at['records']
        _set_ptr(new_hdr, at['list'] + 4 * i, tgt, _u(d, e_old))
    struct.pack_into('<I', new_hdr, 4, nhs)
    # chunk descriptors (file offset, compressed length) live in the chunk records; the tail offset at 0x8
    chain, tail = _chunks(d, hs)
    for pos, c in chain:
        k = new_hdr.find(struct.pack('<II', pos, c), at['chunks'], at['chunks'] + len(old['chunks'][1]))
        if k < 0:
            raise ValueError('chunk descriptor not found in the header')
        struct.pack_into('<I', new_hdr, k, pos + delta)
    if struct.unpack_from('>I', d, 8)[0] != tail:
        raise ValueError('unexpected header (tail offset at 0x8)')
    struct.pack_into('>I', new_hdr, 8, tail + delta)
    return bytes(new_hdr) + bytes(d[hs:])


def unlock(d):
    """(normal-layout bytes, lock info) for a locked file; raises on an unknown lock layout."""
    hs, cr, lp, n, recs = _parse(d)
    r0 = min(recs)
    mark = 0x20
    # expected locked order: mark bytes, pointer list, chunk records, records (to the header end)
    if not (mark < lp < cr < r0 and lp + 4 * n == cr):
        raise ValueError('unknown marked header layout')
    extra = bytes(d[mark:lp])
    blocks = [('chunks', cr, bytes(d[cr:r0])), ('list', lp, bytes(d[lp:cr])), ('records', r0, bytes(d[r0:hs]))]
    return _assemble(d, hs, blocks, n), extra


def relock(d, extra):
    """Put a normal-layout file back in the locked layout with the same lock bytes."""
    hs, cr, lp, n, recs = _parse(d)
    r0 = min(recs)
    if not (cr == 0x20 and cr < lp < r0 and lp + 4 * n == r0):
        raise ValueError('unexpected header layout to lock')
    blocks = [('extra', None, extra), ('list', lp, bytes(d[lp:r0])), ('chunks', cr, bytes(d[cr:lp])),
              ('records', r0, bytes(d[r0:hs]))]
    return _assemble(d, hs, blocks, n)
