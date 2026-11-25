"""Read / rewrite the 2K14 head mesh (LOD0) with a different vertex and index count.

Layout found in png1307 (all fields are located from the file, not hard-coded):
  mesh node (chunk0):  [.. prim_count, ->prim table, 1, ->vertex desc, 0, 8, 3, 7, ->?, ->?, 0, index_count, IB ptr]
                       IB ptr = chunk1 offset | 1, u16 triangle strip
  vertex desc:         [->?, 0, V, n_streams=3, then per stream {.., stride, stride*V, ptr}]
       stride 8   POSITION SHORT4N (w = tangent handedness), chunk0, self-relative ptr | 1
       stride 28  NORMAL SHORT4N, TANGENT SHORT4N (w = handedness), UV SHORT2N, BLENDIDX UBYTE4, BLENDW UBYTE4N;
                  chunk1 absolute ptr | 1
       stride 4   baked AO float, chunk0, self-relative ptr | 1
  prim records (0x40): [5, idx_start, idx_count, idx_count-2, 0, minV, numV, ->bone palette, material, ...]
  prim ranges (0x20 each, same order): [.., .., .., .., numV*128, numV*8, minV*8, minV*8]
  file header: 0x2C = chunk0 size, 0x4C = chunk1 size (memory sizes, beside the ZLIB descriptors)

Rewriting never moves existing data: new vertex / index buffers are appended to the ends of chunk0 and chunk1
and the descriptor fields are repointed, so textures, LOD1 and everything else stay byte-identical.
"""
import struct
import numpy as np

POS_SCALE, POS_OFF = 70.0, np.array([0.0, 50.0, 0.0])


def _u32(b, o):
    return struct.unpack_from('<I', b, o)[0]


def locate(f):
    c0 = bytes(f.chunks[0])
    # vertex desc: stride-8 stream record followed (somewhere) by the 28 and 4 stride records with the same V
    for j in range(0, len(c0) - 12, 4):
        s, n = struct.unpack_from('<II', c0, j)
        if s == 8 and n % 8 == 0 and 100 < n // 8 < 65536:
            V = n // 8
            if _u32(c0, j - 28) == V and c0.find(struct.pack('<II', 28, 28 * V)) > 0:
                break
    else:
        raise ValueError('vertex description not found')
    L = dict(V=V, v_field=j - 28, pos_rec=j, s1_rec=c0.find(struct.pack('<II', 28, 28 * V)),
             ao_rec=c0.find(struct.pack('<II', 4, 4 * V), j))
    desc = L['v_field'] - 12              # [0, ->decl, 0, V, n_streams, ...]
    # mesh node: a self-relative pointer to the vertex desc; prim table pointer two words before it
    node = None
    for k in range(0, len(c0) - 4, 4):
        x = _u32(c0, k)
        if x & 1 and k + (struct.unpack_from('<i', c0, k)[0] & ~1) == desc:
            node = k; break
    if node is None:
        raise ValueError('mesh node not found')
    L['vdesc_ptr'] = node
    L['prim_count'] = node - 12
    L['prim_ptr'] = node - 8
    L['idx_count'] = node + 32
    L['ib_ptr'] = node + 36
    nprim = _u32(c0, L['prim_count'])
    pt = L['prim_ptr'] + (struct.unpack_from('<i', c0, L['prim_ptr'])[0] & ~1)
    L['prims'] = [pt + 0x40 * i for i in range(nprim)]
    assert all(_u32(c0, p) == 5 for p in L['prims']), 'prim table'
    # per-prim vertex ranges (numV*128, numV*8, minV*8, minV*8) after the stream records
    p0 = L['prims'][0]; mv0, nv0 = struct.unpack_from('<II', c0, p0 + 20)
    pat = struct.pack('<4I', nv0 * 128, nv0 * 8, mv0 * 8, mv0 * 8)
    r0 = c0.find(pat, L['ao_rec']) - 16
    assert r0 > 0, 'prim range table'
    L['ranges'] = [r0 + 0x20 * i for i in range(nprim)]
    for i, p in enumerate(L['prims']):
        mv, nv = struct.unpack_from('<II', c0, p + 20)
        assert struct.unpack_from('<4I', c0, L['ranges'][i] + 16) == (nv * 128, nv * 8, mv * 8, mv * 8), 'range %d' % i
    return L


