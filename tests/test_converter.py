import hashlib
from pathlib import Path
import struct
import tempfile
import threading
import unittest
import zlib

from photoconvert.converter import Cancelled, convert


def rgba_png(width=16, height=16):
    def chunk(kind, data):
        return (struct.pack('>I', len(data)) + kind + data +
                struct.pack('>I', zlib.crc32(kind + data)))

    return (b'\x89PNG\r\n\x1a\n' +
            chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress((b'\0' + bytes((255, 0, 0, 0)) * width) * height)) +
            chunk(b'IEND', b''))


class ConverterTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.source = self.folder / "Тест 'Фото'.png"
        self.source.write_bytes(rgba_png())
        self.stop = threading.Event()

    def test_real_codecs_and_existing_file(self):
        digest = hashlib.sha256(self.source.read_bytes()).digest()
        png = convert(self.source, self.folder, 'PNG', 90, self.stop)
        self.assertTrue(png.name.endswith(' (1).png'))
        jpg = convert(png, self.folder, 'JPG', 92, self.stop)
        self.assertEqual(jpg.read_bytes()[:2], b'\xff\xd8')
        bmp = convert(jpg, self.folder, 'BMP', 90, self.stop)
        self.assertEqual(bmp.read_bytes()[:2], b'BM')
        # The fully transparent PNG must become white before JPEG encoding.
        self.assertTrue(all(channel > 240 for channel in bmp.read_bytes()[54:57]))
        roundtrip = convert(bmp, self.folder, 'PNG', 90, self.stop)
        self.assertEqual(roundtrip.read_bytes()[:8], b'\x89PNG\r\n\x1a\n')
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).digest(), digest)
        self.assertEqual(len(list(self.folder.iterdir())), 5)

    def test_jpg_exif_rotation(self):
        self.source.write_bytes(rgba_png(20, 12))
        jpg = convert(self.source, self.folder, 'JPG', 90, self.stop)
        tiff = (b'II' + struct.pack('<HIH', 42, 8, 1) +
                struct.pack('<HHIHHI', 274, 3, 1, 6, 0, 0))
        exif = b'Exif\0\0' + tiff
        raw = jpg.read_bytes()
        jpg.write_bytes(raw[:2] + b'\xff\xe1' + struct.pack('>H', len(exif) + 2) + exif + raw[2:])
        png = convert(jpg, self.folder, 'PNG', 90, self.stop)
        self.assertEqual(struct.unpack('>II', png.read_bytes()[16:24]), (12, 20))

    def test_invalid_source_and_cancel_create_no_output(self):
        self.source.write_bytes(b'not a png')
        with self.assertRaises(OSError):
            convert(self.source, self.folder, 'JPG', 90, self.stop)
        self.stop.set()
        with self.assertRaises(Cancelled):
            convert(self.source, self.folder, 'PNG', 90, self.stop)
        self.assertEqual(list(self.folder.iterdir()), [self.source])

    def test_16_bit_png_preserves_levels_or_scales_for_8_bit(self):
        from PIL import Image

        with Image.new('I;16', (2, 1)) as image:
            image.putdata([1024, 50000])
            image.save(self.source)
        png = convert(self.source, self.folder, 'PNG', 90, self.stop)
        with Image.open(png) as result:
            self.assertEqual(result.getpixel((0, 0)), 1024)
            self.assertEqual(result.getpixel((1, 0)), 50000)
        bmp = convert(self.source, self.folder, 'BMP', 90, self.stop)
        with Image.open(bmp) as result:
            self.assertEqual(result.getpixel((0, 0)), (3, 3, 3))
            self.assertEqual(result.getpixel((1, 0)), (194, 194, 194))
