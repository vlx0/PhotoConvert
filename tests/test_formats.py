import gzip
import json
from pathlib import Path
import tempfile
import threading
import unittest
import wave
import zipfile

from photoconvert.converter import convert


class FormatsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.stop = threading.Event()

    def convert(self, source, fmt):
        return convert(source, self.root, fmt, 90, self.stop)

    def test_audio_outputs_can_be_decoded(self):
        import av

        source = self.root / 'audio.wav'
        with wave.open(str(source), 'wb') as file:
            file.setparams((1, 2, 44100, 0, 'NONE', 'not compressed'))
            file.writeframes(b'\0\0' * 44100)
        for fmt in ('WAV', 'FLAC', 'MP3', 'OGG', 'M4A'):
            with self.subTest(fmt=fmt):
                output = self.convert(source, fmt)
                with av.open(str(output)) as audio:
                    seconds = sum(frame.samples / frame.sample_rate for frame in audio.decode(audio=0))
                    self.assertAlmostEqual(seconds, 1, delta=.05)

    def test_video_remux_keeps_all_frames(self):
        import av

        source = self.root / 'video.mp4'
        with av.open(str(source), 'w') as video:
            stream = video.add_stream('libx264', rate=24)
            stream.width, stream.height, stream.pix_fmt = 16, 16, 'yuv420p'
            for _ in range(5):
                frame = av.VideoFrame(16, 16, 'yuv420p')
                for plane in frame.planes:
                    plane.update(bytes(plane.buffer_size))
                video.mux(stream.encode(frame))
            video.mux(stream.encode(None))
        for fmt in ('MKV', 'MP4'):
            result = self.convert(source, fmt)
            with av.open(str(result)) as video:
                self.assertEqual(sum(1 for _ in video.decode(video=0)), 5)

    def test_table_values_and_formula_like_text(self):
        from openpyxl import load_workbook

        source = self.root / 'table.csv'
        source.write_text('Name,Value\n"one,two",=1+1\n', encoding='utf-8')
        rows = [['Name', 'Value'], ['one,two', '=1+1']]
        self.assertEqual(json.loads(self.convert(source, 'JSON').read_text(encoding='utf-8')), rows)
        result = self.convert(source, 'XLSX')
        book = load_workbook(result, read_only=True)
        try:
            self.assertEqual(book.active['B2'].data_type, 's')
            self.assertEqual([list(row) for row in book.active.values], rows)
        finally:
            book.close()
        self.assertIn('"one,two",=1+1', self.convert(result, 'CSV').read_text(encoding='utf-8-sig'))

    def test_document_and_binary_compression(self):
        source = self.root / 'text.txt'
        content = 'Текст <тег>'
        source.write_text(content, encoding='utf-8')
        docx = self.convert(source, 'DOCX')
        self.assertEqual(self.convert(docx, 'TXT').read_text(encoding='utf-8'), content)
        self.assertIn('&lt;тег&gt;', self.convert(source, 'HTML').read_text(encoding='utf-8'))
        binary = self.root / 'data.bin'
        binary.write_bytes(bytes(range(256)))
        with gzip.open(self.convert(binary, 'GZ'), 'rb') as stream:
            self.assertEqual(stream.read(), binary.read_bytes())
        with zipfile.ZipFile(self.convert(binary, 'ZIP')) as archive:
            self.assertEqual(archive.read(binary.name), binary.read_bytes())