def read(f, L=None):
    L = L or locate(f)
    c0, c1 = bytes(f.chunks[0]), bytes(f.chunks[1])
    V = _u32(c0, L['v_field'])
    pp = L['pos_rec'] + 8; pos_off = pp + (_u32(c0, pp) & ~1)
    ap = L['ao_rec'] + 8; ao_off = ap + (_u32(c0, ap) & ~1)
    s1_off = _u32(c0, L['s1_rec'] + 8) & ~1
    ib = _u32(c0, L['ib_ptr']) & ~1; ni = _u32(c0, L['idx_count'])
    prims = []
    for p in L['prims']:
        t, s, n, n2, z, mv, nv = struct.unpack_from('<7I', c0, p)
        prims.append(dict(start=s, count=n, mv=mv, nv=nv, mat=_u32(c0, p + 0x20)))
    return dict(V=V, pos_raw=np.frombuffer(c0[pos_off:pos_off + 8 * V], '<i2').reshape(V, 4).copy(),
                s1=np.frombuffer(c1[s1_off:s1_off + 28 * V], np.uint8).reshape(V, 28).copy(),
                ao=np.frombuffer(c0[ao_off:ao_off + 4 * V], '<f4').copy(),
                strip=np.frombuffer(c1[ib:ib + 2 * ni], '<u2').copy(), prims=prims)


def strip_tris(strip, start, count):
    """triangles (with correct winding) of one strip prim"""
    seg = strip[start:start + count]; out = []
    for i in range(len(seg) - 2):
        a, b, c = int(seg[i]), int(seg[i + 1]), int(seg[i + 2])
        if a == b or b == c or a == c:
            continue
        out.append((a, b, c) if i % 2 == 0 else (b, a, c))
    return np.array(out, int).reshape(-1, 3)


def tris_to_strip_simple(tris):
    """triangle list -> one strip (degenerate joins), every real triangle at an even position (winding kept)"""
    out = []
    for a, b, c in tris:
        if out:
            out += [out[-1], a, a]
            if (len(out) - 1) % 2:
                out.append(a)
            out += [b, c]
        else:
            out = [a, b, c]
    return np.array(out, np.int64)


def tris_to_strip(tris):
    """triangle list -> one strip. Greedy: keep walking to an unused neighbour that shares the strip's last edge
    with the winding the strip parity needs (triangle at even position i = (s[i], s[i+1], s[i+2]), odd = (s[i+1],
    s[i], s[i+2])), so connected cards cost ~1 index per triangle; separate runs are joined with degenerates and
    every run starts at an even position. ~2-3x shorter than one-run-per-triangle: the game drops the end of a
    prim's strip past 2^17 indices (beard + hair lost the last part of the beard in game)."""
    tris = np.asarray(tris, np.int64)
    if len(tris) == 0:
        return np.zeros(0, np.int64)
    edge = {}                                    # directed edge (u, v) -> [tri ids having it]
    for t, (a, b, c) in enumerate(tris):
        for u, v in ((a, b), (b, c), (c, a)):
            edge.setdefault((int(u), int(v)), []).append(t)
    used = np.zeros(len(tris), bool)

    def third(t, u, v):
        a, b, c = (int(x) for x in tris[t])
        for p, q, r in ((a, b, c), (b, c, a), (c, a, b)):
            if p == u and q == v:
                return r
        return None

    def take(u, v):
        for t in edge.get((u, v), ()):
            if not used[t]:
                return t
        return None

    out = []
    for t0 in range(len(tris)):
        if used[t0]:
            continue
        a, b, c = (int(x) for x in tris[t0])
        if out:
            out += [out[-1], a, a]
            if (len(out) - 1) % 2:
                out.append(a)
            out += [b, c]
        else:
            out = [a, b, c]
        used[t0] = True
        while True:
            x, y = out[-2], out[-1]
            i = len(out) - 2                    # position of the next triangle
            t = take(x, y) if i % 2 == 0 else take(y, x)
            if t is None:
                break
            d = third(t, x, y) if i % 2 == 0 else third(t, y, x)
            used[t] = True; out.append(d)
    return np.array(out, np.int64)


def strip_to_tris(strip):
    """strip -> real triangles with their winding (degenerates dropped); for checking tris_to_strip"""
    s = np.asarray(strip); out = []
    for i in range(len(s) - 2):
        a, b, c = s[i], s[i + 1], s[i + 2]
        if a == b or b == c or a == c:
            continue
        out.append((a, b, c) if i % 2 == 0 else (b, a, c))
    return out


def _align(n, a):
    return (n + a - 1) // a * a


