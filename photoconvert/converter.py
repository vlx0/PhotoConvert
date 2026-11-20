"""Format discovery, image conversion and atomic output publication."""
import bz2
from contextlib import nullcontext
import gzip
import lzma
import os
from pathlib import Path
import tempfile
import zipfile

from .formats import AUDIO, VIDEO, TABLE, DOCUMENT, convert_media, convert_table, convert_document

IMAGES = {
    'PNG': ('.png', 'PNG'), 'JPG': ('.jpg', 'JPEG'), 'BMP': ('.bmp', 'BMP'),
    'WebP': ('.webp', 'WEBP'), 'GIF': ('.gif', 'GIF'), 'TIFF': ('.tiff', 'TIFF'),
    'ICO': ('.ico', 'ICO'), 'AVIF': ('.avif', 'AVIF'),
}
IMAGE_INPUTS = {extension for extension, _ in IMAGES.values()} | {'.jpeg', '.tif', '.ppm', '.pgm', '.tga'}
ARCHIVES = {'ZIP': '.zip', 'GZ': '.gz', 'BZ2': '.bz2', 'XZ': '.xz'}
COMPRESS = {'GZ': gzip.open, 'BZ2': bz2.open, 'XZ': lzma.open}
EXTENSIONS = {name: pair[0] for name, pair in IMAGES.items()}
EXTENSIONS.update({**AUDIO, **VIDEO, **TABLE, **DOCUMENT, **ARCHIVES})
MEDIA_INPUTS = set(AUDIO.values()) | set(VIDEO.values()) | {
    '.aac', '.aiff', '.aif', '.opus', '.wma', '.avi', '.mpeg', '.mpg', '.flv', '.wmv', '.ts', '.m4v',
}
VIDEO_INPUTS = MEDIA_INPUTS - set(AUDIO.values()) - {'.aac', '.aiff', '.aif', '.opus', '.wma'}
TABLE_INPUTS = set(TABLE.values()) | {'.toml'}
DOCUMENT_INPUTS = set(DOCUMENT.values()) | {'.htm', '.md', '.log', '.pdf'}
QUALITY_FORMATS = {'JPG', 'WebP', 'AVIF'}


class Cancelled(Exception):
    """The user stopped the current job."""


def check_stop(stop):
    if stop.is_set():
        raise Cancelled


def choices(path):
    suffix = Path(path).suffix.lower()
    options = []
    if suffix in IMAGE_INPUTS:
        from PIL import features
        options.extend(name for name in IMAGES
                       if name not in {'WebP', 'AVIF'} or features.check(name.lower()))
    if suffix in MEDIA_INPUTS:
        options.extend(AUDIO)
    if suffix in VIDEO_INPUTS:
        options.extend(VIDEO)
    if suffix in TABLE_INPUTS:
        options.extend(name for name, extension in TABLE.items() if extension != suffix)
    if suffix in DOCUMENT_INPUTS:
        options.extend(name for name, extension in DOCUMENT.items() if extension != suffix)
    return tuple(options) + tuple(ARCHIVES)


def convert_image(source, output, fmt, quality, stop):
    from PIL import Image, ImageOps

    if source.stat().st_size > 256 * 1024 * 1024:
        raise ValueError('Исходное изображение больше 256 МиБ')
    with Image.open(source) as image:
        if image.width * image.height > 40_000_000:
            raise ValueError('Изображение больше 40 мегапикселей')
        if getattr(image, 'is_animated', False):
            raise ValueError('У изображения несколько кадров или страниц')
        image.load()
        check_stop(stop)
        ImageOps.exif_transpose(image, in_place=True)
        if image.mode.startswith('I;16'):
            if fmt == 'PNG':
                result = image if image.mode == 'I;16' else image.convert('I').convert('I;16')
            elif fmt == 'TIFF':
                result = image
            else:
                grayscale = image.convert('I').point(lambda value: value / 257).convert('L')
                result = grayscale.convert('RGB')
        elif fmt in ('JPG', 'BMP'):
            if image.has_transparency_data:
                rgba = image.convert('RGBA')
                result = Image.new('RGB', image.size, 'white')
                result.paste(rgba, mask=rgba.getchannel('A'))
            else:
                result = image if image.mode == 'RGB' else image.convert('RGB')
        else:
            mode = 'RGBA' if image.has_transparency_data else 'RGB'
            result = image if image.mode == mode else image.convert(mode)
        with (result if result is not image else nullcontext(image)) as rendered:
            options = {'quality': quality} if fmt in QUALITY_FORMATS else {}
            if fmt == 'PNG':
                options['compress_level'] = 3
            elif fmt == 'WebP':
                options['method'] = 2
            elif fmt == 'AVIF':
                options.update(speed=8, max_threads=2)
            elif fmt == 'ICO':
                rendered.thumbnail((256, 256))
                options['sizes'] = [rendered.size]
            if 'icc_profile' in image.info and fmt in ('PNG', 'JPG', 'WebP', 'AVIF', 'TIFF'):
                if image.mode in ('RGB', 'RGBA', 'P'):
                    options['icc_profile'] = image.info['icc_profile']
            rendered.info.clear()
            check_stop(stop)
            rendered.save(output, format=IMAGES[fmt][1], **options)


def copy_bytes(source, output, stop):
    while chunk := source.read(1024 * 1024):
        check_stop(stop)
        output.write(chunk)


def compress(source, output, fmt, stop):
    with source.open('rb') as original:
        if fmt == 'ZIP':
            with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED,
                                 compresslevel=3) as archive:
                with archive.open(source.name, 'w', force_zip64=True) as entry:
                    copy_bytes(original, entry, stop)
        else:
            level = {'GZ': {'compresslevel': 3}, 'BZ2': {'compresslevel': 5},
                     'XZ': {'preset': 3}}[fmt]
            # Omit the random temporary basename from the gzip header.
            if fmt == 'GZ':
                with output.open('wb') as stream:
                    with gzip.GzipFile(filename='', mode='wb', fileobj=stream, **level) as encoded:
                        copy_bytes(original, encoded, stop)
            else:
                with COMPRESS[fmt](output, 'wb', **level) as encoded:
                    copy_bytes(original, encoded, stop)


def convert(source, folder, output_format, quality, stop):
    source, folder = Path(source).absolute(), Path(folder).absolute()
    if not 1 <= quality <= 100 or output_format not in choices(source):
        raise ValueError('Выбранный формат недоступен для этого файла')
    check_stop(stop)
    with tempfile.TemporaryDirectory(prefix='.photoconvert-', dir=folder) as temp:
        encoded = Path(temp) / ('image' + EXTENSIONS[output_format])
        if output_format in ARCHIVES:
            compress(source, encoded, output_format, stop)
        elif output_format in IMAGES:
            convert_image(source, encoded, output_format, quality, stop)
        elif output_format in AUDIO or output_format in VIDEO:
            convert_media(source, encoded, output_format, lambda: check_stop(stop))
        elif output_format in TABLE:
            convert_table(source, encoded, output_format, lambda: check_stop(stop))
        else:
            convert_document(source, encoded, output_format, lambda: check_stop(stop))

        stem = source.name if output_format in ARCHIVES else source.stem
        for number in range(10000):
            check_stop(stop)
            suffix = f' ({number})' if number else ''
            destination = folder / f'{stem}{suffix}{EXTENSIONS[output_format]}'
            try:
                # Windows rename does not replace an existing destination.
                os.rename(encoded, destination)
            except FileExistsError:
                continue
            return destination
        raise OSError('Не удалось подобрать свободное имя файла')
