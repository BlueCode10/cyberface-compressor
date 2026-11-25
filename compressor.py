"""Cyberface Compressor core: make an NBA 2K14 cyberface (png####.iff) smaller.

A 2K14 .iff holds two zlib chunks (chunk0: skeleton / mesh records / texture headers, chunk1: vertex + index
buffers and every texture's mip chain). Two ways to shrink it:

  1. Lossless (always): recompress both chunks with zopfli, a slower deflate encoder whose output is ordinary
     zlib, so the game reads it like any other file. The decompressed bytes are identical (checked after
     writing). Typically 3-4% smaller.

  2. Texture downscale (opt-in, per texture): halve or quarter a texture by dropping its top mip levels. The
     next level down is already in the file (the converter writes full box-filtered mip chains), so nothing is
     re-encoded. The file is rewritten in the free layout (no VRAM block table, as RED MC and the converter's
     HD mode write it).

Locked (NBA2KHUB IFF Locker) files: the header is put back in the normal order in memory (lockfmt.unlock), edited
like any other file, then locked again the same way (lockfmt.relock), so the output stays locked.

Command line:  python compressor.py file.iff [--chest_color 1024] [--face_color 512] [--hair_color 256] [-o out.iff]
"""
import os, sys, struct, zlib, time, tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__))), 'lib'))
from iff2k import Iff  # noqa: E402
import layout  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lockfmt  # noqa: E402

try:
    import zopfli.zlib as _zopfli
except ImportError:                     # still works (zlib level 9 only), just without the lossless gain
    _zopfli = None

K14_MAGIC = bytes.fromhex('94EF3BFF')
ZOPFLI_ITERATIONS = 5                   # 15 (zopfli's default) is ~0.3% smaller and 3x slower


def _deflate(data, iterations):
    """smallest zlib stream for data: zopfli when available, else (or if somehow larger) zlib level 9"""
    best = zlib.compress(data, 9)
    if _zopfli is not None:
        z = _zopfli.compress(data, numiterations=iterations)
        if len(z) < len(best):
            best = z
    return best


def load(path):
    """(Iff, lock bytes or None). A locked file is read through a normal-layout copy of its header."""
    with open(path, 'rb') as fh:
        d = fh.read()
    if d[:4] != K14_MAGIC:
        raise ValueError('%s is not an NBA 2K14 .iff file' % os.path.basename(path))
    if not lockfmt.is_marked(d):
        return Iff(path), None
    u, extra = lockfmt.unlock(d)
    fd, tmp = tempfile.mkstemp(suffix='.iff')
    try:
        with os.fdopen(fd, 'wb') as fh:
            fh.write(u)
        return Iff(tmp), extra
    finally:
        os.remove(tmp)


# texture names by hash, for files whose name table leaves them out (e.g. RED MC's LeBron James sample)
KNOWN = {zlib.crc32(n.encode()) & 0xFFFFFFFF: n for n in
         ('face_color', 'face_normal', 'face_occlusion', 'chest_color', 'hair_color', 'facialhair_color',
          'headband_color', 'eye_color', 'teeth_color')}


def _name_textures(f):
    for h, n in KNOWN.items():
        f.names.setdefault(h, n)


def is_free(f):
    """textures stored as plain mip chains at their record offsets: no VRAM block table after the chunk1 record.
    The converter / RED MC write type 6; older RED MC saves keep type 5 but still drop the table (inner marker 0)."""
    r = layout._c1_rec(f)
    return struct.unpack_from('<I', f.d, r + 28)[0] != 5


def _fmt(fourcc):
    if fourcc in (b'DXT1', b'DXT5'):
        return fourcc.decode()
    if fourcc[0] == 0x15:
        return 'ARGB'
    raise ValueError('unknown texture format %r' % fourcc)


def _lvl(fmt, w, h):
    if fmt == 'ARGB':
        return w * h * 4
    return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * (8 if fmt == 'DXT1' else 16)