def write(f, L, pos_raw, s1, ao, strip, prims):
    """prims: list of dict(start, count, mv, nv) in the original prim order (materials unchanged)"""
    V = len(pos_raw); assert V < 65536 and strip.max() < V
    c0, c1 = f.chunks[0], f.chunks[1]
    hdr = f.d
    # chunk0: append positions + AO
    o_pos = _align(len(c0), 128); c0 += b'\0' * (o_pos - len(c0)); c0 += pos_raw.astype('<i2').tobytes()
    o_ao = _align(len(c0), 16); c0 += b'\0' * (o_ao - len(c0)); c0 += ao.astype('<f4').tobytes()
    c0 += b'\0' * (_align(len(c0), 128) - len(c0))
    # chunk1: the game only uploads chunk1 ranges listed in the header's VRAM block table
    # [count, (code, offset) x count], size = (code >> 12) * 4. Entry 0 is the mesh block (offset 0, ending
    # where the textures start); the rest are texture mip groups. New index buffer + stream1 are inserted at
    # the end of the mesh block, the block is grown, and every texture offset is shifted back by the same amount.
    import layout
    free = layout.is_free(f)          # no block table (layout.free_layout): only the texture offsets move
    blk = free_vram(f) if free else vram_blocks(f)
    ins = blk['mesh_end']
    payload = bytearray()
    o_ib = ins; payload += strip.astype('<u2').tobytes()
    payload += b'\0' * (_align(len(payload), 128) - len(payload))
    o_s1 = ins + len(payload); payload += s1.astype(np.uint8).tobytes()
    delta = _align(len(payload), 0x1000); payload += b'\0' * (delta - len(payload))
    c1[ins:ins] = payload
    new_size = ins + delta
    if not free:
        assert new_size // 4 < (1 << 20), 'mesh block too large for the VRAM table'
        code0 = _u32(hdr, blk['entry0'])
        struct.pack_into('<I', hdr, blk['entry0'], ((new_size // 4) << 12) | (code0 & 0xFFF))
    for e in blk['entries'][1:]:
        o = _u32(hdr, e + 4)
        if o >= ins:
            struct.pack_into('<I', hdr, e + 4, o + delta)
    for o1_field in blk['tex_o1_fields']:
        struct.pack_into('<I', hdr, o1_field, _u32(hdr, o1_field) + delta)
    struct.pack_into('<I', hdr, 0x4C, _u32(hdr, 0x4C) + delta)

    def put(o, v):
        struct.pack_into('<I', c0, o, v)
    put(L['v_field'], V)
    for rec, stride, off, rel in ((L['pos_rec'], 8, o_pos, True), (L['s1_rec'], 28, o_s1, False), (L['ao_rec'], 4, o_ao, True)):
        put(rec + 4, stride * V)
        put(rec + 8, ((off - (rec + 8)) if rel else off) | 1)
    put(L['idx_count'], len(strip)); put(L['ib_ptr'], o_ib | 1)
    for p, r, pr in zip(L['prims'], L['ranges'], prims):
        struct.pack_into('<4I', c0, p + 4, pr['start'], pr['count'], pr['count'] - 2, 0)
        struct.pack_into('<2I', c0, p + 20, pr['mv'], pr['nv'])
        struct.pack_into('<4I', c0, r + 16, pr['nv'] * 128, pr['nv'] * 8, pr['mv'] * 8, pr['mv'] * 8)
    struct.pack_into('<I', hdr, 0x2C, len(c0))
    return dict(o_pos=o_pos, o_ao=o_ao, o_ib=o_ib, o_s1=o_s1, inserted=delta)


def free_vram(f):
    """vram_blocks() for a file without a block table: the mesh part ends where the first texture starts"""
    TEX = 0x5C369069
    hdr = f.d; tex_o1 = []; i = f.toc_start
    while i + 20 <= f.toc_end:
        h, t, n = struct.unpack_from('<III', hdr, i)
        if t == TEX and n == 2:
            tex_o1.append(i + 16); i += 20
        else:
            i += 4
    return dict(entries=[], mesh_end=min(_u32(hdr, o) for o in tex_o1), tex_o1_fields=tex_o1)


def vram_blocks(f):
    """Locate the header VRAM block table and the texture records' chunk1 offsets."""
    TEX = 0x5C369069
    hdr = f.d; toc0, toc1 = f.toc_start, f.toc_end
    tex_o1 = []
    i = toc0
    while i + 20 <= toc1:
        h, t, n = struct.unpack_from('<III', hdr, i)
        if t == TEX and n == 2:
            tex_o1.append(i + 16); i += 20
        else:
            i += 4
    tex_start = min(_u32(hdr, o) for o in tex_o1)
    for e0 in range(toc0, toc1 - 8, 4):
        code, off = struct.unpack_from('<II', hdr, e0)
        if off == 0 and (code >> 12) * 4 == tex_start and (code & 0xFFF):
            break
    else:
        raise ValueError('VRAM block table not found')
    n = _u32(hdr, e0 - 4)
    assert 1 < n < 64, 'VRAM block count'
    return dict(entry0=e0, entries=[e0 + 8 * k for k in range(n)], mesh_end=tex_start, tex_o1_fields=tex_o1)


def quant_pos(p, w):
    q = np.round((p - POS_OFF) / POS_SCALE * 32767); assert np.abs(q).max() <= 32767, 'position out of range'
    return np.c_[q, w].astype('<i2')
