"""Bounded RGB seam processing. Diagnostics are heuristics, not quality certification."""
from statistics import median

from PIL import Image, ImageChops, ImageStat


SIDES = ('left', 'right', 'top', 'bottom')


def margins(canvas):
    x, y = canvas['offset_x'], canvas['offset_y']
    return dict(left=x, top=y,
                right=canvas['width'] - x - canvas['original_width'],
                bottom=canvas['height'] - y - canvas['original_height'])


def _smooth(t):
    t = max(0., min(1., t))
    return t * t * (3 - 2 * t)


def build_seam_mask(size, actual, feather_px):
    """255 means exact source. Nearest active edge wins, including corners."""
    w, h = size
    width = min(feather_px, (w - 1) // 2, (h - 1) // 2)
    if width < 1:
        return Image.new('L', size, 255), 0
    def axis(length, first, last):
        values = []
        for i in range(length):
            distance = min(i if first else width, length - 1 - i if last else width)
            values.append(round(255 * _smooth(distance / width)))
        return values
    horizontal = Image.new('L', (w, 1))
    horizontal.putdata(axis(w, actual['left'], actual['right']))
    vertical = Image.new('L', (1, h))
    vertical.putdata(axis(h, actual['top'], actual['bottom']))
    return ImageChops.darker(horizontal.resize(size), vertical.resize(size)), width


def verify_core(original, result, canvas, mask):
    x, y = canvas['offset_x'], canvas['offset_y']
    crop = result.crop((x, y, x + original.width, y + original.height))
    difference = ImageChops.difference(original, crop)
    combined = difference.getchannel(0)
    for band in difference.split()[1:]:
        combined = ImageChops.lighter(combined, band)
    protected = mask.point(lambda p: 255 if p == 255 else 0)
    different = ImageChops.multiply(combined, protected)
    changed = original.width * original.height - different.histogram()[0]
    if changed:
        raise ValueError(f'原图核心像素保护校验失败：{changed} 个像素不同')
    return dict(verified=True, different_pixels=0, scope='core',
                protected_pixels=protected.histogram()[255],
                allowed_change_pixels=original.width * original.height - protected.histogram()[255])


def seam_metrics(image, canvas, inner=0):
    """Mean RGB jump across active boundaries (and optionally feather inner joins)."""
    x, y = canvas['offset_x'], canvas['offset_y']
    w, h = canvas['original_width'], canvas['original_height']
    actual = margins(canvas)
    pairs = {
        'left': ((x-1+inner, y, x+inner, y+h), (x+inner, y, x+1+inner, y+h)),
        'right': ((x+w-1-inner, y, x+w-inner, y+h), (x+w-inner, y, x+w+1-inner, y+h)),
        'top': ((x, y-1+inner, x+w, y+inner), (x, y+inner, x+w, y+1+inner)),
        'bottom': ((x, y+h-1-inner, x+w, y+h-inner), (x, y+h-inner, x+w, y+h+1-inner)),
    }
    values = {side: round(sum(ImageStat.Stat(ImageChops.difference(image.crop(a), image.crop(b))).mean)/3, 4)
              for side, (a, b) in pairs.items() if actual[side] > 0}
    return dict(edges=values, max_jump=max(values.values(), default=0))


def _ramp_mask(size, canvas, reach, inside):
    """255 at the seam edge, fading to 0 after `reach` px on one side of it."""
    x, y = canvas['offset_x'], canvas['offset_y']
    w, h = canvas['original_width'], canvas['original_height']
    cw, ch = size
    mask = Image.new('L', size)
    if reach < 1:
        return mask
    for side in SIDES:
        if not margins(canvas)[side]:
            continue
        if side in ('left', 'right'):
            if inside:
                start, end = (x, min(x + reach, x + w)) if side == 'left' else (max(x, x + w - reach), x + w)
                dist = [i - x if side == 'left' else x + w - 1 - i for i in range(start, end)]
            else:
                start, end = (max(0, x - reach), x) if side == 'left' else (x + w, min(cw, x + w + reach))
                dist = [x - 1 - i if side == 'left' else i - (x + w) for i in range(start, end)]
            n = end - start
            if n < 1:
                continue
            strip = Image.new('L', (n, 1))
            strip.putdata([round(255 * (1 - _smooth(d / reach))) for d in dist])
            strip = strip.resize((n, ch))
            mask.paste(ImageChops.lighter(mask.crop((start, 0, end, ch)), strip), (start, 0))
        else:
            if inside:
                start, end = (y, min(y + reach, y + h)) if side == 'top' else (max(y, y + h - reach), y + h)
                dist = [i - y if side == 'top' else y + h - 1 - i for i in range(start, end)]
            else:
                start, end = (max(0, y - reach), y) if side == 'top' else (y + h, min(ch, y + h + reach))
                dist = [y - 1 - i if side == 'top' else i - (y + h) for i in range(start, end)]
            n = end - start
            if n < 1:
                continue
            strip = Image.new('L', (1, n))
            strip.putdata([round(255 * (1 - _smooth(d / reach))) for d in dist])
            strip = strip.resize((cw, n))
            mask.paste(ImageChops.lighter(mask.crop((0, start, cw, end)), strip), (0, start))
    return mask


def _shift(image, delta):
    return Image.merge('RGB', [channel.point([max(0, min(255, round(v + delta[i]))) for v in range(256)])
                               for i, channel in enumerate(image.split())])


def _local_tone(original, candidate, canvas, width):
    """Estimate a small RGB offset on both sides of every active seam.

    `bias` is how far the model's redraw of the original drifted, so it can be removed
    inside the feather band; `seam` compares the generated band with the original edge
    and is applied only to the new area. Both are capped at +-16 levels.
    """
    x, y = canvas['offset_x'], canvas['offset_y']
    w, h = original.size
    actual = margins(canvas)
    samples, skipped = [], []
    for side in SIDES:
        if not actual[side]:
            continue
        # Never reach past the generated strip: short extensions stay unsampled.
        band = min(width, w // 4, h // 4, actual[side])
        if band < 2:
            skipped.append(side + ': extension too narrow')
            continue
        inner = (0, 0, band, h) if side == 'left' else (w - band, 0, w, h) if side == 'right' \
            else (0, 0, w, band) if side == 'top' else (0, h - band, w, h)
        outer = (x - band, y, x, y + h) if side == 'left' else (x + w, y, x + w + band, y + h) if side == 'right' \
            else (x, y - band, x + w, y) if side == 'top' else (x, y + h, x + w, y + h + band)
        a = ImageStat.Stat(original.crop(inner))
        r = ImageStat.Stat(candidate.crop((inner[0] + x, inner[1] + y, inner[2] + x, inner[3] + y)))
        g = ImageStat.Stat(candidate.crop(outer))
        bright = (sum(a.mean) / 3, sum(r.mean) / 3, sum(g.mean) / 3)
        if min(bright) < 8 or max(bright) > 247 or min(sum(a.stddev) / 3, sum(r.stddev) / 3, sum(g.stddev) / 3) < 2:
            skipped.append(side + ': flat or extreme samples')
            continue
        samples.append(dict(bias=[a.mean[i] - r.mean[i] for i in range(3)],
                            seam=[a.mean[i] - g.mean[i] for i in range(3)]))
    if not samples:
        return candidate, dict(applied=False, reason='insufficient reliable samples', skipped=skipped)
    if any(max(s['seam'][i] for s in samples) - min(s['seam'][i] for s in samples) > 24 for i in range(3)):
        return candidate, dict(applied=False, reason='edge estimates disagree', skipped=skipped)
    delta_in = [max(-16., min(16., median(s['bias'][i] for s in samples))) for i in range(3)]
    delta_out = [max(-16., min(16., median(s['seam'][i] for s in samples))) for i in range(3)]
    result = candidate
    if any(delta_out):
        result = Image.composite(_shift(candidate, delta_out), result,
                                 _ramp_mask(candidate.size, canvas, max(2, width * 2), inside=False))
    if any(delta_in):
        result = Image.composite(_shift(candidate, delta_in), result,
                                 _ramp_mask(candidate.size, canvas, max(2, width), inside=True))
    return result, dict(applied=True, offset_rgb=delta_out, redraw_offset_rgb=delta_in, skipped=skipped)


def blend_outpaint_seams(original, candidate, canvas, params):
    if original.mode != 'RGB' or candidate.mode != 'RGB':
        raise ValueError('融合要求已展平的 RGB 输入与候选图')
    if candidate.size != (canvas['width'], canvas['height']):
        raise ValueError('引擎输出尺寸与预期不符')
    x, y = canvas['offset_x'], canvas['offset_y']
    strict = candidate.copy()
    strict.paste(original, (x,y))
    mask, width = build_seam_mask(original.size, margins(canvas), params.get('feather_px',32))
    before = seam_metrics(strict, canvas)
    info = dict(requested_mode=params.get('seam_mode','strict'), effective_mode='strict',
                feather_px=0, tone=dict(applied=False, reason='disabled'), fallback=[],
                before=before, warning='接缝指标仅用于发现风险；构图、文字与纹理仍需人工检查。')
    if info['requested_mode'] == 'strict' or not width:
        if info['requested_mode'] != 'strict':
            info['fallback'].append('image too small for a protected core')
        info['after'] = before
        return strict, info, Image.new('L', original.size,255)
    box = (x,y,x+original.width,y+original.height)
    def composite(base):
        result = base.copy()
        result.paste(Image.composite(original, base.crop(box), mask),(x,y))
        verify_core(original,result,canvas,mask)
        return result
    feather = composite(candidate)
    result = feather
    if params.get('tone_match',True):
        toned, info['tone'] = _local_tone(original,candidate,canvas,width)
        result = composite(toned)
        if seam_metrics(result,canvas)['max_jump'] > seam_metrics(feather,canvas)['max_jump'] + 1:
            result = feather
            info['tone']['applied'] = False
            info['fallback'].append('tone correction increased boundary jump; feather only')
    after = seam_metrics(result,canvas)
    if after['max_jump'] > before['max_jump'] + 1:
        result = strict
        mask = Image.new('L',original.size,255)
        info['tone']['applied'] = False
        info['fallback'].append('feather increased boundary jump; strict result')
    else:
        info.update(effective_mode='feather',feather_px=width)
    info['after'] = seam_metrics(result,canvas)
    info['inner_join'] = seam_metrics(result,canvas,width)
    info['preservation'] = verify_core(original,result,canvas,mask)
    return result, info, mask