def _levels(t):
    """[(w, h, bytes)] of the chain the header declares (mip count at 0x66 bits 7..)"""
    out, w, h = [], t['w'], t['h']
    for _ in range(t['mips']):
        out.append((w, h, _lvl(t['fmt'], w, h))); w, h = max(1, w // 2), max(1, h // 2)
    return out


def free_textures(f):
    """texture records of a free-layout file, in chunk1 order, each with its chain size and smallest allowed size"""
    out, i = [], f.toc_start
    while i + 20 <= f.toc_end:
        h, t, n = struct.unpack_from('<III', f.d, i)
        if t == layout.TEX and n == 2:
            o0, o1 = struct.unpack_from('<II', f.d, i + 12)
            hb = f.chunks[0][o0:o0 + 0xC0]
            w, hh = struct.unpack_from('<HH', hb, 0x68)
            mips = struct.unpack_from('<H', hb, 0x66)[0] >> 7
            out.append(dict(name=f.names.get(h, hex(h)), rec=i, o0=o0, o1=o1, fmt=_fmt(bytes(hb[8:12])),
                            w=w, h=hh, mips=mips)); i += 20
        else:
            i += 4
    out.sort(key=lambda t: t['o1'])
    for k, t in enumerate(out):
        t['size'] = sum(n for _, _, n in _levels(t))
        nxt = out[k + 1]['o1'] if k + 1 < len(out) else len(f.chunks[1])
        if t['o1'] + t['size'] > nxt:
            raise ValueError('%s mip chain runs past the next texture: downscaling disabled' % t['name'])
        # resizable: square, power of two, full chain down to 1x1
        full = t['w'] == t['h'] and t['w'] & (t['w'] - 1) == 0 and t['mips'] == t['w'].bit_length()
        t['min'] = _min_size(t) if full else t['w']
    last = out[-1] if out else None
    if last and any(f.chunks[1][last['o1'] + last['size']:]):
        raise ValueError('data after the textures in chunk1: downscaling disabled')
    return out


def inspect(path):
    """What the file holds and what can be done with it (for the UI)."""
    f, extra = load(path)
    _name_textures(f)
    locked = extra is not None and lockfmt.LOCK_MARK in extra
    mark = lockfmt.mark_text(extra) if extra is not None else None
    texs, note = [], None
    try:
        if is_free(f):
            texs = [dict(name=t['name'], fmt=t['fmt'], w=t['w'], h=t['h'], min=t['min'], bytes=t['size'])
                    for t in free_textures(f)]
        else:
            tl = layout.textures(f)
            pos = layout.positions(f) if tl else {}
            texs = [dict(name=t['name'], fmt=t['fmt'], w=t['w'], h=t['h'], min=_min_size(t),
                         bytes=sum(layout.level_size(t['fmt'], d) for (n, d) in pos if n == t['name'])) for t in tl]
            if tl:
                _check_packed_ok(f, tl, pos)
    except Exception as e:
        note = str(e); texs = [dict(t, min=t['w']) for t in texs]
    if not texs and not note:
        note = 'No texture records found: lossless only.'
    return dict(size=os.path.getsize(path), locked=locked, mark=mark, textures=texs, note=note,
                chunks=[dict(ulen=u, clen=c) for _, u, c in f.chain], zopfli=_zopfli is not None)


def _min_size(t):
    return 64 if t['w'] >= 64 else t['w']


def _check_packed_ok(f, texs, pos):
    """free_layout keeps chunk1 up to the first texture and rewrites the textures after it; refuse if anything
    else (e.g. appended mesh buffers) lives after the textures, or a chain is incomplete."""
    end = 0
    for t in texs:
        d = t['w']
        while d >= 1:
            if (t['name'], d) not in pos:
                raise ValueError('%s mip chain is incomplete: downscaling disabled' % t['name'])
            end = max(end, pos[(t['name'], d)] + layout.level_size(t['fmt'], d))
            d //= 2
    if end != len(f.chunks[1]) and any(f.chunks[1][end:]):
        raise ValueError('data after the textures in chunk1: downscaling disabled')


def _wanted(texs, sizes, log):
    want = {}
    for t in texs:
        w = sizes.get(t['name'])
        if not w or w >= t['w']:
            continue
        if t.get('min', 0) == t['w']:
            log('%s %dx%d kept: only square textures with a full mip chain can be resized' % (t['name'], t['w'], t['h']))
            continue
        if w & (w - 1) or w < t.get('min', _min_size(t)):
            raise ValueError('%s: %d is not a valid size' % (t['name'], w))
        want[t['name']] = w
        log('%s %d -> %d' % (t['name'], t['w'], w))
    return want


def kept_regions(f):
    """[(start, end)] of chunk1 data that is not texture mip chains: the part before the first texture and any
    block between textures (e.g. RED MC's LeBron James stores the head mesh buffers between face_occlusion and
    face_normal). These are referenced by absolute chunk1 offsets, so they must stay byte for byte where they are.
    Alignment padding (up to 64 zero bytes) is not kept."""
    if not is_free(f):
        texs = layout.textures(f)
        return [(0, texs[0]['o1'])] if texs and texs[0]['o1'] else []
    texs = free_textures(f); c1 = f.chunks[1]; out = []
    if texs and texs[0]['o1'] > 0:
        out.append((0, texs[0]['o1']))
    for k, t in enumerate(texs):
        end = t['o1'] + t['size']
        nxt = texs[k + 1]['o1'] if k + 1 < len(texs) else len(c1)
        if nxt - end > 64 or any(c1[end:nxt]):
            out.append((end, nxt))
    return out


def _downscale_free(f, sizes, log):
    """Rewrite the textures in chunk1: every chain kept byte for byte (odd ones, e.g. a 512x128 single-level hair,
    included) except the resized ones, whose chain starts at the new size's existing level. Non-texture data stays
    at its offset (kept_regions). Textures before the last such block shrink in place (freed space zeroed, which
    compresses to almost nothing); textures after it are packed (16-byte aligned starts) and the chunk shortened.
    Record offsets, header sizes / mip counts and the chunk1 size field are updated."""
    texs = free_textures(f)
    want = _wanted(texs, sizes, log)
    if not want:
        return False
    c1 = f.chunks[1]
    boundary = max((e for _, e in kept_regions(f)), default=0)
    body = bytearray(c1[:boundary])
    for t in texs:
        if t['name'] in want:
            w = want[t['name']]; skip = 0; d = t['w']
            while d > w:
                skip += _lvl(t['fmt'], d, d); d //= 2
            blob = bytes(c1[t['o1'] + skip:t['o1'] + t['size']])
            m = struct.unpack_from('<H', f.chunks[0], t['o0'] + 0x66)[0]
            struct.pack_into('<H', f.chunks[0], t['o0'] + 0x66, (m & 0x7F) | (w.bit_length() << 7))
            struct.pack_into('<HH', f.chunks[0], t['o0'] + 0x68, w, w)
        else:
            blob = bytes(c1[t['o1']:t['o1'] + t['size']])
        if t['o1'] < boundary:                 # in place: same offset, the rest of the old chain zeroed
            body[t['o1']:t['o1'] + t['size']] = blob + bytes(t['size'] - len(blob))
        else:
            body += bytes(((len(body) + 15) & ~15) - len(body))
            struct.pack_into('<I', f.d, t['rec'] + 16, len(body))
            body += blob
    c1[:] = body
    struct.pack_into('<I', f.d, layout._c1_rec(f) + 12, len(c1))
    return True


def _only_tex_headers_changed(a, b, g):
    """chunk0 may differ only in the texture headers' mip count / size fields (0x66..0x6B)"""
    if len(a) != len(b):
        return False
    ok = []
    i = g.toc_start
    while i + 20 <= g.toc_end:
        h, t, n = struct.unpack_from('<III', g.d, i)
        if t == layout.TEX and n == 2:
            o0 = struct.unpack_from('<I', g.d, i + 12)[0]; ok.append((o0 + 0x66, o0 + 0x6C)); i += 20
        else:
            i += 4
    return all(any(s <= k < e for s, e in ok) for k in range(len(a)) if a[k] != b[k])


def _downscale(f, sizes, log):
    if is_free(f):
        return _downscale_free(f, sizes, log)
    texs = layout.textures(f)
    pos = layout.positions(f)
    _check_packed_ok(f, texs, pos)
    want = _wanted(texs, sizes, log)
    if not want:
        return False
    keep = {}
    for t in texs:
        if t['name'] in want:
            lv, d = [], want[t['name']]          # the existing levels from w down to 1x1 become the new chain
            while d >= 1:
                o = pos[(t['name'], d)]; lv.append(bytes(f.chunks[1][o:o + layout.level_size(t['fmt'], d)])); d //= 2
            keep[t['name']] = (t['fmt'], lv)
    layout.free_layout(f, want)
    for name, (fmt, lv) in keep.items():
        layout.write_levels(f, name, fmt, lv)
    return True


def rebuild(f, chunks, iterations, log):
    """Write f.d's header + the given chunks recompressed + the name table. Patches each chunk descriptor
    (LE file offset, compressed length) and the BE tail offset at 0x8, as iff2k.Iff.rebuild does."""
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=len(chunks)) as ex:
        comps = list(ex.map(lambda c: _deflate(bytes(c), iterations), chunks))
    log('compressed in %.1fs' % (time.time() - t0))
    first = f.chain[0][0]
    head = bytearray(f.d[:first]); body = bytearray(); pos = first
    for (p, u, c), data, comp in zip(f.chain, chunks, comps):
        clen = len(comp) + 16
        desc = head.find(struct.pack('<II', p, c), 0x10, first)
        if desc < 0:
            raise ValueError('chunk descriptor not found in the header')
        struct.pack_into('<II', head, desc, pos, clen)
        body += b'ZLIB' + struct.pack('>II', len(data), clen) + b'\0\0\0\0' + comp
        pos += clen
    if struct.unpack_from('>I', head, 8)[0] != f.tail_pos:
        raise ValueError('unexpected header (tail offset at 0x8)')
    struct.pack_into('>I', head, 8, pos)
    return bytes(head + body + f.d[f.tail_pos:])


def compress(src, dst, sizes=None, iterations=ZOPFLI_ITERATIONS, log=print):
    """Write a smaller copy of src to dst. sizes: {texture name: new width} (only smaller sizes apply).
    Returns dict(before, after, lossless)."""
    if os.path.abspath(src) == os.path.abspath(dst):
        raise ValueError('refusing to overwrite the original file')
    f, extra = load(src)
    _name_textures(f)
    before = os.path.getsize(src)
    orig = [bytes(c) for c in f.chunks]
    kept = [(s, orig[1][s:e]) for s, e in kept_regions(f)]
    changed = False
    if sizes and any(sizes.values()):
        changed = _downscale(f, sizes, log)
    out = rebuild(f, f.chunks, iterations, log)
    if extra is not None:
        out = lockfmt.relock(out, extra)
        log('header mark kept: %s' % lockfmt.mark_text(extra))
    # verify: the written file parses and decompresses to exactly the chunks we meant to write
    tmp = dst + '.part'
    with open(tmp, 'wb') as fh:
        fh.write(out)
    try:
        g, g_extra = load(tmp)
        if (g_extra is None) != (extra is None):
            raise ValueError('verification failed: lock state changed')
        expect = [bytes(c) for c in f.chunks]
        if [bytes(c) for c in g.chunks] != expect:
            raise ValueError('verification failed: decompressed data differs')
        if not changed and expect != orig:
            raise ValueError('verification failed: lossless output differs from the original')
        if changed:                   # the texture layout still reads
            _name_textures(g)
            free_textures(g) if is_free(g) else layout.positions(g)
            # every non-texture byte of chunk1 (mesh buffers etc.) is still there, at the same offset
            g1 = bytes(g.chunks[1])
            for s, data in kept:
                if g1[s:s + len(data)] != data:
                    raise ValueError('verification failed: mesh / non-texture data at chunk1 %#x moved or changed' % s)
            if bytes(g.chunks[0]) != orig[0] and not _only_tex_headers_changed(orig[0], bytes(g.chunks[0]), g):
                raise ValueError('verification failed: chunk0 changed outside the texture headers')
        if len(out) >= before and not changed:
            log('no gain: the file is already as small as this tool can make it')
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return dict(before=before, after=len(out), lossless=not changed)


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description='Make an NBA 2K14 cyberface .iff smaller.')
    ap.add_argument('src'); ap.add_argument('-o', '--out')
    ap.add_argument('--iterations', type=int, default=ZOPFLI_ITERATIONS)
    for n in ('face_color', 'face_normal', 'chest_color', 'face_occlusion', 'hair_color'):
        ap.add_argument('--' + n, type=int, default=0, metavar='SIZE', help='downscale %s to SIZE' % n)
    a = ap.parse_args(argv)
    sizes = {n: getattr(a, n) for n in ('face_color', 'face_normal', 'chest_color', 'face_occlusion', 'hair_color')}
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.src)), 'Compressed', os.path.basename(a.src))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    info = inspect(a.src)
    if info['note']:
        print(info['note'])
    r = compress(a.src, out, sizes, a.iterations)
    print('%s: %d -> %d bytes (%.1f%% smaller, %s)' % (out, r['before'], r['after'],
          100.0 * (r['before'] - r['after']) / r['before'], 'lossless' if r['lossless'] else 'textures downscaled'))


if __name__ == '__main__':
    main(sys.argv[1:])
